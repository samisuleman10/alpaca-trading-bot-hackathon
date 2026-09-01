@echo off
REM Start the live trader for today's session.
REM
REM One file so there is no shell syntax to get wrong at half past three.
REM Run it from anywhere, in PowerShell or cmd, by its full path.
REM
REM It clears ALPACA_API_KEY and ALPACA_SECRET_KEY for its own run. Those
REM variables override the CLI profile silently on every command, so a stale
REM one left in a terminal points the whole session at the wrong account while
REM everything still looks healthy. The trader also refuses to start on an
REM unexpected account, so this is the belt and the doctor is the braces.
REM
REM Anything you type after the script name is passed straight through, so
REM  trade.cmd --dry-run       rehearses without sending a single order.

set "REPO=C:\Users\samip\Desktop\Sami\Projects\Python\alpaca-options-agent"
set "PY=C:\Users\samip\AppData\Local\Programs\Python\Python39\python.exe"

cd /d "%REPO%"
set "PYTHONPATH=%REPO%\src"
set "ALPACA_API_KEY="
set "ALPACA_SECRET_KEY="

echo Starting the trader. Leave this window open until 22:00. Ctrl+C stops it.
echo.
"%PY%" -m agent.live --journal journal --quarter-size %*
echo.
echo Session ended with code %ERRORLEVEL%. The journal is in %REPO%\journal.
pause
