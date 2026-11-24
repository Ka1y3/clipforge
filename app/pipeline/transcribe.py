"""Reconnaissance vocale (faster-whisper, optionnelle) — uniquement sur les fenêtres candidates."""
import logging
from .. import config

log = logging.getLogger("transcribe")
_model = None

def available():
    try:
        import faster_whisper  # noqa
        return True
    except ImportError:
        return False

def _get(model_name):
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        name = model_name or config.env("WHISPER_MODEL", "small")
        log.info("chargement whisper %s", name)
        _model = WhisperModel(name, device="auto", compute_type="auto")
    return _model

def transcribe(path, start, end, model_name="", lang=None):
    """Retourne [{'start','end','text','words':[{'w','s','e'}]}] en temps absolu de la source."""
    from .audio import read_pcm
    import numpy as np
    x = read_pcm(path, start, end - start)
    if len(x) < 1600: return []
    segs, _ = _get(model_name).transcribe(
        x.astype(np.float32), language=lang or config.env("WHISPER_LANG", "fr") or None,
        word_timestamps=True, vad_filter=True, beam_size=1)
    out = []
    for s in segs:
        words = [{"w": w.word.strip(), "s": start + w.start, "e": start + w.end} for w in (s.words or [])]
        out.append({"start": start + s.start, "end": start + s.end, "text": s.text.strip(), "words": words})
    return out
