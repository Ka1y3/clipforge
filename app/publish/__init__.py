import logging, time
from .. import db, jobs, secure
import json
from . import youtube, tiktok, unavailable

log = logging.getLogger("publish")
PLATFORMS = {"youtube": youtube, "tiktok": tiktok, "instagram": unavailable}

def platform_status():
    return {"youtube": {"implemented": True, "configured": youtube.configured(), "connected": youtube.connected()},
            "tiktok": {"implemented": True, "configured": tiktok.configured(), "connected": tiktok.connected()},
            "instagram": {"implemented": False, "configured": False, "connected": False}}

@jobs.handler("publish")
def publish_job(ctx, publication_id, **_):
    p = db.one("SELECT * FROM publications WHERE id=?", (publication_id,))
    clip = db.one("SELECT * FROM clips WHERE id=?", (p["clip_id"],))
    db.update("publications", publication_id, status="publishing", error=None)
    ctx.progress(0.1, "Envoi…")
    res = PLATFORMS[p["platform"]].publish(clip, clip["title"] or "Clip", clip["description"], clip["hashtags"],
                                           json.loads(p.get("options") or "{}"))
    ext_id, url, note = (tuple(res) + ("",))[:3]
    db.update("publications", publication_id, status="published", external_id=ext_id, external_url=url or None, error=note or None)

@jobs.handler("publish:fail")
def publish_fail(publication_id, error="", **_):
    db.update("publications", publication_id, status="error", error=error[:400])

def enqueue_due():
    for p in db.q("SELECT id FROM publications WHERE status='scheduled' AND (scheduled_at IS NULL OR scheduled_at<=?)", (time.time(),)):
        db.update("publications", p["id"], status="queued")
        jobs.enqueue("publish", publication_id=p["id"])
