@echo off
rem skyguard.cmd -- the cmd twin of tools/skyguard.sh:  tools\skyguard.cmd lock status
rem Python: %SKYGUARD_PYTHON%, else the one setup.sh recorded in skyrim-paths.env, else py -3, else python. For the lock without Python: tools\skyguard-unlock.cmd
setlocal EnableExtensions DisableDelayedExpansion
set "HERE=%~dp0"
if defined SKYGUARD_PYTHON goto custom
rem No %SKYGUARD_PYTHON%: the interpreter setup.sh recorded in ..\skyrim-paths.env (Plan 8 T7), only when it exists.
set "SG_FILE_PY="
if exist "%HERE%..\skyrim-paths.env" for /f "usebackq tokens=1,* delims==" %%a in ("%HERE%..\skyrim-paths.env") do if "%%a"=="SKYGUARD_PYTHON" set "SG_FILE_PY=%%b"
if not defined SG_FILE_PY goto probe
if "%SG_FILE_PY:~0,1%%SG_FILE_PY:~-1%"=="''" set "SG_FILE_PY=%SG_FILE_PY:~1,-1%"
if defined SG_FILE_PY set "SG_FILE_PY=%SG_FILE_PY:'\''='%"
if defined SG_FILE_PY if exist "%SG_FILE_PY%" set "SKYGUARD_PYTHON=%SG_FILE_PY%"
if defined SKYGUARD_PYTHON goto custom
:probe
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
