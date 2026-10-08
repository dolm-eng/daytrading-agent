@echo off
cd /d "%~dp0"

echo === Verification de Python ===
python --version
if errorlevel 1 (
    echo ERREUR : Python n'est pas installe ou pas dans le PATH.
    echo Installe-le depuis https://www.python.org/downloads/ en cochant "Add Python to PATH".
    pause
    exit /b 1
)

echo.
echo === Environnement virtuel + dependances ===
if not exist venv\Scripts\python.exe python -m venv venv
call venv\Scripts\activate.bat
pip install -r requirements.txt

echo.
echo === Tests automatiques ===
python -m pytest -q

if not exist .env (
    echo.
    echo ATTENTION : pas de fichier .env. Copie .env.example en .env et mets tes cles PAPER Alpaca.
    pause
    exit /b 1
)

echo.
echo === Test du bot : une evaluation, SANS envoyer d'ordre ===
python live_daytrade.py --once --dry-run

echo.
echo === Termine. Appuie sur une touche pour fermer. ===
pause
