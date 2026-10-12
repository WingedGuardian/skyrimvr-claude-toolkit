# skyguard-entry.ps1 -- the fail-closed Windows entry for the toolkit's Codex hooks.
#
#   pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "<root>/.codex/hooks/skyguard-entry.ps1" <role>
#     || powershell <same>            (.codex/hooks.json commandWindows; the payload arrives on stdin)
#
# <role> is pre-tool or session-start. The rules live in tools/skyguard/ (Python); this file only
# carries the payload to the engine and the engine's answer back -- and owns every failure.
#
# Why it exists (MEASURED, codex-cli 0.160.0): Codex blocks a tool call on exactly ONE thing, a parsed
# JSON permissionDecision "deny" with exit 0. "ask", exit 1, exit 2, a crash, bad JSON, a timeout and a
# missing script all let the command RUN. So every path out of this file is either the engine's own
# validated answer or a deny (pre-tool) / a NOT LIVE line (session-start), and it always exits 0.
# The `||` in the definition reruns this file under Windows PowerShell 5.1 with an EMPTY stdin when pwsh
# exits non-zero (MEASURED); an empty payload is therefore a deny, never a silent allow.
#
# Runs on Windows PowerShell 5.1 and PowerShell 7: ASCII only (5.1 reads a BOM-less file as ANSI), no
# cmdlets on the hot path (PowerShell 7 auto-loads a module on a cmdlet's first use, which costs time on
# every call), and nothing may reach the output stream except through Send-Text: Invoke-Main's own
# output is discarded. Never logs the environment: hooks inherit all of it, secrets included (MEASURED).
#
# SETUP: setup.sh fills in the two paths below.
# Test-only knobs: SKYGUARD_WRAPPER_TIMEOUT_S (the engine deadline; default 40 pre-tool, 95 session-start,
# both below the definition timeouts 60/120 so this file, not Codex, decides), SKYGUARD_NO_PYTHON_FALLBACK=1
# (configured interpreter only, so a missing interpreter is really missing).

$ErrorActionPreference = 'Stop'
$PY = '{{PYTHON_PATH}}'
$BASHEXE = '{{BASH_PATH}}'

$script:Role = ''
if ($args.Count -ge 1) { $script:Role = [string]$args[0] }
$script:Known = ($script:Role -ceq 'pre-tool') -or ($script:Role -ceq 'session-start')
$script:Emitted = $false
$script:T0ms = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$script:T0 = ''
$script:Inv = ''
$script:HB = ''
$script:Utf8 = [System.Text.UTF8Encoding]::new($false, $false)
$script:IsWin = [System.IO.Path]::DirectorySeparatorChar -eq [char]92

function Get-Stamp([long]$ms) {
    return ([double]$ms / 1000).ToString('0.000', [System.Globalization.CultureInfo]::InvariantCulture)
}

function Get-ElapsedS { return [int](([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $script:T0ms) / 1000) }

function Send-Text([string]$text) {
    # The ONE way out. Raw UTF-8 bytes (no BOM, no console code page), once.
    if ($script:Emitted) { return }
    $script:Emitted = $true
    if ($text.Length -eq 0) { return }
    $b = $script:Utf8.GetBytes($text + "`n")
    $s = [Console]::OpenStandardOutput()
    $s.Write($b, 0, $b.Length)
    $s.Flush()
}

function Write-Record([string]$stage, [string]$rest) {
    # Telemetry (tools/skyguard/health.py): "<t> <inv> <rest>". A telemetry failure never changes the answer.
    if (-not $script:HB -or -not $script:Inv) { return }
    try {
        $line = (Get-Stamp ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())) + ' ' + $script:Inv
        if ($rest) { $line += ' ' + ($rest -replace '\s+', ' ') }
        [System.IO.File]::WriteAllText($script:HB + '/' + $script:Role + '.' + $stage, $line + "`n")
    } catch { }
}

