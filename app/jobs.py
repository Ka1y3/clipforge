"""File de tâches persistante (SQLite) + worker en arrière-plan."""
import json, logging, threading, time, traceback
from . import db

log = logging.getLogger("jobs")
HANDLERS = {}
_wake = threading.Event()
_stop = threading.Event()

def handler(kind):
    def deco(fn): HANDLERS[kind] = fn; return fn
    return deco

class Ctx:
    def __init__(self, job_id): self.id = job_id
    def progress(self, p, msg=None):
        db.update("jobs", self.id, progress=round(float(p), 3), **({"message": msg} if msg else {}))

def enqueue(kind, **payload):
    jid = db.run("INSERT INTO jobs(kind,payload,created) VALUES(?,?,?)", (kind, json.dumps(payload), time.time()))
    _wake.set()
    return jid

def _next():
    j = db.one("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1")
    if j:
        db.update("jobs", j["id"], status="running", started=time.time())
    return j

def run_one():
    j = _next()
    if not j: return False
    ctx = Ctx(j["id"])
    try:
        HANDLERS[j["kind"]](ctx, **json.loads(j["payload"] or "{}"))
        db.update("jobs", j["id"], status="done", progress=1.0, finished=time.time())
    except Exception as e:
        log.error("job %s (%s) échoué: %s\n%s", j["id"], j["kind"], e, traceback.format_exc())
        db.update("jobs", j["id"], status="error", error=str(e)[:500], finished=time.time())
        on_fail = HANDLERS.get(j["kind"] + ":fail")
        if on_fail:
            try: on_fail(**json.loads(j["payload"] or "{}"), error=str(e))
            except Exception: log.exception("on_fail")
    return True

def _loop():
    while not _stop.is_set():
        try:
            if not run_one():
                _wake.wait(2); _wake.clear()
        except Exception:
            log.exception("worker"); time.sleep(2)

def start():
    # tâches interrompues par un arrêt brutal -> erreur explicite
    db.run("UPDATE jobs SET status='error', error='interrompu (redémarrage)' WHERE status='running'")
    threading.Thread(target=_loop, name="job-worker", daemon=True).start()

def stop(): _stop.set(); _wake.set()
