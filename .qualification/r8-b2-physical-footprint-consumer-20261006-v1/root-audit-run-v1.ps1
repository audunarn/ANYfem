$ErrorActionPreference = 'Stop'
$taskRaw = $PSScriptRoot
$taskFolder = Join-Path $taskRaw 'root-audit-v2'
if (Test-Path -LiteralPath $taskFolder) { throw 'Unique audit folder already exists' }
[void](New-Item -ItemType Directory -Path $taskFolder)
$taskLedger = Get-Content -LiteralPath (Join-Path $taskRaw 'ledger.json') -Raw | ConvertFrom-Json
$taskCap = [Math]::Min(5.0, 600.0 - [double]$taskLedger.used_seconds)
if ($taskCap -le 0) { throw 'No remaining audit authority' }
$taskProcess = [System.Diagnostics.Process]::new()
$taskProcess.StartInfo.FileName = 'C:/Python/Python314/python.exe'
$taskProcess.StartInfo.ArgumentList.Add('-I')
$taskProcess.StartInfo.ArgumentList.Add('-S')
$taskProcess.StartInfo.ArgumentList.Add((Join-Path $taskRaw 'root-installed-audit-v1.py'))
$taskProcess.StartInfo.WorkingDirectory = $taskRaw
$taskProcess.StartInfo.UseShellExecute = $false
$taskProcess.StartInfo.CreateNoWindow = $true
$taskProcess.StartInfo.RedirectStandardOutput = $true
$taskProcess.StartInfo.RedirectStandardError = $true
$taskClock = [System.Diagnostics.Stopwatch]::StartNew()
[void]$taskProcess.Start()
$taskOutput = $taskProcess.StandardOutput.ReadToEndAsync()
$taskError = $taskProcess.StandardError.ReadToEndAsync()
$taskTimedOut = -not $taskProcess.WaitForExit([int]($taskCap * 1000))
if ($taskTimedOut) { $taskProcess.Kill($true); $taskProcess.WaitForExit() }
$taskClock.Stop()
$taskElapsed = $taskClock.Elapsed.TotalSeconds
[System.IO.File]::WriteAllText((Join-Path $taskFolder 'stdout.log'), $taskOutput.GetAwaiter().GetResult())
[System.IO.File]::WriteAllText((Join-Path $taskFolder 'stderr.log'), $taskError.GetAwaiter().GetResult())
$taskReceipt = [ordered]@{
    command = @('C:/Python/Python314/python.exe', '-I', '-S', (Join-Path $taskRaw 'root-installed-audit-v1.py'))
    cwd = $taskRaw
    status = $(if ($taskTimedOut) { 'timeout' } elseif ($taskProcess.ExitCode -ne 0) { 'failed' } else { 'completed' })
    exit_code = $taskProcess.ExitCode
    process_wall_seconds = $taskElapsed
    seconds_limit = $taskCap
    cumulative_used_seconds = ([double]$taskLedger.used_seconds + $taskElapsed)
    remaining_seconds = (600.0 - [double]$taskLedger.used_seconds - $taskElapsed)
    memory_accounting = 'Metadata-only Python -I -S; no numerical imports or new model execution; peak memory not independently sampled'
    audit_sha256 = (Get-FileHash -LiteralPath (Join-Path $taskRaw 'root-installed-audit-v1.py') -Algorithm SHA256).Hash.ToLowerInvariant()
    wrapper_sha256 = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
}
[System.IO.File]::WriteAllText((Join-Path $taskFolder 'execution.json'), (($taskReceipt | ConvertTo-Json -Depth 8) + "`n"))
$taskLedger.used_seconds = $taskReceipt.cumulative_used_seconds
$taskAttempt = [ordered]@{path=$taskFolder}
foreach ($taskKey in $taskReceipt.Keys) { $taskAttempt[$taskKey] = $taskReceipt[$taskKey] }
$taskLedger.attempts += [pscustomobject]$taskAttempt
[System.IO.File]::WriteAllText((Join-Path $taskRaw 'ledger.json'), (($taskLedger | ConvertTo-Json -Depth 30) + "`n"))
$taskReceipt | ConvertTo-Json -Depth 8 -Compress
if ($taskProcess.ExitCode -ne 0 -or $taskTimedOut) { exit 1 }
