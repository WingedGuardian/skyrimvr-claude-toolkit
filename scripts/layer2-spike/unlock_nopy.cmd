@echo off
rem Plan 6 Task 3 U2: unlock with NO Python -- cmd + attrib + icacls only.
rem   unlock_nopy.cmd <file> [<file> ...]
rem Order matters: the deny ACE goes first (with WA denied, attrib -R is itself refused), then the bit.
rem Spike guard: refuses any path not under C:\Temp\skyguard-ml2\ (string check; the Python
rem driver has already sandbox-asserted the RESOLVED path before calling this).
setlocal EnableDelayedExpansion
for /f "tokens=2 delims=," %%a in ('%SystemRoot%\System32\whoami.exe /user /fo csv /nh') do set "SID=%%~a"
if not defined SID (echo could not read the user SID & exit /b 2)
set rc=0
:loop
if "%~1"=="" goto done
set "F=%~f1"
if /i not "!F:~0,21!"=="C:\Temp\skyguard-ml2\" (echo REFUSED outside scratch: !F! & set rc=3 & shift & goto loop)
%SystemRoot%\System32\icacls.exe "!F!" /remove:d *!SID! >nul
%SystemRoot%\System32\attrib.exe -R "!F!"
echo unlocked !F!
shift
goto loop
:done
exit /b %rc%
