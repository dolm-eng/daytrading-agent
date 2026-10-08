@echo off
REM A planifier dans le Planificateur de taches Windows, du lundi au vendredi
REM vers 15h20 (heure de Paris). Le bot attend lui-meme l'ouverture du marche
REM US, trade pendant la seance, ferme tout avant la cloture et envoie un
REM email. Le PC doit rester allume (et ne pas se mettre en veille) jusqu'a
REM environ 22h05. Lance setup_test.bat au moins une fois avant.

cd /d "%~dp0"
if not exist logs mkdir logs

if not exist venv\Scripts\python.exe (
    echo [%date% %time%] venv introuvable, lance setup_test.bat d'abord. >> logs\run.log
    exit /b 1
)

call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
echo [%date% %time%] Debut >> logs\run.log
python live_daytrade.py >> logs\run.log 2>&1
echo [%date% %time%] Fin >> logs\run.log
