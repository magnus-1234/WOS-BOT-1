@echo off
cd /d %~dp0

set PYTHONUTF8=1

if exist "bot_venv\Scripts\activate.bat" (
    call "bot_venv\Scripts\activate.bat"
)

python -m music_bot.bot