function Send-Failure([string]$stage, [string]$cause) {
    # The cause goes into a JSON string literal: an allowlist, never escaping by hand.
    $safe = [regex]::Replace([string]$cause, '[^A-Za-z0-9 ._:/()=,+-]', '')
    if ($safe.Length -gt 300) { $safe = $safe.Substring(0, 300) }
    Write-Record 'failed' ($stage + ' ' + $safe)
    if ($script:Role -ceq 'session-start') {
        Send-Text ('SKYGUARD: NOT LIVE -- the Codex session hook could not get a result from the toolkit engine (' + $safe + '). The toolkit guards may not be running: tell the user, and ask them to run: bash tools/hook-canary.sh')
    } else {
        Send-Text ('{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"GUARD INERT: the Codex hook could not get a verdict from the toolkit engine (' + $safe + '). NOTHING was checked: this is a refusal, not a decision. Tell the user to run: bash tools/hook-canary.sh"}}')
    }
}

function Join-Args([string[]]$list) {
    # Quote only what needs it: py.exe reads its RAW command line, and a quoted "-3" is not its version
    # switch (X4 MEASURED). No argument here ends in a backslash (forward slashes throughout).
    $parts = foreach ($a in $list) {
        $x = ([string]$a).Replace('"', '')
        if ($x.Length -eq 0) { '""' } elseif ($x -match '\s') { '"' + $x + '"' } else { $x }
    }
    return ($parts -join ' ')
}

function Get-Tail([string[]]$list) {
    # every element but the first, as an array (no cmdlet on the hot path)
    $t = @()
    for ($i = 1; $i -lt $list.Length; $i++) { $t += $list[$i] }
    return , $t
}

function Stop-Tree($proc) {
    if ($script:IsWin) {
        try {
            $k = [System.Diagnostics.ProcessStartInfo]::new()
            $k.FileName = 'taskkill.exe'
            $k.Arguments = '/T /F /PID ' + $proc.Id
            $k.UseShellExecute = $false
            $k.CreateNoWindow = $true
            $k.RedirectStandardOutput = $true
            $k.RedirectStandardError = $true
            $t = [System.Diagnostics.Process]::Start($k)
            [void]$t.WaitForExit(5000)
        } catch { }
    }
    try { $proc.Kill() } catch { }
}

