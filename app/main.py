import json, logging, shutil, threading, time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from . import config, db, gallery, jobs, log as logmod, media, twitch, chat_recorder, publish
from .pipeline import analyzer, clip as clipmod, transcribe, llm  # noqa: enregistre les handlers

log = logging.getLogger("app")
_stop = threading.Event()

def scheduler():
    last_sync = 0
    while not _stop.wait(30):
        try:
            publish.enqueue_due()
            st = db.get_settings()
            if st["auto_sync"] and twitch.configured() and twitch.account() and time.time() - last_sync > st["sync_minutes"] * 60:
                last_sync = time.time(); twitch.sync_vods(max_pages=1)
                if st["auto_analyze"]:
                    for v in db.q("SELECT id,created_at FROM vods WHERE status='new'"):
                        age = time.time() - datetime.fromisoformat(v["created_at"].replace("Z", "+00:00")).timestamp()
                        if age < 7 * 86400: db.update("vods", v["id"], status="queued"); jobs.enqueue("analyze", vod_id=v["id"])
        except Exception:
            log.exception("scheduler")

@asynccontextmanager
async def lifespan(app):
    logmod.setup(); db.init(); jobs.start()
    threading.Thread(target=scheduler, daemon=True, name="scheduler").start()
    yield
    _stop.set(); jobs.stop()

app = FastAPI(title="ClipForge", lifespan=lifespan)

# ---------- état / auth ----------
@app.get("/api/status")
def status():
    acc = twitch.account()
    return {"ffmpeg": bool(shutil.which("ffmpeg")), "whisper": transcribe.available(), "llm": llm.available(),
            "twitch": {"configured": twitch.configured(), "connected": bool(acc), "user": acc["user"] if acc else None},
            "platforms": publish.platform_status(),
            "chat_files": [p.stem for p in (config.DATA / "chat").glob("*.jsonl")],
            "counts": {"vods": db.one("SELECT COUNT(*) n FROM vods")["n"], "moments": db.one("SELECT COUNT(*) n FROM moments")["n"],
                       "clips": db.one("SELECT COUNT(*) n FROM clips WHERE status='done'")["n"],
                       "published": db.one("SELECT COUNT(*) n FROM publications WHERE status='published'")["n"]}}

@app.get("/auth/twitch/login")
def twitch_login():
    if not twitch.configured(): raise HTTPException(400, "TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET manquants dans .env")
    return RedirectResponse(twitch.login_url())

@app.get("/auth/twitch/callback")
def twitch_cb(code: str = "", state: str = "", error: str = ""):
    if error or not code: raise HTTPException(400, error or "code manquant")
    try: twitch.handle_callback(code, state)
    except ValueError as e: raise HTTPException(400, str(e))
    return RedirectResponse("/#/settings")

@app.post("/auth/twitch/logout")
def twitch_logout():
    from . import secure; secure.delete_account("twitch"); return {"ok": True}

@app.post("/auth/{provider}/logout")
def provider_logout(provider: str):
    if provider not in ("twitch", "youtube", "tiktok"): raise HTTPException(404)
    from . import secure; secure.delete_account(provider); return {"ok": True}

@app.get("/auth/google/login")
def google_login():
    if not publish.youtube.configured(): raise HTTPException(400, "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET manquants dans .env")
    return RedirectResponse(publish.youtube.login_url())

@app.get("/auth/google/callback")
def google_cb(code: str = "", state: str = "", error: str = ""):
    if error or not code: raise HTTPException(400, error or "code manquant")
    try: publish.youtube.handle_callback(code, state)
    except ValueError as e: raise HTTPException(400, str(e))
    return RedirectResponse("/#/settings")

@app.get("/auth/tiktok/login")
def tiktok_login():
    if not publish.tiktok.configured(): raise HTTPException(400, "TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET manquants dans .env")
    return RedirectResponse(publish.tiktok.login_url())

