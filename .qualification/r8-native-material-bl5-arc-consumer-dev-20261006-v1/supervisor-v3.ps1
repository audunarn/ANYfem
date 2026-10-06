param([Parameter(Mandatory)][ValidatePattern('^[a-z0-9-]+$')][string]$Attempt,[double]$Cap=120,[double]$Reserve=30)
$ErrorActionPreference='Stop';$raw=$PSScriptRoot
$authority=Get-Content -LiteralPath (Join-Path $raw 'authority.json') -Raw|ConvertFrom-Json
if($authority.status -ne 'APPROVED' -or -not $authority.approval_evidence){throw 'PENDING_USER_APPROVAL: interpreter launch refused'}
$ledger=Get-Content -LiteralPath (Join-Path $raw 'ledger.json') -Raw|ConvertFrom-Json
if($ledger.status -ne 'APPROVED' -or $authority.total_seconds -ne 150 -or $ledger.total_seconds -ne 150){throw 'Fresh authority/ledger mismatch'}
if($Cap -le 0 -or $Reserve -lt 0){throw 'Invalid execution bound'}
$limit=[Math]::Min($Cap,150-[double]$ledger.used_seconds-$Reserve)
if($limit -le 0){throw 'No allowance after reserve'}
$lock=Join-Path $raw 'active.lock';$held=[IO.File]::Open($lock,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try{
 $folder=Join-Path $raw $Attempt;if(Test-Path -LiteralPath $folder){throw 'Attempt already exists; failures are immutable'}
 [void](New-Item -ItemType Directory -Path $folder)
 $command=@('C:/Python/Python314/python.exe','-I',(Join-Path $raw 'entry-v3.py'),$Attempt)
 $process=[Diagnostics.Process]::new();$process.StartInfo.FileName=$command[0]
 foreach($arg in $command[1..3]){$process.StartInfo.ArgumentList.Add($arg)}
 $process.StartInfo.WorkingDirectory=$raw;$process.StartInfo.UseShellExecute=$false;$process.StartInfo.CreateNoWindow=$true
 $process.StartInfo.RedirectStandardOutput=$true;$process.StartInfo.RedirectStandardError=$true
 foreach($key in @('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS')){$process.StartInfo.Environment[$key]='1'}
 $process.StartInfo.Environment['NUMBA_DISABLE_JIT']='1';$process.StartInfo.Environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1';$process.StartInfo.Environment['PYTHONDONTWRITEBYTECODE']='1'
 [void]$process.StartInfo.Environment.Remove('OPENBLAS_CORETYPE')
 $clock=[Diagnostics.Stopwatch]::StartNew();$started=$false;$timedOut=$false;$launchFailure=$null
 try{
  $started=$process.Start();$process.ProcessorAffinity=[IntPtr]1
  if($process.ProcessorAffinity -ne [IntPtr]1){throw 'Native parent affinity readback failed'}
  $out=$process.StandardOutput.ReadToEndAsync();$err=$process.StandardError.ReadToEndAsync()
  $timedOut=-not $process.WaitForExit([int]([Math]::Max(0,$limit-$clock.Elapsed.TotalSeconds)*1000))
  if($timedOut){$process.Kill($true);$process.WaitForExit()}
 }catch{
  $launchFailure=$_.Exception.ToString()
  if($started -and -not $process.HasExited){$process.Kill($true);$process.WaitForExit()}
 }finally{
  $clock.Stop();$elapsed=$clock.Elapsed.TotalSeconds
  if($out){[IO.File]::WriteAllText((Join-Path $folder 'stdout.log'),$out.GetAwaiter().GetResult())}
  if($err){[IO.File]::WriteAllText((Join-Path $folder 'stderr.log'),$err.GetAwaiter().GetResult())}
  $receipt=[ordered]@{command=$command;cwd=$raw;status=$(if($launchFailure){'launch_failed'}elseif($timedOut){'timeout'}elseif($process.ExitCode -ne 0){'failed'}else{'completed'});exit_code=$(if($started){$process.ExitCode}else{$null});failure=$launchFailure;process_wall_seconds=$elapsed;seconds_limit=$limit;reserve_seconds=$Reserve;cumulative_used_seconds=([double]$ledger.used_seconds+$elapsed);remaining_seconds=(150-[double]$ledger.used_seconds-$elapsed);authority_sha256=(Get-FileHash -LiteralPath (Join-Path $raw 'authority.json')).Hash.ToLower();supervisor_sha256=(Get-FileHash -LiteralPath $PSCommandPath).Hash.ToLower();entry_sha256=(Get-FileHash -LiteralPath (Join-Path $raw 'entry-v3.py')).Hash.ToLower();manifest_sha256=(Get-FileHash -LiteralPath (Join-Path $raw 'frozen-manifest-v3.json')).Hash.ToLower();selectors_sha256=(Get-FileHash -LiteralPath (Join-Path $raw 'selectors.json')).Hash.ToLower();tree_memory='Entry watchdog samples entire tree, flushes samples and fails on resource changes; native timeout kills tree';timing='Whole interpreter launch through terminal, including preparation/import/test/post-audit overhead'}
  [IO.File]::WriteAllText((Join-Path $folder 'execution.json'),($receipt|ConvertTo-Json -Depth 12))
  $ledger.used_seconds=$receipt.cumulative_used_seconds;$ledger.attempts+=([pscustomobject]@{path=$folder;receipt=$receipt})
  [IO.File]::WriteAllText((Join-Path $raw 'ledger.json'),($ledger|ConvertTo-Json -Depth 40))
  $receipt|ConvertTo-Json -Depth 12 -Compress
 }
 if($receipt.status -ne 'completed'){exit 1}
}finally{$held.Dispose();Remove-Item -LiteralPath $lock}
