# Wrapper failures produce an explicit deny: hook execution errors can fail open.
try {
    $ErrorActionPreference = 'Stop'
    $inputJson = [Console]::In.ReadToEnd()
    $guardScript = Join-Path $PSScriptRoot 'protect-save-access.py'
    if ($guardScript.Contains('"')) { throw 'Invalid guard script path.' }
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = 'C:/Tools/Python/python.exe'
    $start.Arguments = '-B "' + $guardScript + '"'
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $child = New-Object System.Diagnostics.Process
    $child.StartInfo = $start
    if (-not $child.Start()) { throw 'Save guard interpreter did not start.' }
    try {
        $write = $child.StandardInput.WriteAsync($inputJson)
        if (-not $write.Wait(2000)) { throw 'Save guard input timed out.' }
        $child.StandardInput.Close()
        # Return an explicit deny before the hosting hook's30-second timeout.
        # Hosting timeout can fail open; the child must never reach that limit.
        if (-not $child.WaitForExit(20000)) { throw 'Save guard interpreter timed out.' }
        if ($child.ExitCode -ne 0) { throw 'Save guard interpreter failed.' }
        $result = $child.StandardOutput.ReadToEnd()
    } finally {
        if (-not $child.HasExited) {
            $child.Kill()
            $null = $child.WaitForExit(2000)
        }
        $child.Dispose()
    }
    if ($null -ne $result -and "$result".Trim()) {
        $null = "$result" | ConvertFrom-Json -ErrorAction Stop
        Write-Output $result
    }
} catch {
    Write-Output '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"SAVE ACCESS BLOCKED: save guard could not run or returned an invalid decision. Repair the hook; do not bypass it."}}'
}
exit 0
