"""Stockage chiffré des tokens (Fernet). Clé locale dans data/secret.key (droits 600)."""
import json, os, time
from cryptography.fernet import Fernet
from . import config, db

def _fernet():
    p = config.KEY_PATH
    if not p.exists():
        p.write_bytes(Fernet.generate_key())
        try: os.chmod(p, 0o600)
        except OSError: pass
    return Fernet(p.read_bytes())

def save_account(provider, data: dict):
    tok = _fernet().encrypt(json.dumps(data).encode()).decode()
    db.run("INSERT INTO accounts(provider,data,updated) VALUES(?,?,?) "
           "ON CONFLICT(provider) DO UPDATE SET data=excluded.data, updated=excluded.updated",
           (provider, tok, time.time()))

def load_account(provider):
    r = db.one("SELECT data FROM accounts WHERE provider=?", (provider,))
    if not r: return None
    return json.loads(_fernet().decrypt(r["data"].encode()))

def delete_account(provider):
    db.run("DELETE FROM accounts WHERE provider=?", (provider,))
