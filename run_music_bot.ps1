$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONUTF8 = '1'

$venvActivate = Join-Path $scriptDir "bot_venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    . $venvActivate
}

python -m music_bot.bot
exit $LASTEXITCODE

