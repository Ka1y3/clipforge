# ClipForge — VOD Twitch → clips prêts à publier

## Installation
```
pip install -r requirements.txt     # + FFmpeg installé (avec libass)
cp .env.example .env                # renseigner TWITCH_*, GOOGLE_*, (ANTHROPIC_API_KEY optionnel)
python run.py                       # http://localhost:8000
```
Twitch : créer une app sur dev.twitch.tv, redirect `http://localhost:8000/auth/twitch/callback`.
Google : activer YouTube Data API v3, redirect `http://localhost:8000/auth/google/callback`.

## Chat (signal important)
L'API Twitch ne donne pas le chat des VOD. Pendant le live : `python -m app.chat_recorder <chaine>`.
Sans chat enregistré, la détection utilise audio + vidéo + voix (poids renormalisés).

## Détection
audio (volume anormal, montée brutale, timbre aigu) + chat (débit, emotes) → pics espacés → bornes calées sur les pauses
→ raffinement des meilleurs candidats seulement : mouvement vidéo, transcription Whisper, note IA → score 0–100.
Le coût reste faible : audio-only pour l'analyse, vidéo téléchargée uniquement par section.

## Tests
`python -m unittest tests.test_detection tests.test_pipeline`

## TikTok
developers.tiktok.com → Manage apps → créer une app, plateforme **Desktop**.
Produits : *Login Kit* (redirect `http://localhost:8000/auth/tiktok/callback`) + *Content Posting API* (activer *Direct Post*).
Scopes : user.info.basic, video.publish, video.upload. Mettre Client key/secret dans `.env` (TIKTOK_*).
Tant que l'app n'est pas auditée par TikTok, les publications directes sont limitées à la visibilité privée ; le mode « brouillon » envoie la vidéo dans l'app TikTok pour finalisation.
