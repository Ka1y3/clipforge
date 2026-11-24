"""Fusion des signaux (audio, chat, texte, vidéo, IA) -> moments classés."""
import re
import numpy as np
from scipy.ndimage import median_filter, uniform_filter1d

def robust_z(x, window=121, floor=1e-3):
    """Z-score robuste par rapport à une baseline glissante (médiane/MAD)."""
    x = np.asarray(x, np.float32)
    if len(x) == 0: return x
    w = min(window | 1, (len(x) // 2) * 2 - 1) if len(x) > 3 else 1
    base = median_filter(x, size=max(w, 1), mode="nearest")
    mad = median_filter(np.abs(x - base), size=max(w, 1), mode="nearest") * 1.4826
    return (x - base) / np.maximum(mad, floor)

def squash(z, k=1.5, mid=2.0):
    """Z-score -> 0..1 (sigmoïde)."""
    return 1 / (1 + np.exp(-(np.asarray(z, np.float32) - mid) / k))

def audio_signal(rms_db, hf_ratio):
    """Volume inhabituel + montée brutale + timbre aigu (cris, rires)."""
    if len(rms_db) == 0: return np.zeros(0, np.float32)
    loud = robust_z(rms_db, 121, floor=2.0)       # plancher 2 dB : ignore les micro-variations
    prev = np.concatenate([np.full(4, rms_db[0]), rms_db[:-4]])[:len(rms_db)]
    burst = (rms_db - uniform_filter1d(prev, 4)) / 6.0           # ~ +6 dB = 1
    bright = robust_z(hf_ratio, 121, floor=0.04)
    z = 0.6 * loud + 0.8 * np.clip(burst, 0, 4) + 0.25 * np.clip(bright, 0, 6)
    return squash(z, 1.5, 2.0)

EMOTES = re.compile(r"\b(kekw|lul|lmao|lol|omegalul|pog|pogchamp|pogu|monkas|ez|gg|wtf|mdr|ptdr|xd|oh+ ?my ?god|omg)\b", re.I)

def chat_signal(messages, duration, bin_s=1):
    """messages: [(offset_s, texte)] -> 0..1 par seconde (débit de messages + emotes)."""
    n = int(duration // bin_s) + 1
    if not messages or n < 5: return None
    cnt = np.zeros(n, np.float32); emo = np.zeros(n, np.float32)
    for t, txt in messages:
        i = int(t // bin_s)
        if 0 <= i < n:
            cnt[i] += 1
            if EMOTES.search(txt or ""): emo[i] += 1
    cnt = uniform_filter1d(cnt, 5) * 5; emo = uniform_filter1d(emo, 5) * 5
    z = 0.7 * robust_z(cnt, 301, floor=1.0) + 0.5 * robust_z(emo, 301, floor=0.5)
    return squash(z, 1.5, 2.0)

LAUGH = re.compile(r"(ha){2,}|(he){2,}|(hi){2,}|\bmdr\b|\bptdr\b|\blol\b", re.I)
HYPE = re.compile(r"\b(oh+ ?(mon )?dieu|putain|wesh|waouh|wow|non+ ?non+|c'est pas vrai|incroyable|impossible|let'?s go|gagn[ée]|on a gagn|omg|wtf|no way|what the|holy|clutch|ace|insane|fuck|merde|nan+ ?mais|ouais+|yes+)\b", re.I)

def text_score(text):
    """Intérêt d'une transcription (0..1) : rires, exclamations, interjections, débit."""
    if not text or not text.strip(): return 0.0
    words = text.split()
    s = 0.0
    s += min(len(LAUGH.findall(text)), 4) * 0.18
    s += min(len(HYPE.findall(text)), 4) * 0.15
    s += min(text.count("!"), 5) * 0.05
    s += min(len(words) / 80.0, 1.0) * 0.15          # beaucoup de parole = moment actif
    return float(min(s, 1.0))

def combine(signals, weights):
    """signals: {nom: array|None}. Les signaux absents sont ignorés (poids renormalisés)."""
    avail = {k: v for k, v in signals.items() if v is not None and len(v)}
    if not avail: return np.zeros(0, np.float32)
    n = min(len(v) for v in avail.values())
    tw = sum(weights[k] for k in avail)
    fused = sum(weights[k] * np.asarray(v[:n], np.float32) for k, v in avail.items()) / tw
    # bonus de convergence : plusieurs signaux forts en même temps
    strong = sum((np.asarray(v[:n]) > 0.6).astype(np.float32) for v in avail.values())
    fused = fused + 0.08 * np.maximum(strong - 1, 0)
    return uniform_filter1d(fused, 5)

def pick_peaks(fused, n, min_gap=45, floor=0.25):
    """Meilleurs pics espacés d'au moins min_gap secondes."""
    f = np.asarray(fused).copy()
    peaks = []
    while len(peaks) < n and len(f):
        i = int(np.argmax(f))
        if f[i] < floor: break
        peaks.append((i, float(f[i])))
        f[max(0, i - min_gap):i + min_gap + 1] = -1
    return peaks

def snap_boundaries(peak, rms_db, vod_duration, lead=12, tail=10, min_len=15, max_len=60, snap=4):
    """Début/fin autour du pic, calés sur les micro-pauses (pas de coupure en plein mot)."""
    start = max(0, peak - lead); end = min(vod_duration, peak + tail)
    def quietest(t, lo, hi):
        a = int(max(lo, t - snap)); b = int(min(hi, t + snap)) + 1
        if b <= a or len(rms_db) == 0: return t
        b = min(b, len(rms_db)); a = min(a, b - 1)
        return a + int(np.argmin(rms_db[a:b]))
    if len(rms_db):
        start = quietest(start, 0, peak - 3)
        end = quietest(end, peak + 3, vod_duration)
    if end - start < min_len:
        extra = min_len - (end - start); start = max(0, start - extra * 0.6); end = min(vod_duration, end + extra * 0.4)
    if end - start > max_len:
        start = max(start, peak - max_len * 0.6); end = start + max_len
    return float(start), float(min(end, vod_duration))

def final_score(base, text=0.0, video=0.0, llm=None):
    """Score 0–100. Le LLM, s'il est présent, pèse fort car il juge le contenu."""
    s = 0.62 * base + 0.14 * text + 0.09 * video
    if llm is not None:
        s = 0.5 * s / 0.85 + 0.5 * (llm / 10.0)
    else:
        s = s / 0.85
    return round(float(min(max(s, 0), 1)) * 100, 1)
