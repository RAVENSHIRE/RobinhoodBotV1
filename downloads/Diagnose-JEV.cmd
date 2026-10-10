@echo off
setlocal EnableExtensions DisableDelayedExpansion
title HoodScout - JEV Authentication Diagnosis
set "HOODSCOUT_PROJECT=%~1"
if not defined HOODSCOUT_PROJECT set "HOODSCOUT_PROJECT=%USERPROFILE%\Desktop\dev\hoodscout-robinhood"
if not exist "%HOODSCOUT_PROJECT%\.venv\Scripts\python.exe" goto missing
if not exist "%~dp0diagnose_jev.py" goto extract
echo Reading the existing project .env without asking for your key.
echo This checks the model list and makes one small synthetic inference, which may use credits.
echo It does not start HoodScout, change project files, enable JEV or send trades.
echo Keep the output below for diagnosis. It contains no API key values.
"%HOODSCOUT_PROJECT%\.venv\Scripts\python.exe" "%~dp0diagnose_jev.py" --project "%HOODSCOUT_PROJECT%"
set "HOODSCOUT_DIAG_EXIT=%ERRORLEVEL%"
echo.
echo Check finished. A nonzero result means JEV is still unverified.
pause
exit /b %HOODSCOUT_DIAG_EXIT%
:missing
echo Project virtual environment not found. Drag your project folder onto this CMD file.
pause
exit /b 1
:extract
echo Extract All from the ZIP first. Keep diagnose_jev.py beside this CMD file.
pause
exit /b 1
