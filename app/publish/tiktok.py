"""TikTok Content Posting API (officielle). Login Kit « Desktop » : redirect http://localhost + PKCE (SHA256 hex).
Modes : 'direct' (Direct Post, scope video.publish) ou 'draft' (boîte de réception, scope video.upload).
Tant que l'app TikTok n'est pas auditée, TikTok limite les publications directes à la visibilité privée."""
import hashlib, logging, secrets, string, time, urllib.parse
from pathlib import Path
from .. import config, secure, http as H

log = logging.getLogger("tiktok")
AUTH = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"
SCOPES = "user.info.basic,video.publish,video.upload"
CHUNK = 32 * 1024 * 1024          # TikTok : 5–64 Mo par morceau (dernier jusqu'à 128 Mo)
_states = {}                      # state -> (expiration, code_verifier)

def configured(): return bool(config.env("TIKTOK_CLIENT_KEY") and config.env("TIKTOK_CLIENT_SECRET"))
def connected(): return secure.load_account("tiktok") is not None
def redirect_uri(): return f"{config.BASE_URL}/auth/tiktok/callback"

def pkce_pair():
    alphabet = string.ascii_letters + string.digits + "-._~"
    verifier = "".join(secrets.choice(alphabet) for _ in range(64))
    return verifier, hashlib.sha256(verifier.encode()).hexdigest()      # TikTok exige le SHA256 en hexadécimal

def login_url():
    state = secrets.token_urlsafe(24); verifier, challenge = pkce_pair()
    _states[state] = (time.time() + 600, verifier)
    return AUTH + "?" + urllib.parse.urlencode({
        "client_key": config.env("TIKTOK_CLIENT_KEY"), "scope": SCOPES, "response_type": "code",
        "redirect_uri": redirect_uri(), "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})

def _store(tok):
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 86400)) - 60
    secure.save_account("tiktok", {"token": tok})

def handle_callback(code, state):
    exp, verifier = _states.pop(state, (0, None))
    if exp < time.time(): raise ValueError("state OAuth invalide ou expiré")
    tok = H.request("POST", f"{API}/v2/oauth/token/", data={
        "client_key": config.env("TIKTOK_CLIENT_KEY"), "client_secret": config.env("TIKTOK_CLIENT_SECRET"),
        "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri(), "code_verifier": verifier})
    if "access_token" not in tok: raise ValueError(f"TikTok: {tok.get('error_description') or tok}")
    _store(tok)

def _access():
    acc = secure.load_account("tiktok")
    if not acc: raise RuntimeError("TikTok non connecté")
    t = acc["token"]
    if t["expires_at"] < time.time():
        n = H.request("POST", f"{API}/v2/oauth/token/", data={
            "client_key": config.env("TIKTOK_CLIENT_KEY"), "client_secret": config.env("TIKTOK_CLIENT_SECRET"),
            "grant_type": "refresh_token", "refresh_token": t["refresh_token"]})
        if "access_token" not in n: raise RuntimeError("TikTok: session expirée, reconnectez le compte")
        _store(n)
        t = n
    return t["access_token"]

def _post(path, body):
    res = H.request("POST", API + path, json_body=body, headers={"Authorization": f"Bearer {_access()}"})
    err = res.get("error") or {}
    if err.get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok {err.get('code')}: {err.get('message')}")
    return res.get("data") or {}

def creator_info():
    """Pseudo, visibilités autorisées, durée max, réglages commentaires/duo/stitch du compte."""
    return _post("/v2/post/publish/creator_info/query/", {})

def plan_chunks(size):
    if size <= CHUNK: return size, 1
    return CHUNK, size // CHUNK            # le reste est fusionné dans le dernier morceau

def _upload(upload_url, path, size, chunk, total):
    with open(path, "rb") as f:
        for i in range(total):
            start = i * chunk
            end = size - 1 if i == total - 1 else start + chunk - 1
            f.seek(start); data = f.read(end - start + 1)
            st, _, _ = H.request("PUT", upload_url, data=data, raw=True, timeout=1800, headers={
                "Content-Type": "video/mp4", "Content-Range": f"bytes {start}-{end}/{size}"})
            if st not in (201, 206): raise RuntimeError(f"upload TikTok: statut {st}")

def publish(clip, title, description, hashtags, options=None):
    """Retourne (publish_id, url, note)."""
    o = options or {}
    path = Path(clip["path"])
    if not path.is_file(): raise RuntimeError("fichier du clip introuvable")
    size = path.stat().st_size; chunk, total = plan_chunks(size)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": total}
    mode = o.get("mode", "direct"); username = None
    if mode == "draft":
        data = _post("/v2/post/publish/inbox/video/init/", {"source_info": source})
    else:
        info = creator_info(); username = info.get("creator_username")
        mx = info.get("max_video_post_duration_sec")
        if mx and (clip.get("duration") or 0) > mx: raise RuntimeError(f"clip trop long pour ce compte (max {mx} s)")
        privacy = o.get("privacy")
        if privacy not in (info.get("privacy_level_options") or []):
            raise RuntimeError(f"visibilité '{privacy}' non autorisée (options : {info.get('privacy_level_options')})")
        caption = " ".join(x for x in [title or "", " ".join(h for h in (hashtags or "").split() if h.startswith("#"))] if x)[:2200]
        data = _post("/v2/post/publish/video/init/", {
            "post_info": {"title": caption, "privacy_level": privacy,
                          "disable_comment": bool(o.get("disable_comment", info.get("comment_disabled", False))),
                          "disable_duet": bool(o.get("disable_duet", info.get("duet_disabled", False))),
                          "disable_stitch": bool(o.get("disable_stitch", info.get("stitch_disabled", False))),
                          "video_cover_timestamp_ms": 1000},
            "source_info": source})
    pid = data["publish_id"]
    _upload(data["upload_url"], path, size, chunk, total)
    for _ in range(40):                                   # ~3 min max
        st = _post("/v2/post/publish/status/fetch/", {"publish_id": pid})
        s = st.get("status")
        if s == "PUBLISH_COMPLETE":
            ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
            url = f"https://www.tiktok.com/@{username}/video/{ids[0]}" if ids and username else ""
            note = "Publié en privé (app TikTok non auditée)." if o.get("privacy") == "SELF_ONLY" else ""
            return pid, url, note
        if s == "SEND_TO_USER_INBOX":
            return pid, "", "Envoyé dans votre boîte de réception TikTok : ouvrez l'app et terminez la publication."
        if s == "FAILED": raise RuntimeError(f"TikTok a refusé la vidéo : {st.get('fail_reason')}")
        time.sleep(5)
    raise RuntimeError("TikTok : délai dépassé pendant le traitement (vérifiez dans l'app)")
