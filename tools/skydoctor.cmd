@echo off
rem skydoctor.cmd -- the cmd twin of tools/skydoctor:  tools\skydoctor.cmd [--root R] [--json] [--no-toolchain]
rem Exit 0 every line live, 1 any line not live, 2 any line unknown (or no Python: nothing could be checked).
rem Python: %SKYGUARD_PYTHON%, else py -3, else python. Layer 0 finds Git for Windows' bash by itself.
setlocal EnableExtensions
set "HERE=%~dp0"
if defined SKYGUARD_PYTHON goto custom
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepy
python -c "import sys" >nul 2>nul
if not errorlevel 1 goto usepython
echo skydoctor: no working Python found, so nothing could be checked; for the lock, run tools\skyguard-unlock.cmd 1>&2
exit /b 2
:custom
"%SKYGUARD_PYTHON%" -B "%HERE%skyguard\doctor.py" %*
exit /b %errorlevel%
:usepy
py -3 -B "%HERE%skyguard\doctor.py" %*
exit /b %errorlevel%
:usepython
python -B "%HERE%skyguard\doctor.py" %*
exit /b %errorlevel%
