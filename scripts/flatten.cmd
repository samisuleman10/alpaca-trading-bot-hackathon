@echo off
REM Close every option position, then confirm the account is empty.
REM
REM Scheduled for 21:50 local time, which is 15:50 in New York -- ten minutes
REM before the closing bell. A call option left to expire while it is worth
REM something does not quietly disappear: it is exercised, and becomes 100
REM real shares per contract. At SPY's price that is roughly $77,000 of stock
REM per contract, bought with money the account never budgeted for.
REM
REM Exit code 0 means the account is confirmed flat. 1 means it is not, and
REM that needs a person.
REM
REM Note for after 25 October: Europe leaves summer time a week before the US
REM does, so the New York offset changes from 6 hours to 5 and this task must
REM move to 20:50. It does not matter before then.

set "REPO=C:\Users\samip\Desktop\Sami\Projects\Python\alpaca-options-agent"
set "PY=C:\Users\samip\AppData\Local\Programs\Python\Python39\python.exe"

cd /d "%REPO%"
set "PYTHONPATH=%REPO%\src"
"%PY%" -m agent.flattener --journal journal >> "%REPO%\journal\flattener.log" 2>&1
exit /b %ERRORLEVEL%
