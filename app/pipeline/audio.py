"""Analyse audio en flux (RAM constante, même pour une VOD de 10 h)."""
import subprocess
import numpy as np

SR = 16000

def _ffmpeg_pcm_cmd(src, start=None, dur=None):
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start is not None: cmd += ["-ss", str(start)]
    if dur is not None: cmd += ["-t", str(dur)]
    cmd += ["-i", str(src), "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    return cmd

def read_pcm(src, start=None, dur=None):
    out = subprocess.run(_ffmpeg_pcm_cmd(src, start, dur), capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0

def second_features(frame):
    """frame: 1 s d'échantillons -> (rms_db, ratio énergie 1.5–5 kHz / totale)."""
    rms = float(np.sqrt(np.mean(frame ** 2)) + 1e-9)
    spec = np.abs(np.fft.rfft(frame * np.hanning(len(frame)))) ** 2
    freqs = np.fft.rfftfreq(len(frame), 1 / SR)
    tot = spec.sum() + 1e-12
    hf = spec[(freqs >= 1500) & (freqs <= 5000)].sum() / tot
    return 20 * np.log10(rms), float(hf)

def features_from_samples(x):
    n = len(x) // SR
    rms = np.empty(n, np.float32); hf = np.empty(n, np.float32)
    for i in range(n):
        rms[i], hf[i] = second_features(x[i * SR:(i + 1) * SR])
    return rms, hf

def profile(src, duration=None, progress=None, chunk_s=120):
    """Lit la source par blocs et renvoie rms_db[], hf_ratio[] (1 valeur/seconde)."""
    cmd = _ffmpeg_pcm_cmd(src, None, duration)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    rms_all, hf_all, buf = [], [], b""
    step = SR * 2 * chunk_s
    try:
        while True:
            data = p.stdout.read(step)
            if not data: break
            buf += data
            usable = (len(buf) // (SR * 2)) * SR * 2
            if usable:
                x = np.frombuffer(buf[:usable], np.int16).astype(np.float32) / 32768.0
                r, h = features_from_samples(x)
                rms_all.append(r); hf_all.append(h)
                buf = buf[usable:]
                if progress and duration:
                    progress(min(1.0, sum(len(a) for a in rms_all) / duration))
    finally:
        p.stdout.close(); p.wait()
    if not rms_all:
        return np.zeros(0, np.float32), np.zeros(0, np.float32)
    return np.concatenate(rms_all), np.concatenate(hf_all)
