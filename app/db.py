import json, sqlite3, threading
from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (provider TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL);
CREATE TABLE IF NOT EXISTS vods (
  id INTEGER PRIMARY KEY AUTOINCREMENT, twitch_id TEXT UNIQUE, title TEXT, url TEXT,
  duration REAL, created_at TEXT, thumbnail TEXT, game TEXT, channel TEXT,
  status TEXT DEFAULT 'new', error TEXT, added REAL);
CREATE TABLE IF NOT EXISTS moments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, vod_id INTEGER NOT NULL, start REAL, end REAL, peak REAL,
  score REAL, label TEXT, title TEXT, transcript TEXT, features TEXT, status TEXT DEFAULT 'new', edit TEXT,
  FOREIGN KEY(vod_id) REFERENCES vods(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY AUTOINCREMENT, moment_id INTEGER, vod_id INTEGER, fmt TEXT, path TEXT,
  options TEXT, status TEXT DEFAULT 'queued', title TEXT, description TEXT, hashtags TEXT,
  duration REAL, created REAL, draft INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER NOT NULL, platform TEXT NOT NULL,
  status TEXT DEFAULT 'scheduled', scheduled_at REAL, external_id TEXT, external_url TEXT,
  error TEXT, created REAL);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT, status TEXT DEFAULT 'queued',
  progress REAL DEFAULT 0, message TEXT, error TEXT, created REAL, started REAL, finished REAL);
CREATE INDEX IF NOT EXISTS idx_moments_vod ON moments(vod_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);
"""

def conn():
    c = getattr(_local, "c", None)
    path = str(config.DB_PATH)
    if c is None or getattr(_local, "path", None) != path:
        c = sqlite3.connect(path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.c, _local.path = c, path
    return c

def init():
    conn().executescript(SCHEMA)
    cols = [r[1] for r in conn().execute("PRAGMA table_info(publications)")]
    ccols = [r[1] for r in conn().execute("PRAGMA table_info(clips)")]
    if "edit" not in [r[1] for r in conn().execute("PRAGMA table_info(moments)")]:
        conn().execute("ALTER TABLE moments ADD COLUMN edit TEXT")
    if "draft" not in ccols:
        conn().execute("ALTER TABLE clips ADD COLUMN draft INTEGER DEFAULT 0")
    if "options" not in cols:
        conn().execute("ALTER TABLE publications ADD COLUMN options TEXT")
    conn().commit()

def q(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]

def one(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None

def run(sql, args=()):
    cur = conn().execute(sql, args)
    conn().commit()
    return cur.lastrowid

def update(table, id_, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    run(f"UPDATE {table} SET {cols} WHERE id=?", (*fields.values(), id_))

DEFAULT_SETTINGS = {
    "auto_sync": True, "auto_previews": True, "sync_minutes": 15, "auto_analyze": False,
    "clip_min": 15, "clip_max": 60, "max_moments": 12,
    "whisper_model": "", "use_llm": True,
    "webcam_rect": None, "game_rect": None, "layout": "split", "posting_times": ["08:00", "14:30", "19:55"],
    "subtitle_style": "bold", "default_hashtags": "#twitch #clips",
}

def get_settings():
    row = one("SELECT v FROM kv WHERE k='settings'")
    s = dict(DEFAULT_SETTINGS)
    if row:
        s.update(json.loads(row["v"]))
    return s

def set_settings(patch):
    s = get_settings()
    s.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
    run("INSERT INTO kv(k,v) VALUES('settings',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (json.dumps(s),))
    return s
