"""Twitch : OAuth (authorization code), Helix (VOD), jetons chiffrés + refresh auto."""
import re, secrets, time, urllib.parse, logging
from datetime import datetime, timezone
from . import config, db, secure
from .http import request, HttpError

log = logging.getLogger("twitch")
AUTH = "https://id.twitch.tv/oauth2"
HELIX = "https://api.twitch.tv/helix"
SCOPES = ["user:read:email"]
_states = {}          # state -> expiry (anti-CSRF)
_app_token = {"t": None, "exp": 0}

def configured():
    return bool(config.env("TWITCH_CLIENT_ID") and config.env("TWITCH_CLIENT_SECRET"))

def redirect_uri():
    return f"{config.BASE_URL}/auth/twitch/callback"

def login_url():
    state = secrets.token_urlsafe(24); _states[state] = time.time() + 600
    return f"{AUTH}/authorize?" + urllib.parse.urlencode({
        "client_id": config.env("TWITCH_CLIENT_ID"), "redirect_uri": redirect_uri(),
        "response_type": "code", "scope": " ".join(SCOPES), "state": state})

def handle_callback(code, state):
    exp = _states.pop(state, 0)
    if exp < time.time(): raise ValueError("state OAuth invalide ou expiré")
    tok = request("POST", f"{AUTH}/token", data={
        "client_id": config.env("TWITCH_CLIENT_ID"), "client_secret": config.env("TWITCH_CLIENT_SECRET"),
        "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri()})
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    me = _get("/users", tok["access_token"])["data"][0]
    secure.save_account("twitch", {"token": tok, "user": {
        "id": me["id"], "login": me["login"], "name": me["display_name"], "avatar": me.get("profile_image_url")}})
    return me["login"]

def _refresh(acc):
    tok = request("POST", f"{AUTH}/token", data={
        "client_id": config.env("TWITCH_CLIENT_ID"), "client_secret": config.env("TWITCH_CLIENT_SECRET"),
        "grant_type": "refresh_token", "refresh_token": acc["token"]["refresh_token"]})
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    acc["token"] = tok; secure.save_account("twitch", acc)
    return acc

def _get(path, token, params=None):
    return request("GET", HELIX + path, params=params, headers={
        "Authorization": f"Bearer {token}", "Client-Id": config.env("TWITCH_CLIENT_ID")})

def account():
    return secure.load_account("twitch")

def user_token():
    acc = account()
    if not acc: raise RuntimeError("Compte Twitch non connecté")
    if acc["token"]["expires_at"] < time.time(): acc = _refresh(acc)
    return acc["token"]["access_token"], acc

def app_token():
    if _app_token["exp"] < time.time():
        t = request("POST", f"{AUTH}/token", data={
            "client_id": config.env("TWITCH_CLIENT_ID"), "client_secret": config.env("TWITCH_CLIENT_SECRET"),
            "grant_type": "client_credentials"})
        _app_token.update(t=t["access_token"], exp=time.time() + t["expires_in"] - 60)
    return _app_token["t"]

def parse_duration(s):
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", s or "")
    h, mi, se = (int(x or 0) for x in m.groups()) if m else (0, 0, 0)
    return h * 3600 + mi * 60 + se

def _upsert(v):
    thumb = (v.get("thumbnail_url") or "").replace("%{width}", "320").replace("%{height}", "180")
    row = (v["id"], v["title"], v["url"], parse_duration(v["duration"]), v["created_at"], thumb, v.get("user_login"), time.time())
    if db.one("SELECT id FROM vods WHERE twitch_id=?", (v["id"],)):
        db.run("UPDATE vods SET title=?, duration=?, thumbnail=? WHERE twitch_id=?", (v["title"], row[3], thumb, v["id"]))
        return False
    db.run("INSERT INTO vods(twitch_id,title,url,duration,created_at,thumbnail,channel,added) VALUES(?,?,?,?,?,?,?,?)", row)
    return True

def sync_vods(max_pages=5):
    """Récupère les VOD (archives) du compte connecté. Renvoie le nombre de nouvelles VOD."""
    token, acc = user_token(); new = 0; cursor = None
    for _ in range(max_pages):
        params = {"user_id": acc["user"]["id"], "type": "archive", "first": 100}
        if cursor: params["after"] = cursor
        res = _get("/videos", token, params)
        for v in res["data"]:
            new += _upsert(v)
        cursor = res.get("pagination", {}).get("cursor")
        if not cursor: break
    log.info("sync VOD: %d nouvelles", new)
    return new

def add_by_url(url):
    m = re.search(r"twitch\.tv/videos/(\d+)", url) or re.fullmatch(r"(\d+)", url.strip())
    if not m: raise ValueError("URL de VOD Twitch invalide")
    res = _get("/videos", app_token(), {"id": m.group(1)})
    if not res["data"]: raise ValueError("VOD introuvable")
    _upsert(res["data"][0])
    return db.one("SELECT * FROM vods WHERE twitch_id=?", (m.group(1),))

def add_local(path, title=None):
    """Fichier vidéo local (VOD déjà téléchargée)."""
    import subprocess
    from pathlib import Path
    p = Path(path).expanduser().resolve()
    if not p.is_file(): raise ValueError("fichier introuvable")
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                               capture_output=True, text=True, check=True).stdout.strip())
    created = datetime.fromtimestamp(p.stat().st_mtime - dur, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tid = f"local:{p.name}:{int(p.stat().st_size)}"
    if not db.one("SELECT id FROM vods WHERE twitch_id=?", (tid,)):
        db.run("INSERT INTO vods(twitch_id,title,url,duration,created_at,added) VALUES(?,?,?,?,?,?)",
               (tid, title or p.stem, str(p), dur, created, time.time()))
    return db.one("SELECT * FROM vods WHERE twitch_id=?", (tid,))
