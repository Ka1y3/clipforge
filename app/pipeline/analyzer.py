"""Orchestration de l'analyse d'une VOD -> moments classés (sauvegardés en base)."""
import json, logging
from datetime import datetime
import numpy as np
from .. import db, media, jobs, twitch
from ..chat_recorder import load_for_vod
from . import audio, scoring, transcribe, video, llm
from .clip import queue_preview, drop_previews

log = logging.getLogger("analyzer")
WEIGHTS = {"audio": 0.5, "chat": 0.3, "video": 0.2}

@jobs.handler("analyze")
def analyze(ctx, vod_id, **_):
    vod = db.one("SELECT * FROM vods WHERE id=?", (vod_id,))
    st = db.get_settings()
    db.update("vods", vod_id, status="analyzing", error=None)
    try:
        ctx.progress(0.02, "Accès à l'audio…")
        src = media.vod_audio(vod)
        ctx.progress(0.25, "Analyse audio…")
        dur = vod["duration"]
        rms, hf = audio.profile(src, dur, progress=lambda p: ctx.progress(0.25 + 0.35 * p))
        dur = min(dur, len(rms)) or len(rms)
        a_sig = scoring.audio_signal(rms, hf)

        ctx.progress(0.62, "Analyse du chat…")
        acc = twitch.account()
        channel = vod.get("channel") or ((acc or {}).get("user") or {}).get("login")
        chat = load_for_vod(channel, vod["created_at"], dur)
        c_sig = scoring.chat_signal(chat, dur) if len(chat) > 50 else None
        if c_sig is not None and len(c_sig) < len(a_sig):
            c_sig = np.pad(c_sig, (0, len(a_sig) - len(c_sig)))

        fused = scoring.combine({"audio": a_sig, "chat": c_sig, "video": None}, WEIGHTS)
        want = int(st["max_moments"])
        peaks = scoring.pick_peaks(fused, int(want * 2.5), min_gap=max(30, int(st["clip_min"]) * 2))
        if not peaks:
            drop_previews(vod_id=vod_id)
            db.run("DELETE FROM moments WHERE vod_id=?", (vod_id,))
            db.update("vods", vod_id, status="analyzed"); return

        cands = []
        for i, (peak, base) in enumerate(peaks):
            s, e = scoring.snap_boundaries(peak, rms, dur, min_len=st["clip_min"], max_len=st["clip_max"])
            cands.append({"peak": peak, "base": base, "start": s, "end": e, "text": "", "tscore": 0.0, "vscore": 0.0,
                          "signals": {"audio": round(float(a_sig[min(peak, len(a_sig) - 1)]), 2),
                                      "chat": round(float(c_sig[min(peak, len(c_sig) - 1)]), 2) if c_sig is not None else None}})

        # Raffinement sur les meilleurs candidats seulement (coût maîtrisé)
        top = sorted(cands, key=lambda c: -c["base"])[:int(want * 1.5)]
        whisper_ok = transcribe.available()
        for n, c in enumerate(top):
            ctx.progress(0.68 + 0.22 * n / max(len(top), 1), f"Raffinement {n + 1}/{len(top)}…")
            try:
                path, off = media.section(vod, c["start"] - 2, c["end"] + 2, height=360)
                m, _ = video.motion_profile(path, max(c["start"] - 2 - off, 0), c["end"] - c["start"] + 4)
                c["vscore"] = video.video_score(m)
            except Exception as e:
                log.warning("vidéo ignorée (%s)", e)
            if whisper_ok:
                try:
                    segs = transcribe.transcribe(media.vod_audio(vod), c["start"], c["end"], st["whisper_model"])
                    c["text"] = " ".join(s["text"] for s in segs)
                    c["tscore"] = scoring.text_score(c["text"])
                except Exception as e:
                    log.warning("transcription ignorée (%s)", e)

        rated = {}
        if st["use_llm"] and llm.available():
            ctx.progress(0.92, "Évaluation IA…")
            rated = llm.rate(vod["title"], [{"id": i, "text": c["text"][:600], "signals": c["signals"],
                                             "duration": round(c["end"] - c["start"])} for i, c in enumerate(top)])
        for i, c in enumerate(top):
            r = rated.get(i, {})
            c["llm"] = r.get("interest"); c["label"] = r.get("label"); c["title"] = r.get("title")

        for c in top:
            c["score"] = scoring.final_score(c["base"], c["tscore"], c["vscore"], c.get("llm"))
        final = sorted(top, key=lambda c: -c["score"])[:want]

        drop_previews(vod_id=vod_id)
        db.run("DELETE FROM moments WHERE vod_id=?", (vod_id,))
        new_ids = []
        for c in final:
            label = c.get("label") or _guess_label(c)
            new_ids.append(db.run("INSERT INTO moments(vod_id,start,end,peak,score,label,title,transcript,features) VALUES(?,?,?,?,?,?,?,?,?)",
                   (vod_id, c["start"], c["end"], c["peak"], c["score"], label, c.get("title"), c["text"],
                    json.dumps({"base": round(c["base"], 3), "text": round(c["tscore"], 3), "video": round(c["vscore"], 3),
                                "llm": c.get("llm"), **c["signals"], "chat_used": c_sig is not None,
                                "whisper": whisper_ok, "ai_rated": bool(rated)}))))
        db.update("vods", vod_id, status="analyzed")
        if st.get("auto_previews", True):
            for mid in new_ids: queue_preview(mid)
        ctx.progress(1.0, f"{len(final)} moments détectés")
    except Exception as e:
        db.update("vods", vod_id, status="error", error=str(e)[:300]); raise

def _guess_label(c):
    s = c["signals"]
    if s.get("chat") and s["chat"] > 0.7 and s["audio"] > 0.6: return "réaction + chat en feu"
    if c["tscore"] > 0.5: return "moment drôle / réaction"
    if c["vscore"] > 0.5: return "action intense"
    if s.get("chat") and s["chat"] > 0.7: return "chat en feu"
    return "pic audio"
