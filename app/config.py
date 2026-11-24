import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CLIPFORGE_DATA", ROOT / "data"))
for sub in ("cache", "clips", "chat", "tmp"):
    (DATA / sub).mkdir(parents=True, exist_ok=True)

def _load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
_load_env()

def env(name, default=""):
    return os.environ.get(name, default)

BASE_URL = env("BASE_URL", "http://localhost:8000")
DB_PATH = DATA / "clipforge.db"
KEY_PATH = DATA / "secret.key"