function Invoke-Proc([string[]]$cmd, [string[]]$more, [byte[]]$in, [int]$ms) {
    # Runs cmd+more with `in` on stdin; kills the whole tree when `ms` passes. -> @{Code; Out; TimedOut}
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $cmd[0]
    $psi.Arguments = Join-Args ((Get-Tail $cmd) + $more)
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $outMs = [System.IO.MemoryStream]::new()
    # Raw bytes, not a StreamReader: a reader would silently drop a BOM the engine must not print.
    $outTask = $proc.StandardOutput.BaseStream.CopyToAsync($outMs)
    $errTask = $proc.StandardError.BaseStream.CopyToAsync([System.IO.Stream]::Null)
    $deadline = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() + $ms
    $timedOut = $false
    try {
        # Asynchronously, under the deadline: a child that never reads would otherwise block this write.
        $w = $proc.StandardInput.BaseStream.WriteAsync($in, 0, $in.Length)
        if (-not $w.Wait([Math]::Max(1, $ms))) { $timedOut = $true }
        else { $proc.StandardInput.Close() }
    } catch {
        # the child exited without reading all of it: its exit code decides below
    }
    if (-not $timedOut) {
        $left = [int][Math]::Max(1, $deadline - [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
        if (-not $proc.WaitForExit($left)) { $timedOut = $true }
    }
    if ($timedOut) {
        Stop-Tree $proc
        return @{ Code = -1; Out = ''; TimedOut = $true }
    }
    $proc.WaitForExit()
    # A grandchild holding the pipe open must not hold this file past its deadline either.
    $left = [int][Math]::Max(1000, $deadline - [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
    if (-not $outTask.Wait($left)) { Stop-Tree $proc; return @{ Code = -1; Out = ''; TimedOut = $true } }
    return @{ Code = $proc.ExitCode; Out = $script:Utf8.GetString($outMs.ToArray()); TimedOut = $false }
}

# PY-CHAIN-BEGIN
# Interpreter fallback chain -- the same candidates in the same order as the Claude wrappers
# (tests/test_codex_entry.py::test_fallback_chain_matches_the_claude_wrappers). The configured $PY is the
# only thing the hot path touches; probing runs ONLY once it has failed. Order: py -3 / python / python3
# that resolve and are not a Store alias, then the usual install locations (newest version first), then a
# Store alias, last. A candidate is accepted only if it is Python >= 3.8 AND runs the engine's own
# `run.py --selftest <nonce>` and echoes the nonce (a stub printing {} would otherwise read as ALLOW).
# Each probe gets min(SKYGUARD_PROBE_TIMEOUT, the time left), default 15 s, sub-second allowed, and no probe
# runs in the last 10 s before the wrapper's own limit (the engine needs them) -- the Claude chain's budget.
# It was 3 s per probe and 8 s in all: below a HEALTHY selftest MEASURED at up to 3.8 s native and ~9 s
# through a shim at 2x load (protect-bash.sh), and windows-latest's PowerShell legs refused a working Python
# that way (CI runs 38178385999, 38201545601). Every probe's outcome and duration goes to <role>.probes.
# A fallback that is taken writes a `failed` record BEFORE the engine runs, so the health report says
# DEGRADED until the configured one works again.
$script:PyCandidates = @(@('py', '-3'), @('python'), @('python3'))
$script:PyVersions = 30..8
$script:PyPosix = @('/usr/bin/python3', '/usr/local/bin/python3')
$script:FB = $false
$script:ProbeMs = [long]15000
$script:ProbeEndMs = $script:T0ms + 30000          # Invoke-Main sets it from the wrapper's limit
$script:Probes = @()
$script:Probed = $false                            # a fallback ran: only then is <role>.probes written
try {
    if ($env:SKYGUARD_PROBE_TIMEOUT) {
        $v = [double]::Parse($env:SKYGUARD_PROBE_TIMEOUT, [System.Globalization.CultureInfo]::InvariantCulture)
        if ($v -gt 0) { $script:ProbeMs = [long]($v * 1000) }
    }
} catch { }
function Get-ProbeMs {
    # the probe's budget: the configured one, never past the end of probing; 0 = no time left to start one
    $left = $script:ProbeEndMs - [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    if ($left -lt 200) { return 0 }
    return [int][Math]::Min($script:ProbeMs, $left)
}
function Add-Stage([string]$what) {
    # where the call is, in ms since the entry started -- written with the probes (CI run 38277736359: ~24 s went
    # by between the start and the first probe, and nothing said where)
    $script:Probes += ($what + ' at ' + ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $script:T0ms) + 'ms')
}
function Find-OnPath([string]$name) {
    # PATH x PATHEXT by hand, in Get-Command's order (each directory in turn, PATHEXT order within it). NOT
    # Get-Command: on windows-latest Windows PowerShell 5.1 every fallback call that looked an absent name up spent
    # ~22-25 s before its first probe (CI runs 38201545601, 38277736359: every 5.1 test reaching this chain; pwsh
    # and the calls that never reach it took ~1-4 s), which used up the budget meant for the probes themselves.
    $exts = @('')
    if ($script:IsWin) {
        $exts = @()
        foreach ($e in ([string]$env:PATHEXT).Split(';')) { if ($e) { $exts += $e } }
        if ($exts.Count -eq 0) { $exts = @('.COM', '.EXE', '.BAT', '.CMD') }
    }
    foreach ($d in ([string]$env:PATH).Split([System.IO.Path]::PathSeparator)) {
        $d = $d.Trim().Trim('"')
        if (-not $d) { continue }
        foreach ($e in $exts) {
            try { $f = [System.IO.Path]::Combine($d, $name + $e) } catch { continue }
            if ([System.IO.File]::Exists($f)) { return $f }
        }
    }
    return $null
}
function Add-Probe([string[]]$c, [string]$what, [long]$t0) {
    $script:Probes += (($c -join ' ') + ' ' + $what + ' ' + ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $t0) + 'ms')
}
function Test-PyGood([string[]]$c) {
    $t0 = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $ms = Get-ProbeMs
    if ($ms -le 0) { Add-Probe $c 'skipped:no-time-left' $t0; return $false }
    try {
        $r = Invoke-Proc $c @('-c', 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)') ([byte[]]@()) $ms
    } catch { Add-Probe $c 'rejected:cannot-start' $t0; return $false }
    if ($r.TimedOut) { Add-Probe $c ('rejected:version-check-timeout-' + $ms + 'ms') $t0; return $false }
    if ($r.Code -ne 0) { Add-Probe $c ('rejected:version-check-exit-' + $r.Code) $t0; return $false }
    $ms = Get-ProbeMs
    if ($ms -le 0) { Add-Probe $c 'rejected:no-time-left-for-selftest' $t0; return $false }
    $n = [string][System.Random]::new().Next(100000, 999999999)
    try {
        $r = Invoke-Proc $c @($script:RunPy, '--selftest', $n) ([byte[]]@()) $ms
    } catch { Add-Probe $c 'rejected:selftest-cannot-start' $t0; return $false }
    if ($r.TimedOut) { Add-Probe $c ('rejected:selftest-timeout-' + $ms + 'ms') $t0; return $false }
    if (-not ($r.Code -eq 0 -and $r.Out.Replace("`r", '').TrimEnd("`n") -ceq ('skyguard-selftest:' + $n))) {
        Add-Probe $c ('rejected:selftest-exit-' + $r.Code + '-or-no-nonce') $t0; return $false
    }
    Add-Probe $c 'accepted' $t0
    return $true
}
function Use-Py([string[]]$c) {
    $script:FB = $true
    Write-Record 'failed' ('python configured ' + $PY + ' unusable; used ' + ($c -join ' ') + ' -- re-run setup.sh')
    return $c
}
function Find-PyFallback {
    $alias = $null
    $script:Probed = $true
    Add-Stage 'fallback-start'
    foreach ($cand in $script:PyCandidates) {
        $t0 = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        $found = Find-OnPath $cand[0]
        $script:Probes += ('lookup ' + $cand[0] + ' ' + $(if ($null -eq $found) { 'absent' } else { 'found' }) + ' ' +
                           ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $t0) + 'ms')
        if ($null -eq $found) { continue }
        $c = @([string]$found) + (Get-Tail $cand)
        if ($c[0] -match '[\\/][Ww]indows[Aa]pps[\\/]') { if ($null -eq $alias) { $alias = $c }; continue }
        if (Test-PyGood $c) { return (Use-Py $c) }
    }
    $bases = @()
    if ($env:LOCALAPPDATA) { $bases += ($env:LOCALAPPDATA.Replace([string][char]92, '/') + '/Programs/Python') }
    if ($env:ProgramFiles) { $bases += $env:ProgramFiles.Replace([string][char]92, '/') }
    $bases += 'C:'
    foreach ($m in $script:PyVersions) {
        foreach ($d in $bases) {
            $p = $d + '/Python3' + $m + '/python.exe'
            if ([System.IO.File]::Exists($p) -and (Test-PyGood @($p))) { return (Use-Py @($p)) }
        }
    }
    # SKYGUARD_NO_SYSTEM_PYTHON=1 (test knob) skips the fixed POSIX locations, as the bash chain does.
    if ($env:SKYGUARD_NO_SYSTEM_PYTHON -ne '1') {
        foreach ($p in $script:PyPosix) {
            if ([System.IO.File]::Exists($p) -and (Test-PyGood @($p))) { return (Use-Py @($p)) }
        }
    }
    if ($null -ne $alias -and (Test-PyGood $alias)) { return (Use-Py $alias) }
    return $null
}
# PY-CHAIN-END

# The two answers Codex may receive from a pre-tool call: a deny, or context (Codex fails OPEN on "ask"
# and "allow"). The engine prints json.dumps of one of them; anything else is a deny here. Checked with a
# STRICT grammar, not ConvertFrom-Json: PowerShell's parsers accept JSON that Codex's (serde) rejects --
# single quotes, comments, duplicate keys -- and a body Codex cannot parse lets the command RUN.
$script:JStr = '"(?:[^"\\\x00-\x1f]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*"'
$script:JPair = $script:JStr + '[ \t]*:[ \t]*' + $script:JStr
$script:JBody = '\A\{[ \t]*"hookSpecificOutput"[ \t]*:[ \t]*\{[ \t]*(' + $script:JPair + '(?:[ \t]*,[ \t]*' + $script:JPair + ')*)[ \t]*\}[ \t]*\}\z'
$script:JKeys = @('"hookEventName"', '"permissionDecision"', '"permissionDecisionReason"', '"additionalContext"')

function Test-Body([string]$body) {
    $m = [regex]::Match($body, $script:JBody)
    if (-not $m.Success) { return 'engine output is not a verdict JSON' }
    # An escape starts at an ODD run of backslashes: `\\ud83d` is an escaped backslash and the text "ud83d".
    if ($body -match '(?<!\\)(?:\\\\)*\\u[dD][89abAB]') { return 'engine output has a surrogate escape' }
    $seen = @{}
    foreach ($p in [regex]::Matches($m.Groups[1].Value, '(' + $script:JStr + ')[ \t]*:[ \t]*(' + $script:JStr + ')')) {
        $k = $p.Groups[1].Value
        $v = $p.Groups[2].Value
        if ($script:JKeys -cnotcontains $k) { return 'engine output has an unknown key' }
        if ($seen.ContainsKey($k)) { return 'engine output repeats a key' }
        $seen[$k] = $true
        if ($k -ceq '"permissionDecision"' -and $v -cne '"deny"') { return 'engine answered a permissionDecision other than deny' }
        if ($k -ceq '"hookEventName"' -and $v -cne '"PreToolUse"') { return 'engine output names another event' }
    }
    if (-not $seen.ContainsKey('"hookEventName"')) { return 'engine output names no event' }
    return $null
}

function Invoke-Main {
    $stdin = [Console]::OpenStandardInput()
    $buf = [System.IO.MemoryStream]::new()
    $stdin.CopyTo($buf)
    $payload = $buf.ToArray()
    Add-Stage 'stdin-read'

    if (-not $script:Known) { Send-Failure 'role' 'unknown role'; return }

    # Where the install is: two levels above this file (.codex/hooks/), forward slashes.
    $root = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($PSScriptRoot, '..', '..')).Replace([string][char]92, '/')
    if ($root.Length -gt 3) { $root = $root.TrimEnd('/') }
    $script:RunPy = $root + '/tools/skyguard/run.py'

    # Telemetry: invoked -> received -> (engine: evaluated) -> emitted; failed from any stage.
    $hb = $root + '/.claude/backups/.hook-heartbeat/codex'
    try { [void][System.IO.Directory]::CreateDirectory($hb); $script:HB = $hb } catch { }
    $script:T0 = Get-Stamp $script:T0ms
    $prev = @{}
    foreach ($st in @('invoked', 'emitted', 'failed')) {
        $prev[$st] = @()
        try {
            $f = $hb + '/' + $script:Role + '.' + $st
            if ([System.IO.File]::Exists($f)) { $prev[$st] = @(([System.IO.File]::ReadAllText($f).Trim()) -split '\s+') }
        } catch { }
    }
    # `<seq>.<pid>-<rand>`: health.py orders a failure against a completion by <seq>, not by the clock.
    $seq = 0
    if ($prev['invoked'].Count -ge 2 -and $prev['invoked'][1] -match '\A([0-9]{1,15})\.') { $seq = [long]$Matches[1] }
    $script:Inv = '' + ($seq + 1) + '.' + $PID + '-' + [System.Random]::new().Next(0, 32768)
    # A previous call that never emitted, never failed, and is older than any hook timeout was killed.
    $grace = 65
    if ($script:Role -ceq 'session-start') { $grace = 125 }
    if ($prev['invoked'].Count -ge 2) {
        $oinv = $prev['invoked'][1]
        $einv = if ($prev['emitted'].Count -ge 2) { $prev['emitted'][1] } else { '' }
        $finv = if ($prev['failed'].Count -ge 2) { $prev['failed'][1] } else { '' }
        $ot = 0.0
        if ([double]::TryParse($prev['invoked'][0], [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$ot)) {
            if ($oinv -cne $einv -and $oinv -cne $finv -and (($script:T0ms / 1000) - $ot) -gt $grace) {
                Write-Record 'failed' ('killed run ' + $oinv + ' invoked at ' + $prev['invoked'][0] + ' never finished')
            }
        }
    }
    try { [System.IO.File]::WriteAllText($hb + '/' + $script:Role + '.invoked', $script:T0 + ' ' + $script:Inv + "`n") } catch { }

    if ($payload.Length -eq 0) { Send-Failure 'receipt' 'NO PAYLOAD on stdin'; return }
    try { [System.IO.File]::WriteAllText($hb + '/' + $script:Role + '.received', $script:T0 + ' ' + $script:Inv + ' bytes=' + $payload.Length + "`n") } catch { }

    if (-not [System.IO.File]::Exists($script:RunPy)) { Send-Failure 'engine' 'engine missing (tools/skyguard/run.py)'; return }

    # 20+ s under the definition timeouts (60/120): ~5.5 s MEASURED past the deadline (taskkill wait +
    # drain), two PowerShell starts (pwsh, then the `||` rerun under 5.1) and the rerun's own answer all
    # fit before Codex gives up -- and Codex RUNS the command once its timeout passes.
    $limit = 40
    if ($script:Role -ceq 'session-start') { $limit = 95 }
    if ($env:SKYGUARD_WRAPPER_TIMEOUT_S) { $limit = [int]::Parse($env:SKYGUARD_WRAPPER_TIMEOUT_S, [System.Globalization.CultureInfo]::InvariantCulture) }
    $script:ProbeEndMs = $script:T0ms + ([long]$limit - 10) * 1000

    $bashArg = $BASHEXE
    if ($bashArg.StartsWith('{{')) { $bashArg = 'bash' }
    $engineArgs = @($script:RunPy, 'codex', $script:Role, '--root', $root, '--project', $root, '--bash', $bashArg,
                    '--invocation', $script:Inv, '--telemetry', $hb)

    # The configured interpreter, else the chain (unless the test knob forbids it).
    $nofb = ($env:SKYGUARD_NO_PYTHON_FALLBACK -eq '1')
    Add-Stage 'configured-python-check'
    $pyc = $null
    if (-not $PY.StartsWith('{{') -and [System.IO.File]::Exists($PY)) { $pyc = @($PY) }
    if ($null -eq $pyc -and -not $nofb) { $pyc = Find-PyFallback }
    if ($script:Probed) { Write-Record 'probes' ($script:Probes -join '; ') }
    if ($null -eq $pyc) { Send-Failure 'python' 'cannot run Python -- install Python 3 and run setup.sh'; return }

    $left = [long]$limit * 1000 - ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $script:T0ms)
    $r = Invoke-Proc $pyc $engineArgs $payload ([int][Math]::Max(1000, $left))
    if ($r.TimedOut) { Send-Failure 'engine' ('engine did not answer within ' + $limit + ' s'); return }
    if ($r.Code -ne 0 -and -not $script:FB -and -not $nofb -and -not (Test-PyGood $pyc)) {
        $alt = Find-PyFallback
        Write-Record 'probes' ($script:Probes -join '; ')
        if ($null -ne $alt) {
            $pyc = $alt
            $left = [long]$limit * 1000 - ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - $script:T0ms)
            $r = Invoke-Proc $pyc $engineArgs $payload ([int][Math]::Max(1000, $left))
            if ($r.TimedOut) { Send-Failure 'engine' ('engine did not answer within ' + $limit + ' s'); return }
        }
    }
    if ($r.Code -ne 0) { Send-Failure 'engine' ('engine exited ' + $r.Code); return }

    $text = $r.Out.Replace("`r", '')
    if ($text -cne 'SKYGUARD-OK' -and -not $text.StartsWith("SKYGUARD-OK`n", [System.StringComparison]::Ordinal)) {
        Send-Failure 'emission' 'engine output lacks the SKYGUARD-OK line'; return
    }
    $body = ''
    if ($text.Length -gt 12) { $body = $text.Substring(12).TrimEnd("`n") }
    if ($script:Role -ceq 'session-start') {
        if (-not $body.StartsWith('SKYGUARD: ', [System.StringComparison]::Ordinal)) {
            Send-Failure 'emission' 'engine session text lacks the SKYGUARD banner'; return
        }
    } elseif ($body.Length -gt 0) {
        if ($body.Contains("`n")) { Send-Failure 'emission' 'engine printed more than one body line'; return }
        $bad = Test-Body $body
        if ($bad) { Send-Failure 'emission' $bad; return }
    }
    Send-Text $body
    Write-Record 'emitted' 'ok'
    try {
        $t1 = Get-Stamp ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
        [System.IO.File]::AppendAllText($hb + '/' + $script:Role + '.latency', $script:Inv + ' ' + $script:T0 + ' ' + $t1 + "`n")
    } catch { }
}

try {
    $null = Invoke-Main
} catch {
    Send-Failure 'wrapper' ('wrapper error: ' + $_.Exception.GetType().Name)
} finally {
    if (-not $script:Emitted) { Send-Failure 'wrapper' 'the wrapper ended without a verdict' }
}
exit 0
