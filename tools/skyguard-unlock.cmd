@echo off
rem skyguard-unlock.cmd -- undo the Layer 2 lock with NOTHING but cmd, attrib, icacls, findstr and dir.
rem Double-click it, or run it from a prompt. It reads ..\.skyguard\locked.txt (one full path per line, written
rem by "skyguard lock lock"); if that list is missing it tries the fixed list of vanilla file names in ..\Data.
rem
rem It only ever acts on a file that (1) is directly inside ..\Data, (2) has one of the vanilla names the lock
rem covers (the fixed list below, or a Creation Club plugin/archive: cc*.esm .esl .esp .bsa) and (3) carries the
rem toolkit's deny mark. Every other line is REFUSED and the script exits non-zero. Nothing is trusted from the list
rem except the file name.
rem
rem Per file, in this order (with write-attributes denied, attrib -R is itself refused, so the ACE goes first):
rem   1. icacls <file> /remove:d *<your SID>   -- removes EVERY explicit deny entry for YOUR ACCOUNT on that file.
rem      (The lock never locks a file that already has a deny entry of yours, so on a locked file that is only
rem      the toolkit's.) Other accounts' entries and all allow entries are left alone.
rem   2. attrib -R <file>                       -- unless the file was ALREADY read-only before the lock: those carry
rem      the tag (DENY)(WD,AD,WA) instead of (DENY)(WD,AD,WEA,WA), and keep their read-only bit.
rem   3. It re-reads the file: the toolkit's deny entry must be gone and (unless tagged) the bit must be clear.
rem      Anything else prints "NOT unlocked" and the exit code is 4. attrib exits 0 even when it was denied, so
rem      the exit codes of icacls/attrib are not trusted; the re-read is.
rem Exit codes: 0 all done, 1 no list and nothing found, 2 cannot read your user id, 3 a line was refused, 4 a file
rem was not unlocked.
rem Last resort by hand, for one file F:  icacls "F" /remove:d *<your SID>  then  attrib -R "F"
rem   (your SID: run  whoami /user  and copy the S-1-5-21-... value).
rem Not supported: an install path containing a percent sign. SKYGUARD_NO_PAUSE=1 never pauses (it pauses only
rem when started by a double-click). SKYGUARD_LOCK_SANDBOX (tests only): refuse any line not under that folder.
setlocal EnableExtensions DisableDelayedExpansion
set "S32=%SystemRoot%\System32"
set "RC=0"
set "N=0"
set "BADN=0"
set "SID="
for /f "tokens=2 delims=," %%a in ('"%S32%\whoami.exe" /user /fo csv /nh') do set "SID=%%~a"
if not defined SID goto nosid
for %%I in ("%~dp0..\Data\") do set "DATA=%%~fI"
for %%I in ("%~dp0..\.skyguard\locked.txt") do set "LIST=%%~fI"
set "SB="
set "L=0"
if not defined SKYGUARD_LOCK_SANDBOX goto lendone
set "SB=%SKYGUARD_LOCK_SANDBOX%\"
:len
call set "CH=%%SB:~%L%,1%%"
if not defined CH goto lendone
set /a L+=1
goto len
:lendone
set "FIXED="
if exist "%LIST%" goto fromlist
set "FIXED=1"
echo No list of locked files; trying the fixed vanilla names in "%DATA%".
call :enum :fix
goto summary
:fromlist
for /f "usebackq delims=" %%L in ("%LIST%") do call :one "%%L"
:summary
if "%N%%BADN%"=="00" if "%RC%"=="0" set "RC=1" & echo Nothing to unlock: no locked vanilla file was found.
if not "%N%%BADN%"=="00" echo Unlocked %N% file(s); %BADN% not unlocked.
goto finish

:nosid
echo Could not read your user id with whoami; nothing was changed.
set "RC=2"
goto finish

:enum
for %%N in ("Skyrim.esm" "Update.esm" "Dawnguard.esm" "HearthFires.esm" "Dragonborn.esm" "SkyrimVR.esm" "Skyrim_VR - Main.bsa" "_ResourcePack.esl" "_ResourcePack.bsa") do call %~1 "%%~N"
for %%N in (Animations Interface Meshes0 Meshes1 Misc Patch Shaders Sounds Textures0 Textures1 Textures2 Textures3 Textures4 Textures5 Textures6 Textures7 Textures8) do call %~1 "Skyrim - %%N.bsa"
for %%N in (en fr de it es ja pl ru cs) do call %~1 "Skyrim - Voices_%%N0.bsa"
exit /b 0

:chk
if /i "%BASE%"=="%~1" set "OK=1"
exit /b 0

:fix
call :one "%DATA%%~1"
exit /b 0

:split
set "FD=%~1"
set "BASE=%~2"
exit /b 0

:isr1
set "OK="
call :enum :chk
if defined OK exit /b 0
set "P2=%BASE:~0,2%"
set "EX=%BASE:~-4%"
if /i not "%P2%"=="cc" exit /b 0
if /i "%EX%"==".esm" set "OK=1"
if /i "%EX%"==".esl" set "OK=1"
if /i "%EX%"==".esp" set "OK=1"
if /i "%EX%"==".bsa" set "OK=1"
exit /b 0

:one
set "F=%~1"
if "%F%"=="" exit /b 0
echo "%F%"| "%S32%\findstr.exe" /r /c:"[*?<>|]" >nul
if not errorlevel 1 goto refuse
if not defined SB goto sbok
call set "HEAD=%%F:~0,%L%%%"
if /i not "%HEAD%"=="%SB%" goto refuse
:sbok
for %%I in ("%F%") do call :split "%%~dpI" "%%~nxI"
if /i not "%FD%"=="%DATA%" goto refuse
call :isr1
if not defined OK goto refuse
if not exist "%F%" goto missing
set "HASFULL="
set "PRE="
"%S32%\icacls.exe" "%F%" | "%S32%\findstr.exe" /l /c:"(DENY)(WD,AD,WEA,WA)" >nul
if not errorlevel 1 set "HASFULL=1"
"%S32%\icacls.exe" "%F%" | "%S32%\findstr.exe" /l /c:"(DENY)(WD,AD,WA)" >nul
if not errorlevel 1 set "PRE=1"
if not defined HASFULL if not defined PRE goto notours
"%S32%\icacls.exe" "%F%" /remove:d *%SID% >nul
if errorlevel 1 goto failed
if defined PRE goto verify
"%S32%\attrib.exe" -R "%F%"
:verify
set "BAD="
"%S32%\icacls.exe" "%F%" | "%S32%\findstr.exe" /l /c:"(DENY)(WD,AD" >nul
if not errorlevel 1 set "BAD=1"
if defined PRE goto bitok
dir /b /a:r "%F%" >nul 2>nul
if not errorlevel 1 set "BAD=1"
:bitok
if defined BAD goto failed
if defined PRE echo unlocked "%F%" - it was read-only before the lock, so it stays read-only
if not defined PRE echo unlocked "%F%"
set /a N+=1
exit /b 0

:failed
echo NOT unlocked "%F%" - the toolkit's mark or the read-only bit is still there; run: tools\skyguard.cmd lock status
set "RC=4"
set /a BADN+=1
exit /b 0

:notours
echo left alone, it does not carry the toolkit's mark: "%F%"
exit /b 0

:missing
if not defined FIXED echo skipped, not found: "%F%"
exit /b 0

:refuse
echo REFUSED, not a vanilla file inside Data: "%F%"
set "RC=3"
exit /b 0

:finish
if defined SKYGUARD_NO_PAUSE exit /b %RC%
echo %cmdcmdline%| "%S32%\findstr.exe" /r /c:"cmd\" \"$" >nul
if errorlevel 1 exit /b %RC%
pause
exit /b %RC%
