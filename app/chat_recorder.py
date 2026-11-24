"""Enregistreur de chat Twitch (IRC anonyme, lecture seule, officiel).
Lancer pendant le live :  python -m app.chat_recorder <chaine>
Écrit data/chat/<chaine>.jsonl  ({"t": epoch_s, "user": .., "msg": ..}).
L'API Twitch ne fournit pas le chat des VOD : il faut l'enregistrer pendant le stream."""
import json, random, re, socket, ssl, sys, time, logging
from . import config

log = logging.getLogger("chat")
TAGS = re.compile(r"^@([^ ]+) :([^!]+)![^ ]+ PRIVMSG #[^ ]+ :(.*)$")

def parse(line):
    m = TAGS.match(line)
    if not m: return None
    tags = dict(t.split("=", 1) for t in m.group(1).split(";") if "=" in t)
    ts = int(tags.get("tmi-sent-ts", time.time() * 1000)) / 1000
    return {"t": ts, "user": m.group(2), "msg": m.group(3)}

def record(channel):
    out = config.DATA / "chat" / f"{channel.lower()}.jsonl"
    while True:
        try:
            raw = socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=30)
            s = ssl.create_default_context().wrap_socket(raw, server_hostname="irc.chat.twitch.tv")
            s.settimeout(330)
            for cmd in ("CAP REQ :twitch.tv/tags", "PASS SCHMOOPIIE", f"NICK justinfan{random.randint(10000, 99999)}",
                        f"JOIN #{channel.lower()}"):
                s.sendall((cmd + "\r\n").encode())
            buf = ""
            with open(out, "a", encoding="utf-8") as f:
                while True:
                    data = s.recv(8192)
                    if not data: raise ConnectionError("fermé")
                    buf += data.decode("utf-8", "replace")
                    *lines, buf = buf.split("\r\n")
                    for ln in lines:
                        if ln.startswith("PING"): s.sendall(b"PONG :tmi.twitch.tv\r\n")
                        else:
                            ev = parse(ln)
                            if ev: f.write(json.dumps(ev, ensure_ascii=False) + "\n"); f.flush()
        except Exception as e:
            log.warning("chat déconnecté (%s), reconnexion 5 s", e); time.sleep(5)

def load_for_vod(channel, created_iso, duration):
    """-> [(offset_secondes_dans_la_VOD, message)]"""
    from datetime import datetime
    f = config.DATA / "chat" / f"{(channel or '').lower()}.jsonl"
    if not channel or not f.exists(): return []
    t0 = datetime.fromisoformat(created_iso.replace("Z", "+00:00")).timestamp()
    res = []
    with open(f, encoding="utf-8") as fh:
        for line in fh:
            try: e = json.loads(line)
            except ValueError: continue
            off = e["t"] - t0
            if 0 <= off <= duration: res.append((off, e["msg"]))
    return res

if __name__ == "__main__":
    from . import log as _l; _l.setup()
    if len(sys.argv) < 2: sys.exit("usage: python -m app.chat_recorder <chaine>")
    record(sys.argv[1])
