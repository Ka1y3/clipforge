@echo off
cd /d "%~dp0"
title ClipForge - mise a jour
set "DEST=%CD%"
set "ZIP="
for /f "delims=" %%F in ('powershell -NoProfile -Command "$d=(New-Object -ComObject Shell.Application).NameSpace('shell:Downloads').Self.Path; (Get-ChildItem $d -Filter 'clipforge*.zip' | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName"') do set "ZIP=%%F"
if not defined ZIP (
  echo Aucune archive clipforge*.zip trouvee dans Telechargements.
  echo Telechargez la nouvelle version depuis le chat, puis relancez ce fichier.
  pause
  exit /b 1
)
echo Archive utilisee : %ZIP%
echo Arret du serveur...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
echo Installation des fichiers (votre .env et vos donnees sont conserves)...
powershell -NoProfile -Command "$t=Join-Path $env:TEMP 'cf_update'; if(Test-Path $t){Remove-Item $t -Recurse -Force}; Expand-Archive -LiteralPath $env:ZIP -DestinationPath $t -Force; $src=(Get-ChildItem $t -Directory | Select-Object -First 1).FullName; robocopy $src $env:DEST /E /XD data __pycache__ /XF .env Mettre-a-jour.bat | Out-Null; Remove-Item $t -Recurse -Force"
echo Mise a jour terminee.
if exist "%DEST%\.git" (
  echo Sauvegarde sur GitHub...
  git -C "%DEST%" add -A
  git -C "%DEST%" commit -m "Mise a jour %DATE% %TIME%" >nul 2>&1
  git -C "%DEST%" push >nul 2>&1 && (echo Sauvegarde GitHub OK) || (echo Sauvegarde GitHub impossible - verifiez la connexion)
)
echo Demarrage...
call "%DEST%\Lancer-ClipForge.bat"
