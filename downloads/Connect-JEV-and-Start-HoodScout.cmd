@echo off
setlocal EnableExtensions DisableDelayedExpansion
title HoodScout - Connect JEV and Start

rem Run from Downloads, or drag your project folder onto this file.
set "HOODSCOUT_PROJECT=%~1"
if not defined HOODSCOUT_PROJECT if exist "%~dp0.venv\Scripts\python.exe" set "HOODSCOUT_PROJECT=%~dp0."
if not defined HOODSCOUT_PROJECT set "HOODSCOUT_PROJECT=%USERPROFILE%\Desktop\dev\hoodscout-robinhood"
if not exist "%HOODSCOUT_PROJECT%\.venv\Scripts\python.exe" goto missing_project
if not exist "%HOODSCOUT_PROJECT%\pyproject.toml" goto missing_project

echo Project: %HOODSCOUT_PROJECT%
echo Stop any existing HoodScout server with Ctrl+C before continuing.
echo This updater preserves .env, data and .venv and backs up changed source files.
pause

set "HOODSCOUT_UPDATE_WORK=%TEMP%\hoodscout-update-%RANDOM%-%RANDOM%"
set "HOODSCOUT_UPDATE_URL=https://github.com/RAVENSHIRE/RobinhoodBotV1/raw/2749305ad1fb1c97e1d579cdaadd37d6d9424b75/downloads/hoodscout-jev-env-update.zip"
set "HOODSCOUT_UPDATE_SHA=6bf40bbde3d7bcf28a50ddd9a72f88f5c7b92b9967fe3ca60cef0328edb53332"

echo Downloading and verifying the tested update...
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $work=$env:HOODSCOUT_UPDATE_WORK; New-Item -ItemType Directory -Path $work | Out-Null; $zip=Join-Path $work 'update.zip'; Invoke-WebRequest -UseBasicParsing -Uri $env:HOODSCOUT_UPDATE_URL -OutFile $zip; if ((Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant() -ne $env:HOODSCOUT_UPDATE_SHA) { throw 'Update checksum mismatch; nothing has been installed' }; Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $work 'unpacked')"
if errorlevel 1 goto failed

pushd "%HOODSCOUT_PROJECT%"
if errorlevel 1 goto failed
set "HOODSCOUT_PUSHED=1"

echo Applying the update...
".\.venv\Scripts\python.exe" "%HOODSCOUT_UPDATE_WORK%\unpacked\apply_update.py" --project .
if errorlevel 1 goto failed

set "DATA_DIR=./data/paper"
set "LIVE_MODE=false"
rem Tests run without a real decision provider. Setup explicitly checks JEV afterward.
set "DECISION_PROVIDER=stub"
rem Use the project .env instead of inherited credentials from an older terminal.
set "TYPESAFE_API_KEY="
set "PRICE_FEED_MODE=coinbase_kraken"
set "RPC_URL=https://rpc.mainnet.chain.robinhood.com"
set "CHAIN_ID=4663"
set "QUOTER_V2_ADDRESS=0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7"
set "HOST=127.0.0.1"
set "PORT=8000"

echo Running the regression tests...
".\.venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto failed

echo Connecting JEV with the key from your project .env file.
echo VERCEL_TYPESAFE_API_KEY uses Vercel AI Gateway; otherwise TYPESAFE_API_KEY uses TypeSafe.
echo Nothing needs to be typed or pasted in this window.
echo If .env has no key yet, Notepad opens: paste the key after VERCEL_TYPESAFE_API_KEY= then save and close Notepad.
echo The check and subsequent scanner startup each make a small API inference and may use credits.
".\.venv\Scripts\python.exe" -m hoodscout.cli jev-setup
if errorlevel 1 goto failed
set "DECISION_PROVIDER=jev"
set "TYPESAFE_API_KEY="

echo Starting the PAPER dashboard with JEV. Keep this window open.
echo The browser opens after the updated application responds.
powershell.exe -NoLogo -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; $python=(Resolve-Path '.\.venv\Scripts\python.exe').Path; $p=Start-Process -FilePath $python -ArgumentList @('-m','hoodscout.cli','serve') -NoNewWindow -PassThru; $ready=$false; for ($i=0; $i -lt 30; $i++) { if ($p.HasExited) { throw 'HoodScout stopped before startup. Check the server error above, including whether port 8000 is already in use.' }; try { $s=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/state' -TimeoutSec 1; if ($s.mode -eq 'PAPER' -and $s.pool_discovery_topic_version -eq 2 -and $s.live_execution_available -eq $false -and $s.decision_provider -eq 'jev') { $ready=$true; break } } catch {}; Start-Sleep -Seconds 1 }; if ($ready) { Start-Process 'http://127.0.0.1:8000' } else { Write-Warning 'The dashboard did not respond within 30 seconds. Inspect the server output; the process remains running.' }; $p.WaitForExit(); exit $p.ExitCode"
if errorlevel 1 goto failed
popd
echo HoodScout stopped.
pause
exit /b 0

:missing_project
echo Could not find the project and its Python virtual environment at:
echo %HOODSCOUT_PROJECT%
echo Drag the actual hoodscout-robinhood project folder onto this CMD file.
pause
exit /b 1

:failed
echo.
echo UPDATE OR STARTUP STOPPED. Read the error above.
echo No real transactions have been enabled. Do not delete your database or .env.
if defined HOODSCOUT_PUSHED popd
pause
exit /b 1
