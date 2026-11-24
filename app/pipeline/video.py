"""Analyse vidéo légère : mouvement / changements de scène + centre d'action (recadrage 9:16)."""
import subprocess
import numpy as np

W, H, FPS = 64, 36, 4

def _frames(src, start=None, dur=None):
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start is not None: cmd += ["-ss", str(start)]
    if dur is not None: cmd += ["-t", str(dur)]
    cmd += ["-i", str(src), "-an", "-vf", f"fps={FPS},scale={W}:{H},format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // (W * H)
    return np.frombuffer(raw[:n * W * H], np.uint8).reshape(n, H, W).astype(np.float32)

def motion_profile(src, start=None, dur=None):
    """Renvoie (motion[s], centre_x[s] en 0..1). motion = variation moyenne entre images."""
    fr = _frames(src, start, dur)
    if len(fr) < 2: return np.zeros(0, np.float32), np.zeros(0, np.float32)
    diff = np.abs(np.diff(fr, axis=0))
    per_frame = diff.mean(axis=(1, 2))
    col = diff.sum(axis=1) + 1e-6                          # (n-1, W)
    cx = (col * np.arange(W)).sum(axis=1) / col.sum(axis=1) / (W - 1)
    k = FPS; n = len(per_frame) // k
    if n == 0: return np.zeros(0, np.float32), np.zeros(0, np.float32)
    motion = per_frame[:n * k].reshape(n, k).mean(axis=1)
    w = col[:n * k].reshape(n, k, W).sum(axis=1)
    cxs = (w * np.arange(W)).sum(axis=1) / w.sum(axis=1) / (W - 1)
    return motion.astype(np.float32), cxs.astype(np.float32)

def video_score(motion):
    """0..1 : intensité d'action relative (pic de mouvement vs médiane)."""
    if len(motion) == 0: return 0.0
    med = float(np.median(motion)) + 1e-3
    return float(np.clip((motion.max() / med - 1.5) / 6.0, 0, 1))

def smart_crop_x(src, start=None, dur=None):
    """Position horizontale (0..1) du centre d'action, pondérée par le mouvement."""
    motion, cx = motion_profile(src, start, dur)
    if len(motion) == 0: return 0.5
    w = motion + 1e-3
    return float(np.clip((cx * w).sum() / w.sum(), 0.0, 1.0))
