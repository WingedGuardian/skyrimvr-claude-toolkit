@echo off
rem skyguard.cmd -- the cmd twin of tools/skyguard.sh:  tools\skyguard.cmd lock status
rem Python: %SKYGUARD_PYTHON%, else py -3, else python. For the lock without Python: tools\skyguard-unlock.cmd
setlocal EnableExtensions
set "HERE=%~dp0"
if defined SKYGUARD_PYTHON goto custom
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepy
python -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepython
echo skyguard: no working Python found; for the lock, run tools\skyguard-unlock.cmd 1>&2
exit /b 2
:custom
"%SKYGUARD_PYTHON%" -B "%HERE%skyguard" %*
exit /b %errorlevel%
:usepy
py -3 -B "%HERE%skyguard" %*
exit /b %errorlevel%
:usepython
python -B "%HERE%skyguard" %*
exit /b %errorlevel%
