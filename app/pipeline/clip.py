"""Rendu des clips avec FFmpeg : 9:16 / 16:9 / 1:1, recadrage intelligent, webcam, sous-titres, pistes audio."""
import json, logging, shutil, subprocess, tempfile, time
from pathlib import Path
from .. import config, db, jobs, media
from . import subtitles, transcribe, video

log = logging.getLogger("clip")
FORMATS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}

def out_size(fmt, draft=False):
    w, h = FORMATS[fmt]
    return (w // 2, h // 2) if draft else (w, h)

def norm_rect(rect, sw, sh):
    """Zone webcam en fractions (0–1) ou en pixels -> pixels pairs et bornés."""
    if not rect: return None
    x, y, w, h = (float(rect[k]) for k in ("x", "y", "w", "h"))
    if max(x, y, w, h) <= 1.0: x, y, w, h = x * sw, y * sh, w * sw, h * sh
    x = int(max(0, min(x, sw - 32))); y = int(max(0, min(y, sh - 32)))
    w = int(max(32, min(w, sw - x))); h = int(max(32, min(h, sh - y)))
    return {"x": x - x % 2, "y": y - y % 2, "w": w - w % 2, "h": h - h % 2}

LAYOUTS = ("split", "small_facecam", "circle_facecam", "blurred", "fullscreen")

def _even(v): v = int(v); return v - v % 2

def build_filter(fmt, src_w, src_h, ow, oh, crop_x=0.5, cam=None, webcam_overlay=False,
                 ass=None, n_audio=1, audio_tracks=None, layout=None, game=None):
    """Retourne (filter_complex, map_video, map_audio). Entrée 0 = source ; entrée 1 = webcam externe éventuelle.
    cam / game : zones en pixels de la source {x,y,w,h}. layout : mises en page 9:16 (voir LAYOUTS)."""
    def cropx(cw): return int(max(0, min(src_w - cw, crop_x * src_w - cw / 2)))
    def crop_str(r): return f"crop={r['w']}:{r['h']}:{r['x']}:{r['y']}"
    def game_area(tw, th):
        """Chaîne produisant une zone tw x th à partir de la zone Gameplay (ou d'un recadrage automatique)."""
        if game:
            return f"{crop_str(game)},scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th}"
        cw, ch = int(src_h * tw / th), src_h
        if cw > src_w: cw, ch = src_w, int(src_w * th / tw)
        gx = cropx(cw)
        if cam and layout in (None, "split"):          # ne pas réafficher la caméra incluse dans la source
            if cam["x"] + cam["w"] <= src_w * 0.5: gx = max(gx, min(cam["x"] + cam["w"], src_w - cw))
            elif cam["x"] >= src_w * 0.5: gx = min(gx, max(cam["x"] - cw, 0))
        return f"crop={cw}:{ch}:{gx}:{(src_h - ch) // 2},scale={tw}:{th}"
    g = []
    if fmt == "16:9":
        g.append(f"[0:v]scale={ow}:{oh}:force_original_aspect_ratio=decrease,pad={ow}:{oh}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[base]")
    elif fmt == "1:1":
        side = min(src_w, src_h)
        g.append(f"[0:v]crop={side}:{side}:{cropx(side)}:{(src_h - side) // 2},scale={ow}:{oh},setsar=1[base]")
    else:  # 9:16
        lay = layout if layout in LAYOUTS else ("split" if cam else "fullscreen")
        if lay in ("split", "small_facecam", "circle_facecam") and not cam: lay = "fullscreen"
        if lay == "split":
            wh = _even(min(ow * cam["h"] / cam["w"], oh * 0.36)); gh = oh - wh
            g += ["[0:v]split=2[a][b]",
                  f"[a]{crop_str(cam)},scale={ow}:{wh}:force_original_aspect_ratio=increase,crop={ow}:{wh}[cam]",
                  f"[b]{game_area(ow, gh)},setsar=1[game]", "[cam][game]vstack=inputs=2[base]"]
        elif lay == "small_facecam":
            cw2 = _even(ow * 0.5)
            g += ["[0:v]split=2[a][b]", f"[b]{game_area(ow, oh)},setsar=1[game]",
                  f"[a]{crop_str(cam)},scale={cw2}:-2[cam]", f"[game][cam]overlay=(W-w)/2:{int(oh * 0.05)}[base]"]
        elif lay == "circle_facecam":
            side = min(cam["w"], cam["h"]); d = _even(ow * 0.42)
            g += ["[0:v]split=2[a][b]", f"[b]{game_area(ow, oh)},setsar=1[game]",
                  f"[a]crop={side}:{side}:{cam['x'] + (cam['w'] - side) // 2}:{cam['y'] + (cam['h'] - side) // 2},scale={d}:{d},format=yuva420p,"
                  f"geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='if(lt(hypot(X-W/2,Y-H/2),W/2),255,0)'[cam]",
                  f"[game][cam]overlay=(W-w)/2:{int(oh * 0.06)}[base]"]
        elif lay == "blurred":
            fg = (crop_str(game) + ",") if game else ""
            g += ["[0:v]split=2[a][b]", f"[a]{game_area(ow, oh)},boxblur=24:6[bg]",
                  f"[b]{fg}scale={ow}:-2[fg]", "[bg][fg]overlay=0:(H-h)/2[base]"]
        else:  # fullscreen
            g.append(f"[0:v]{game_area(ow, oh)},setsar=1[base]")
    last = "base"
    if webcam_overlay:  # webcam externe (2e piste vidéo) en incrustation
        pw = int(ow * (0.5 if fmt == "9:16" else 0.25))
        g.append(f"[1:v]scale={pw}:-2[pip]")
        g.append(f"[{last}][pip]overlay=W-w-{int(ow * 0.03)}:{'H*0.08' if fmt == '9:16' else 'H-h-' + str(int(oh * 0.05))}[ovl]")
        last = "ovl"
    g.append(f"[{last}]ass={ass}[outv]" if ass else f"[{last}]null[outv]")
    tracks = [t for t in (audio_tracks or []) if 0 <= t < n_audio]
    if len(tracks) > 1:
        g.append("".join(f"[0:a:{t}]" for t in tracks) + f"amix=inputs={len(tracks)}:normalize=0[outa]")
        amap = "[outa]"
    else:
        amap = f"0:a:{tracks[0] if tracks else 0}?"
    return ";".join(g), "[outv]", amap

def render_file(src, offset, start, end, out, fmt="9:16", options=None, words=None, draft=False):
    """Rend [start,end] (temps VOD) ; src est la source, offset = temps VOD du début de src."""
    o = options or {}
    ow, oh = out_size(fmt, draft)
    sw, sh = media.probe(src)
    tmp = Path(tempfile.mkdtemp(dir=config.DATA / "tmp"))
    try:
        crop_x = o.get("crop_x")
        if crop_x in (None, "auto"):
            try: crop_x = video.smart_crop_x(src, start - offset, end - start)
            except Exception: crop_x = 0.5
        ass_name = None
        if words and o.get("subtitles", True):
            (tmp / "sub.ass").write_text(subtitles.build_ass(words, ow, oh, o.get("subtitle_style", "bold"), offset=start,
                                                             overrides=o.get("subtitle_overrides")), encoding="utf-8")
            ass_name = "sub.ass"
        cam_file = o.get("webcam_file") if o.get("webcam_file") and Path(o["webcam_file"]).is_file() else None
        fc, mv, ma = build_filter(fmt, sw, sh, ow, oh, crop_x, norm_rect(o.get("webcam_rect"), sw, sh), bool(cam_file), ass_name,
                                  media.audio_stream_count(src) or 1, o.get("audio_tracks"),
                                  o.get("layout"), norm_rect(o.get("game_rect"), sw, sh))
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{max(start - offset, 0):.3f}", "-t", f"{end - start:.3f}", "-i", str(src)]
        if cam_file:
            cmd += ["-ss", f"{o.get('webcam_offset', 0) + max(start - offset, 0):.3f}", "-t", f"{end - start:.3f}", "-i", cam_file]
        cmd += ["-filter_complex", fc, "-map", mv, "-map", ma, "-c:v", "libx264", "-preset", "veryfast" if draft else "medium",
                "-crf", "30" if draft else str(o.get("crf", 18)), "-pix_fmt", "yuv420p", "-r", "30",
                "-c:a", "aac", "-b:a", "96k" if draft else "192k", "-movflags", "+faststart", "-shortest", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp)
        if r.returncode != 0: raise RuntimeError("ffmpeg: " + r.stderr.strip()[-400:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return crop_x

def get_words(vod, start, end, st):
    if not transcribe.available(): return None
    segs = transcribe.transcribe(media.vod_audio(vod), start, end, st["whisper_model"])
    return [w for s in segs for w in s["words"]]

def drop_previews(mid=None, vod_id=None):
    """Supprime les aperçus (fichiers + lignes) d'un moment ou de toute une VOD."""
    rows = db.q("SELECT id,path FROM clips WHERE draft=1 AND " + ("moment_id=?" if mid else "vod_id=?"), (mid or vod_id,))
    for r in rows:
        if r["path"]:
            Path(r["path"]).unlink(missing_ok=True); Path(r["path"]).with_suffix(".jpg").unlink(missing_ok=True)
        db.run("DELETE FROM clips WHERE id=?", (r["id"],))

def queue_preview(mid):
    """Crée l'aperçu 9:16 (webcam en haut, jeu en bas si une zone webcam est définie) d'un moment."""
    m = db.one("SELECT * FROM moments WHERE id=?", (mid,))
    drop_previews(mid)
    cid = db.run("INSERT INTO clips(moment_id,vod_id,fmt,options,status,title,draft,created) VALUES(?,?,?,?,?,?,1,?)",
                 (mid, m["vod_id"], "9:16", m.get("edit") or "{}", "queued", m["title"] or m["label"], time.time()))
    jobs.enqueue("render", clip_id=cid, draft=True)
    return cid

@jobs.handler("render")
def render_job(ctx, clip_id, draft=False, **_):
    clip = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    m = db.one("SELECT * FROM moments WHERE id=?", (clip["moment_id"],))
    vod = db.one("SELECT * FROM vods WHERE id=?", (clip["vod_id"],))
    o = json.loads(clip["options"] or "{}"); st = db.get_settings()
    start, end = float(o.get("start", m["start"])), float(o.get("end", m["end"]))
    o.setdefault("subtitle_style", st["subtitle_style"])
    if clip["fmt"] == "9:16":
        for k in ("webcam_rect", "game_rect", "layout"):
            if k not in o and st.get(k): o[k] = st[k]
    db.update("clips", clip_id, status="rendering")
    ctx.progress(0.1, "Récupération de la vidéo…")
    src, off = media.section(vod, start - 1, end + 1) if not draft else media.section(vod, start - 1, end + 1, 480)
    words = None
    if o.get("subtitles", True) and not draft:
        ctx.progress(0.4, "Sous-titres…")
        try: words = get_words(vod, start, end, st)
        except Exception as e: log.warning("sous-titres indisponibles: %s", e)
    ctx.progress(0.6, "Encodage…")
    name = f"clip_{clip_id}{'_preview' if draft else ''}.mp4"
    out = config.DATA / "clips" / name
    crop_x = render_file(src, off, start, end, out, clip["fmt"], o, words, draft)
    o["crop_x_used"] = round(float(crop_x), 3); o["subtitled"] = bool(words)
    db.update("clips", clip_id, status="preview" if draft else "done", path=str(out),
              duration=end - start, options=json.dumps(o))
    if draft:
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", "1", "-i", str(out), "-frames:v", "1", "-q:v", "5",
                        str(out.with_suffix(".jpg"))], capture_output=True)
    ctx.progress(1.0, "Terminé")

@jobs.handler("render:fail")
def render_fail(clip_id, error="", **_):
    db.update("clips", clip_id, status="error")
