param([switch]$SkipModel)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install requirements.txt first. See README.md.' }
New-Item -ItemType Directory -Force data | Out-Null
& $taskPython -m evidence_arena.cli migrate
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed' }
$taskOllama = Join-Path $PSScriptRoot 'data\runtime-cpu\ollama.exe'
if (-not $SkipModel -and (Test-Path -LiteralPath $taskOllama)) {
    $env:OLLAMA_HOST = '127.0.0.1:11435'
    $env:OLLAMA_MODELS = Join-Path $PSScriptRoot 'data\models'
    $env:OLLAMA_NUM_PARALLEL = '1'
    $env:OLLAMA_CONTEXT_LENGTH = '8192'
    $env:OLLAMA_MAX_LOADED_MODELS = '1'
    if (-not (Test-NetConnection 127.0.0.1 -Port 11435 -InformationLevel Quiet -WarningAction SilentlyContinue)) {
        Start-Process -FilePath $taskOllama -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput 'data\ollama.out.log' -RedirectStandardError 'data\ollama.err.log'
    }
}
$taskWorker = Start-Process -FilePath $taskPython -ArgumentList '-m evidence_arena.worker' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput 'data\worker.out.log' -RedirectStandardError 'data\worker.err.log'
try {
    if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) {
        Push-Location frontend
        try { & npm.cmd install --no-audit --no-fund; & npm.cmd run build } finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw 'React build failed' }
    }
    & $taskPython -m uvicorn evidence_arena.api:app --host 127.0.0.1 --port 8501
} finally {
    if (-not $taskWorker.HasExited) { Stop-Process -Id $taskWorker.Id }
}

