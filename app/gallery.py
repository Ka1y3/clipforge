"""Galerie des moments : statut d'affichage (Nouveau / Brouillon / Exporté / Programmé / Masqué) + aperçu."""
import json
from . import db

TABS = ["all", "new", "draft", "exported", "scheduled", "hidden"]

def build(status="all", vod_id=None, sort="score"):
    where, args = "", ()
    if vod_id: where, args = " WHERE m.vod_id=?", (vod_id,)
    moments = db.q("SELECT m.*, v.title vod_title, v.created_at vod_date FROM moments m JOIN vods v ON v.id=m.vod_id" + where, args)
    clips = db.q("SELECT id,moment_id,status,draft,created FROM clips WHERE moment_id IS NOT NULL")
    pubs = db.q("SELECT p.status, c.moment_id FROM publications p JOIN clips c ON c.id=p.clip_id")
    previews, exported, scheduled = {}, set(), set()
    for c in sorted(clips, key=lambda c: c["created"] or 0):
        if c["draft"]: previews[c["moment_id"]] = {"id": c["id"], "status": c["status"]}
        elif c["status"] == "done": exported.add(c["moment_id"])
    for p in pubs:
        if p["status"] in ("scheduled", "queued", "publishing"): scheduled.add(p["moment_id"])
    items, counts = [], {t: 0 for t in TABS}
    for m in moments:
        tab = ("hidden" if m["status"] == "hidden" else "scheduled" if m["id"] in scheduled else
               "exported" if m["id"] in exported else "draft" if m["status"] == "draft" else "new")
        counts["all"] += 1; counts[tab] += 1
        if status not in ("all", tab): continue
        m["features"] = json.loads(m["features"] or "{}")
        m["tab"] = tab; m["duration"] = m["end"] - m["start"]; m["preview"] = previews.get(m["id"])
        items.append(m)
    key = {"score": lambda m: -m["score"], "recent": lambda m: (m["vod_date"] or "", -m["start"]),
           "duration": lambda m: -m["duration"]}.get(sort, lambda m: -m["score"])
    items.sort(key=key, reverse=(sort == "recent"))
    return {"items": items, "counts": counts}
