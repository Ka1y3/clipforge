@echo off
cd /d "%~dp0"
title ClipForge
echo Arret de l'ancien serveur s'il tourne...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
echo Verification des composants (rapide)...
py -3.12 -m pip install -q -r requirements.txt
start "" cmd /c "timeout /t 5 >nul & start http://localhost:8000"
echo ClipForge demarre sur http://localhost:8000 - laissez cette fenetre ouverte.
py -3.12 run.py
pause
