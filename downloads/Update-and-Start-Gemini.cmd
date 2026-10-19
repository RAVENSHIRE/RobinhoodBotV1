@echo off
setlocal EnableExtensions DisableDelayedExpansion
title HoodScout - Gemini Paper Setup
set "HOODSCOUT_PROJECT=%~1"
if not defined HOODSCOUT_PROJECT set "HOODSCOUT_PROJECT=%USERPROFILE%\Desktop\dev\hoodscout-robinhood"
if not exist "%HOODSCOUT_PROJECT%\.venv\Scripts\python.exe" goto missing
if not exist "%~dp0apply_update.py" goto extract

echo Stop the old HoodScout server with Ctrl+C before continuing.
echo Add GEMINI_API_KEY to the existing project .env before running this setup.
echo Your key is read locally, never displayed. API checks may use credits.
echo This update preserves .env, data and .venv and backs up changed source files.
echo PAPER mode only. No wallet or transactions.
pause
pushd "%HOODSCOUT_PROJECT%"
if errorlevel 1 goto failed
set "HOODSCOUT_PUSHED=1"
rem Clear inherited provider/key overrides so the project's .env is authoritative.
set "GEMINI_API_KEY="
set "DECISION_PROVIDER="
set "LIVE_MODE=false"
set "DATA_DIR=./data/paper"
set "PRICE_FEED_MODE=coinbase_kraken"
set "RPC_URL=https://rpc.mainnet.chain.robinhood.com"
set "CHAIN_ID=4663"
set "QUOTER_V2_ADDRESS=0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7"
set "HOST=127.0.0.1"
set "PORT=8000"
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$c=New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1',8000); $c.Dispose(); Write-Host 'Port 8000 is in use. Stop the existing server first.'; exit 1 } catch { $c.Dispose(); exit 0 }"
if errorlevel 1 goto failed
".\.venv\Scripts\python.exe" "%~dp0apply_update.py" --project .
if errorlevel 1 goto failed
".\.venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto failed
".\.venv\Scripts\python.exe" -m hoodscout.cli gemini-setup
if errorlevel 1 goto failed

echo Starting Gemini PAPER dashboard. Startup checks may use another API call.
echo Keep this window open; close it to stop the server.
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; $python=(Resolve-Path '.\.venv\Scripts\python.exe').Path; $p=Start-Process -FilePath $python -ArgumentList @('-m','hoodscout.cli','serve') -NoNewWindow -PassThru; $ready=$false; for ($i=0; $i -lt 30; $i++) { if ($p.HasExited) { throw 'HoodScout stopped before startup. Read the server error above.' }; try { $s=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/state' -TimeoutSec 1; if ($s.mode -eq 'PAPER' -and $s.decision_provider -eq 'gemini' -and $s.live_execution_available -eq $false) { $ready=$true; break } } catch {}; Start-Sleep -Seconds 1 }; if ($ready) { Start-Process 'http://127.0.0.1:8000' } else { Write-Warning 'Dashboard did not respond; inspect the server output.' }; $p.WaitForExit(); exit $p.ExitCode"
if errorlevel 1 goto failed
popd
pause
exit /b 0
:missing
echo Project virtual environment not found at %HOODSCOUT_PROJECT%
echo Drag your actual project directory onto this CMD file.
pause
exit /b 1
:extract
echo Extract the entire ZIP before running this CMD. apply_update.py must be alongside it.
pause
exit /b 1
:failed
echo SETUP STOPPED. Read the error above. No real transactions have been enabled.
echo Preserve your .env and database. Never paste your key into chat.
if defined HOODSCOUT_PUSHED popd
pause
exit /b 1
