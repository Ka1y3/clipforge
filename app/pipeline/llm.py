"""Notation optionnelle par IA (Anthropic). Appelée seulement sur les meilleurs candidats."""
import json, logging, re, urllib.request
from .. import config

log = logging.getLogger("llm")

def available():
    return bool(config.env("ANTHROPIC_API_KEY"))

PROMPT = """Tu évalues des extraits de stream Twitch pour en faire des clips courts (TikTok/Shorts).
Pour chaque extrait, donne: interest (0-10, potentiel viral: drôle, surprenant, intense, clutch, fail),
label (court: ex. "fou rire", "clutch", "fail", "réaction"), title (titre accrocheur <= 60 caractères, langue de l'extrait).
Contexte: titre du live "{vod}". Extraits (JSON):
{items}
Réponds UNIQUEMENT par un tableau JSON: [{{"id":..,"interest":..,"label":"..","title":".."}}]"""

def rate(vod_title, items):
    """items: [{'id':int,'text':str,'signals':{...}}] -> {id: {interest,label,title}}"""
    if not items or not available(): return {}
    body = json.dumps({
        "model": config.env("CLAUDE_MODEL", "claude-haiku-5-5"), "max_tokens": 1500,
        "messages": [{"role": "user", "content": PROMPT.format(vod=vod_title, items=json.dumps(items, ensure_ascii=False))}],
    }).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", body, {
        "content-type": "application/json", "x-api-key": config.env("ANTHROPIC_API_KEY"),
        "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        text = "".join(b.get("text", "") for b in data.get("content", []))
        arr = json.loads(re.search(r"\[.*\]", text, re.S).group(0))
        return {int(a["id"]): a for a in arr}
    except Exception as e:
        log.warning("LLM indisponible: %s", e)
        return {}
