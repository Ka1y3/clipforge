class Unavailable(Exception): pass

def publish(clip, *a, **k):
    raise Unavailable("Publication non implémentée pour cette plateforme (voir README : API soumise à validation/audit).")
