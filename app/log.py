import logging, sys
from logging.handlers import RotatingFileHandler
from .config import DATA

def setup():
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    sh = logging.StreamHandler(sys.stdout); sh.setFormatter(fmt)
    fh = RotatingFileHandler(DATA / "clipforge.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(sh); root.addHandler(fh)
