"""YouTube Shorts via YouTube Data API v3 (OAuth Google, upload resumable) — API officielle uniquement."""
import json, secrets, time, urllib.parse
from pathlib import Path
from .. import config, secure
from ..http import request, HttpError

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
_states = {}

def configured(): return bool(config.env("GOOGLE_CLIENT_ID") and config.env("GOOGLE_CLIENT_SECRET"))
def connected(): return secure.load_account("youtube") is not None
def redirect_uri(): return f"{config.BASE_URL}/auth/google/callback"

def login_url():
    st = secrets.token_urlsafe(24); _states[st] = time.time() + 600
    return AUTH + "?" + urllib.parse.urlencode({
        "client_id": config.env("GOOGLE_CLIENT_ID"), "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": st})

def handle_callback(code, state):
    if _states.pop(state, 0) < time.time(): raise ValueError("state OAuth invalide ou expiré")
    tok = request("POST", TOKEN, data={"code": code, "client_id": config.env("GOOGLE_CLIENT_ID"),
        "client_secret": config.env("GOOGLE_CLIENT_SECRET"), "redirect_uri": redirect_uri(), "grant_type": "authorization_code"})
    tok["expires_at"] = time.time() + tok["expires_in"] - 60
    secure.save_account("youtube", {"token": tok})

def _access():
    acc = secure.load_account("youtube")
    if not acc: raise RuntimeError("YouTube non connecté")
    t = acc["token"]
    if t["expires_at"] < time.time():
        n = request("POST", TOKEN, data={"client_id": config.env("GOOGLE_CLIENT_ID"), "client_secret": config.env("GOOGLE_CLIENT_SECRET"),
                                         "refresh_token": t["refresh_token"], "grant_type": "refresh_token"})
        t.update(access_token=n["access_token"], expires_at=time.time() + n["expires_in"] - 60)
        secure.save_account("youtube", acc)
    return t["access_token"]

def publish(clip, title, description, hashtags, options=None):
    """Envoie la vidéo. Retourne (video_id, url). Si scheduled_at (epoch) : privé + publishAt côté YouTube."""
    scheduled_at = None                      # la programmation est gérée par le planificateur de ClipForge
    path = Path(clip["path"])
    if not path.is_file(): raise RuntimeError("fichier du clip introuvable")
    tags = [h.lstrip("#") for h in (hashtags or "").split() if h.startswith("#")]
    vertical = clip["fmt"] == "9:16"
    desc = (description or "") + ("\n\n" + " ".join("#" + t for t in tags) if tags else "")
    if vertical and "#shorts" not in desc.lower(): desc += " #Shorts"
    status = {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    if scheduled_at and scheduled_at > time.time() + 60:
        status.update(privacyStatus="private", publishAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(scheduled_at)))
    meta = {"snippet": {"title": title[:100], "description": desc[:5000], "tags": tags[:15], "categoryId": "20"}, "status": status}
    data = path.read_bytes()
    tok = _access()
    _, hdr, _ = request("POST", "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                        json_body=meta, raw=True, headers={"Authorization": f"Bearer {tok}", "X-Upload-Content-Type": "video/mp4",
                                                           "X-Upload-Content-Length": str(len(data))})
    loc = {k.lower(): v for k, v in hdr.items()}["location"]
    res = request("PUT", loc, data=data, headers={"Content-Type": "video/mp4", "Authorization": f"Bearer {tok}"}, timeout=1800)
    vid = res["id"]
    return vid, f"https://youtube.com/shorts/{vid}" if vertical else f"https://youtube.com/watch?v={vid}"