@app.get("/auth/tiktok/callback")
def tiktok_cb(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error or not code: raise HTTPException(400, error_description or error or "code manquant")
    try: publish.tiktok.handle_callback(code, state)
    except (ValueError, Exception) as e: raise HTTPException(400, str(e))
    return RedirectResponse("/#/settings")

@app.get("/api/tiktok/creator")
def tiktok_creator():
    try: return publish.tiktok.creator_info()
    except Exception as e: raise HTTPException(400, str(e))

# ---------- VOD ----------
@app.get("/api/vods")
def vods():
    return db.q("""SELECT v.*, (SELECT COUNT(*) FROM moments m WHERE m.vod_id=v.id) moments
                   FROM vods v ORDER BY v.created_at DESC""")

@app.post("/api/vods/sync")
def vods_sync():
    try: return {"new": twitch.sync_vods()}
    except Exception as e: raise HTTPException(400, str(e))

class AddVod(BaseModel):
    url: str

@app.post("/api/vods")
def add_vod(b: AddVod):
    try:
        u = b.url.strip()
        return twitch.add_by_url(u) if "twitch.tv" in u or u.isdigit() else twitch.add_local(u)
    except Exception as e: raise HTTPException(400, str(e))

@app.post("/api/vods/{vid}/analyze")
def analyze_vod(vid: int):
    v = db.one("SELECT * FROM vods WHERE id=?", (vid,))
    if not v: raise HTTPException(404)
    if v["status"] in ("queued", "analyzing"): raise HTTPException(409, "analyse déjà en cours")
    db.update("vods", vid, status="queued", error=None)
    return {"job": jobs.enqueue("analyze", vod_id=vid)}

@app.get("/api/vods/{vid}/moments")
def vod_moments(vid: int):
    ms = db.q("SELECT * FROM moments WHERE vod_id=? ORDER BY score DESC", (vid,))
    for m in ms: m["features"] = json.loads(m["features"] or "{}")
    return ms

# ---------- moments / clips ----------
class MomentPatch(BaseModel):
    start: float | None = None
    end: float | None = None
    status: str | None = None
    title: str | None = None
    edit: dict | None = None

@app.get("/api/moments/{mid}")
def get_moment(mid: int):
    m = db.one("SELECT * FROM moments WHERE id=?", (mid,))
    if not m: raise HTTPException(404)
    m["features"] = json.loads(m["features"] or "{}"); m["edit"] = json.loads(m.get("edit") or "{}")
    m["vod"] = db.one("SELECT id,title,duration FROM vods WHERE id=?", (m["vod_id"],))
    return m

@app.patch("/api/moments/{mid}")
def patch_moment(mid: int, b: MomentPatch):
    m = db.one("SELECT * FROM moments WHERE id=?", (mid,))
    if not m: raise HTTPException(404)
    f = {k: v for k, v in b.model_dump().items() if v is not None}
    if "edit" in f: f["edit"] = json.dumps(f["edit"]); f.setdefault("status", "draft" if m["status"] == "new" else m["status"])
    s, e = f.get("start", m["start"]), f.get("end", m["end"])
    dur = db.one("SELECT duration FROM vods WHERE id=?", (m["vod_id"],))["duration"]
    if not (0 <= s < e <= dur + 1) or e - s < 3 or e - s > 180: raise HTTPException(400, "bornes invalides (3 s – 180 s)")
    if ("start" in f or "end" in f) and m["status"] == "new": f["status"] = "draft"
    db.update("moments", mid, **f); return get_moment(mid)

class ClipReq(BaseModel):
    fmt: str = "9:16"
    draft: bool = False
    options: dict = {}
    title: str | None = None

@app.post("/api/moments/{mid}/clips")
def create_clip(mid: int, b: ClipReq):
    m = db.one("SELECT * FROM moments WHERE id=?", (mid,))
    if not m: raise HTTPException(404)
    if b.fmt not in clipmod.FORMATS: raise HTTPException(400, "format inconnu")
    st = db.get_settings()
    if b.draft: clipmod.drop_previews(mid)
    cid = db.run("INSERT INTO clips(moment_id,vod_id,fmt,options,status,title,hashtags,draft,created) VALUES(?,?,?,?,?,?,?,?,?)",
                 (mid, m["vod_id"], b.fmt, json.dumps(b.options), "queued", b.title or m["title"] or m["label"],
                  st["default_hashtags"], 1 if b.draft else 0, time.time()))
    return {"clip": cid, "job": jobs.enqueue("render", clip_id=cid, draft=b.draft)}

@app.get("/api/gallery")
def get_gallery(status: str = "all", vod_id: int | None = None, sort: str = "score"):
    return gallery.build(status, vod_id, sort)

@app.post("/api/moments/{mid}/hide")
def hide_moment(mid: int, hidden: bool = True):
    if not db.one("SELECT id FROM moments WHERE id=?", (mid,)): raise HTTPException(404)
    db.update("moments", mid, status="hidden" if hidden else "new"); return {"ok": True}

@app.post("/api/moments/{mid}/preview")
def regen_preview(mid: int):
    if not db.one("SELECT id FROM moments WHERE id=?", (mid,)): raise HTTPException(404)
    return {"clip": clipmod.queue_preview(mid)}

@app.delete("/api/moments/{mid}")
def delete_moment(mid: int):
    clipmod.drop_previews(mid); db.run("DELETE FROM moments WHERE id=?", (mid,)); return {"ok": True}

@app.get("/api/vods/{vid}/frame")
def vod_frame(vid: int, t: float = 120):
    v = db.one("SELECT * FROM vods WHERE id=?", (vid,))
    if not v: raise HTTPException(404)
    try: return Response(media.frame_jpeg(v, min(max(t, 0), max(v["duration"] - 5, 0))), media_type="image/jpeg")
    except Exception as e: raise HTTPException(400, str(e))

@app.get("/api/clips")
def clips():
    return db.q("""SELECT c.*, m.score, m.label FROM clips c LEFT JOIN moments m ON m.id=c.moment_id
                   WHERE c.draft=0 AND c.status IN ('done','rendering','queued','error') ORDER BY c.created DESC""")

@app.get("/api/clips/{cid}")
def get_clip(cid: int):
    c = db.one("SELECT * FROM clips WHERE id=?", (cid,))
    if not c: raise HTTPException(404)
    return c

class ClipPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    hashtags: str | None = None

@app.patch("/api/clips/{cid}")
def patch_clip(cid: int, b: ClipPatch):
    db.update("clips", cid, **{k: v for k, v in b.model_dump().items() if v is not None}); return get_clip(cid)

@app.delete("/api/clips/{cid}")
def del_clip(cid: int):
    c = get_clip(cid)
    if c["path"]:
        import pathlib; pathlib.Path(c["path"]).unlink(missing_ok=True)
    db.run("DELETE FROM publications WHERE clip_id=?", (cid,)); db.run("DELETE FROM clips WHERE id=?", (cid,))
    return {"ok": True}

@app.get("/media/clips/{cid}/poster")
def clip_poster(cid: int):
    import pathlib
    c = get_clip(cid); p = pathlib.Path(c["path"] or "").with_suffix(".jpg")
    if not c["path"] or not p.is_file(): raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")

@app.get("/media/clips/{cid}")
def clip_media(cid: int, download: bool = False):
    c = get_clip(cid)
    import pathlib
    if not c["path"] or not pathlib.Path(c["path"]).is_file(): raise HTTPException(404, "pas encore rendu")
    return FileResponse(c["path"], media_type="video/mp4", filename=f"clip_{cid}.mp4" if download else None)

# ---------- publication ----------
class PubReq(BaseModel):
    clip_id: int
    platform: str
    title: str | None = None
    description: str | None = None
    hashtags: str | None = None
    scheduled_at: float | None = None      # epoch secondes ; None = maintenant
    options: dict = {}                     # TikTok : mode, privacy, disable_comment/duet/stitch

@app.post("/api/publications")
def create_pub(b: PubReq):
    c = get_clip(b.clip_id)
    if c["status"] != "done": raise HTTPException(400, "le clip n'est pas prêt")
    info = publish.platform_status().get(b.platform)
    if not info: raise HTTPException(400, "plateforme inconnue")
    if not info["implemented"]: raise HTTPException(501, f"{b.platform}: publication non implémentée")
    if not info["connected"]: raise HTTPException(400, f"{b.platform}: compte non connecté")
    db.update("clips", b.clip_id, **{k: v for k, v in {"title": b.title, "description": b.description, "hashtags": b.hashtags}.items() if v is not None})
    if b.platform == "tiktok" and b.options.get("mode", "direct") == "direct" and not b.options.get("privacy"):
        raise HTTPException(400, "TikTok : choisissez la visibilité de la publication")
    pid = db.run("INSERT INTO publications(clip_id,platform,status,scheduled_at,options,created) VALUES(?,?,?,?,?,?)",
                 (b.clip_id, b.platform, "scheduled", b.scheduled_at, json.dumps(b.options), time.time()))
    publish.enqueue_due()
    return db.one("SELECT * FROM publications WHERE id=?", (pid,))

@app.get("/api/publications")
def pubs():
    return db.q("""SELECT p.*, c.title, c.fmt FROM publications p JOIN clips c ON c.id=p.clip_id ORDER BY p.created DESC""")

@app.delete("/api/publications/{pid}")
def cancel_pub(pid: int):
    p = db.one("SELECT * FROM publications WHERE id=?", (pid,))
    if not p: raise HTTPException(404)
    if p["status"] in ("publishing", "published"): raise HTTPException(409, "déjà envoyée")
    db.run("DELETE FROM publications WHERE id=?", (pid,)); return {"ok": True}

# ---------- jobs / réglages ----------
@app.get("/api/jobs")
def list_jobs(limit: int = 30):
    return db.q("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))

@app.get("/api/settings")
def get_settings(): return db.get_settings()

@app.put("/api/settings")
def put_settings(b: dict): return db.set_settings(b)

@app.post("/api/maintenance/purge-cache")
def purge(): media.purge_cache(); return {"ok": True}

app.mount("/", StaticFiles(directory=config.ROOT / "web", html=True), name="web")
