"""Accès média : audio de VOD (analyse) et sections vidéo (clips) — téléchargement à la demande
pour économiser stockage et bande passante. Les fichiers locaux sont utilisés directement."""
import hashlib, logging, os, subprocess, sys
from pathlib import Path
from . import config

log = logging.getLogger("media")
CACHE = config.DATA / "cache"

def is_local(vod): return os.path.isfile(vod["url"] or "")

def _ytdlp(args):
    cmd = [sys.executable, "-m", "yt_dlp", "--no-progress", "--no-warnings", *args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        err = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["erreur yt-dlp"]
        raise RuntimeError(f"yt-dlp: {err[0]} (VOD réservée aux abonnés ? Ajoutez un cookies.txt dans data/)")

def _cookies():
    c = config.DATA / "cookies.txt"
    return ["--cookies", str(c)] if c.exists() else []

def vod_audio(vod):
    """Chemin d'un média contenant l'audio complet (audio-only pour Twitch : léger)."""
    if is_local(vod): return vod["url"]
    out = CACHE / f"vod_{vod['twitch_id']}_audio.m4a"
    if out.exists(): return str(out)
    tmp = CACHE / f"vod_{vod['twitch_id']}_audio.part.m4a"
    _ytdlp(["-f", "audio_only/worst", "-o", str(tmp), "--force-overwrites", *_cookies(), vod["url"]])
    tmp.rename(out)
    return str(out)

def section(vod, start, end, height=1080):
    """Portion [start,end] de la VOD en vidéo, téléchargée uniquement pour cette portion."""
    if is_local(vod): return vod["url"], 0.0                 # (fichier, temps VOD correspondant à t=0 du fichier)
    key = hashlib.md5(f"{vod['twitch_id']}-{start:.1f}-{end:.1f}-{height}".encode()).hexdigest()[:12]
    out = CACHE / f"sec_{vod['twitch_id']}_{key}.mp4"
    if not out.exists():
        _ytdlp(["-f", f"best[height<={height}]/best", "--download-sections", f"*{start:.1f}-{end:.1f}",
                "--force-keyframes-at-cuts", "-o", str(out), "--force-overwrites", *_cookies(), vod["url"]])
    return str(out), float(start)

def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0:s=x", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split("x")[:2]
    return int(w), int(h)

def audio_stream_count(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.split()
    return len(out)

def purge_cache(max_gb=20):
    """Supprime les plus anciens fichiers du cache au-delà de max_gb."""
    files = sorted(CACHE.glob("*"), key=lambda p: p.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    for f in files:
        if total <= max_gb * 1e9: break
        total -= f.stat().st_size; f.unlink(missing_ok=True)

def frame_jpeg(vod, t):
    """Une image JPEG de la VOD à t secondes (pour choisir la zone webcam)."""
    if is_local(vod): src, off = vod["url"], 0.0
    else: src, off = section(vod, t, t + 3, 720)
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(t - off, 0):.2f}", "-i", str(src), "-frames:v", "1",
                        "-vf", "scale=1280:-2", "-f", "mjpeg", "-"], capture_output=True)
    if r.returncode != 0 or not r.stdout: raise RuntimeError("image indisponible")
    return r.stdout
