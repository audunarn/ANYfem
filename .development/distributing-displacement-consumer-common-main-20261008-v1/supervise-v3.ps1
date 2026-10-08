param(
 [Parameter(Mandatory=$true)][ValidateSet('installed-controls','public-example','affected-legacy')][string]$Phase,
 [Parameter(Mandatory=$true)][ValidatePattern('^[a-z][a-z0-9-]{0,63}$')][string]$Attempt
)
$ErrorActionPreference='Stop'
$raw=$PSScriptRoot
$authorityPath=Join-Path $raw 'authority-v3.json'
$authority=Get-Content -LiteralPath $authorityPath -Raw | ConvertFrom-Json
if($authority.status -ne 'APPROVED' -or -not $authority.approval_evidence){throw 'PENDING_USER_AUTHORITY: workload launch refused'}
$lock=[IO.File]::Open((Join-Path $raw 'launch-lock-v3'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
try {
 $ledgerPath=Join-Path $raw 'ledger-v3.json'
 $ledger=Get-Content -LiteralPath $ledgerPath -Raw | ConvertFrom-Json
 $manifestPath=Join-Path $raw 'manifest-v3.json'
 $manifest=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
 $manifestHash=(Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLower()
 $proposalHash=(Get-FileHash -LiteralPath (Join-Path $raw 'verification-proposal-v3.json') -Algorithm SHA256).Hash.ToLower()
 if($authority.total_seconds -ne 240 -or $authority.reserve_seconds -ne 60 -or $ledger.total_seconds -ne 240 -or $ledger.status -ne 'APPROVED'){throw 'Fresh240 artifact-only authority and ledger required'}
 if($null -ne $ledger.in_flight){throw 'Unresolved in-flight accounting; no remainder reused'}
 if($authority.manifest_sha256 -ne $manifestHash -or $authority.proposal_sha256 -ne $proposalHash -or $manifest.proposal_sha256 -ne $proposalHash){throw 'Authority/input bindings changed'}
 if($null -eq $ledger.used_seconds -or $null -eq $ledger.remaining_seconds -or -not [double]::IsFinite([double]$ledger.used_seconds) -or $ledger.used_seconds -lt 0 -or [decimal]$ledger.remaining_seconds -ne ([decimal]240-[decimal]$ledger.used_seconds)){throw 'Missing or inconsistent accounting; no remaining authority assumed'}
 $sum=0.0
 foreach($old in @($ledger.attempts)){
  if($null -eq $old.receipt.process_wall_seconds -or -not [double]::IsFinite([double]$old.receipt.process_wall_seconds) -or $old.receipt.process_wall_seconds -lt 0){throw 'Missing prior process-wall accounting'}
  $sum+=[double]$old.receipt.process_wall_seconds
 }
 if([Math]::Abs($sum-[double]$ledger.used_seconds) -gt 1e-7){throw 'Ledger cumulative sum mismatch'}
 foreach($pair in @(@('entry-v3.py','entry_sha256'),@('supervise-v3.ps1','supervisor_sha256'))){
  if((Get-FileHash -LiteralPath (Join-Path $raw $pair[0]) -Algorithm SHA256).Hash.ToLower() -ne $manifest.($pair[1])){throw 'Runner bytes changed'}
 }
 foreach($mapping in @($manifest.source_files,$manifest.test_files,$manifest.extra_test_files,$manifest.docs_files,$manifest.example_files,$manifest.metadata_files)){
  if($null -eq $mapping){continue}
  foreach($item in $mapping.PSObject.Properties){
   $inputPath=Join-Path $manifest.repository $item.Name
   if((Get-FileHash -LiteralPath $inputPath -Algorithm SHA256).Hash.ToLower() -ne $item.Value){throw ('Frozen source changed: '+$item.Name)}
  }
 }
 $priorSource=$manifest.prior_source_evidence
 if((Get-FileHash -LiteralPath $priorSource.path -Algorithm SHA256).Hash.ToLower() -ne $priorSource.sha256){throw 'Prior 53-case source evidence changed'}
 $sourceResult=Get-Content -LiteralPath $priorSource.path -Raw | ConvertFrom-Json
 if($sourceResult.status -ne 'PASSED' -or $sourceResult.passed -ne 53 -or $sourceResult.collected -ne 53 -or
    $sourceResult.failed -ne 0 -or $sourceResult.errors -ne 0 -or $sourceResult.skipped -ne 0){throw 'Prior source evidence is not a complete pass'}
 $priorTimeout=$manifest.prior_timeout_evidence
 if((Get-FileHash -LiteralPath $priorTimeout.path -Algorithm SHA256).Hash.ToLower() -ne $priorTimeout.sha256){throw 'Prior timeout ledger changed'}
 $priorLedger=Get-Content -LiteralPath $priorTimeout.path -Raw | ConvertFrom-Json
 if($priorLedger.attempts[-1].receipt.status -ne 'timeout'){throw 'Prior build timeout must remain a timeout'}
 $installationPath=Join-Path $raw 'installation-v3.json'
 $installation=Get-Content -LiteralPath $installationPath -Raw | ConvertFrom-Json
 if($installation.manifest_sha256 -ne $manifestHash -or $installation.candidate_commit -ne $manifest.candidate_commit){throw 'Installed artifact binding differs'}
 $expectedWheel=(Resolve-Path -LiteralPath (Join-Path $raw 'wheel/anyfem-0.4.1-py3-none-any.whl')).Path
 if($installation.wheel -ne $expectedWheel -or (Get-FileHash -LiteralPath $expectedWheel -Algorithm SHA256).Hash.ToLower() -ne $installation.wheel_sha256){throw 'Installed wheel identity differs'}
 $siteRoot=(Resolve-Path -LiteralPath (Join-Path $raw 'site')).Path
 $siteFiles=@(Get-ChildItem -LiteralPath $siteRoot -Recurse -File)
 if($siteFiles.Count -ne @($installation.site_files.PSObject.Properties).Count){throw 'Installed site file count differs'}
 foreach($siteFile in $siteFiles){
  $relative=$siteFile.FullName.Substring($siteRoot.Length+1).Replace('\','/')
  $expected=$installation.site_files.PSObject.Properties[$relative]
  if($null -eq $expected -or (Get-FileHash -LiteralPath $siteFile.FullName -Algorithm SHA256).Hash.ToLower() -ne $expected.Value){throw ('Installed site file differs: '+$relative)}
 }
 function RequirePassed([string]$wanted){
  $passed=@($ledger.attempts | Where-Object {$_.receipt.phase -eq $wanted -and $_.receipt.manifest_sha256 -eq $manifestHash -and $_.receipt.status -eq 'completed'})
  if($passed.Count -lt 1){throw ('Fresh packet needs successful '+$wanted)}
  $result=Get-Content -LiteralPath (Join-Path $passed[-1].path 'result.json') -Raw | ConvertFrom-Json
  if($result.status -ne 'PASSED' -or $result.failed -ne 0 -or $result.errors -ne 0 -or $result.skipped -ne 0 -or $result.tests -lt 1 -or $result.passed -ne $result.tests -or $result.tests -ne $manifest.phases.$wanted.expected_cases){throw ('Incomplete checks: '+$wanted)}
  foreach($key in @('tests','passed','failed','errors','skipped')){
   if($passed[-1].receipt.counts.$key -ne $result.$key){throw ('Passed result and charged receipt count differ: '+$key)}
  }
  if($wanted -ne 'source-controls' -and (Get-FileHash -LiteralPath (Join-Path $raw 'installation-v3.json') -Algorithm SHA256).Hash.ToLower() -ne $result.installation_after.receipt_sha256){throw 'Installed consumer wheel/site receipt changed'}
 }
 if($Phase -in @('public-example','affected-legacy')){RequirePassed 'installed-controls'}
 $phaseCeiling=[double]$manifest.phases.$Phase.ceiling_seconds
 $phaseUsed=0.0
 foreach($old in @($ledger.attempts | Where-Object {$_.receipt.phase -eq $Phase})){$phaseUsed+=[double]$old.receipt.process_wall_seconds}
 $limit=[Math]::Min(($phaseCeiling-$phaseUsed),([double]$ledger.remaining_seconds-60.0))
 if($limit -le 2.0){throw 'Not enough phase allowance while retaining cleanup and reserve'}
 $folder=Join-Path $raw $Attempt
 if(Test-Path -LiteralPath $folder){throw 'Unique attempt required; existing evidence preserved'}
 [void](New-Item -ItemType Directory -Path $folder)
 if(-not [Diagnostics.Stopwatch]::IsHighResolution){throw 'Windows high-resolution monotonic deadline required'}
 $deadlineNs=[long](([decimal][Diagnostics.Stopwatch]::GetTimestamp()/[decimal][Diagnostics.Stopwatch]::Frequency+[decimal]$limit)*1000000000)
 $launch=[ordered]@{attempt=$Attempt;phase=$Phase;manifest_sha256=$manifestHash;limit_seconds=$limit;monotonic_clock='WINDOWS_QUERY_PERFORMANCE_COUNTER';monotonic_deadline_ns=$deadlineNs}
 [IO.File]::WriteAllText((Join-Path $folder 'launch-inputs.json'),($launch|ConvertTo-Json))
 $process=[Diagnostics.Process]::new()
 $process.StartInfo.FileName='C:/Python/Python314/python.exe'
 foreach($arg in @('-I','-B',(Join-Path $raw 'entry-v3.py'),'--phase',$Phase,'--attempt',$Attempt)){$process.StartInfo.ArgumentList.Add($arg)}
 $process.StartInfo.WorkingDirectory=$raw
 $process.StartInfo.UseShellExecute=$false
 $process.StartInfo.CreateNoWindow=$true
 $process.StartInfo.RedirectStandardOutput=$true
 $process.StartInfo.RedirectStandardError=$true
 foreach($key in @('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','NUMBER_OF_CPUS','ANYFEM_TEST_THREADS','VECLIB_MAXIMUM_THREADS','BLIS_NUM_THREADS')){$process.StartInfo.Environment[$key]='1'}
 foreach($key in @('PYTHONOPTIMIZE','OPENBLAS_CORETYPE','PYTEST_ADDOPTS','PYTEST_PLUGINS','ANYFEM_EXPECT_NATIVE_V2','ANYFEM_RUN_GUI_TESTS')){[void]$process.StartInfo.Environment.Remove($key)}
 $process.StartInfo.Environment['NUMBA_DISABLE_JIT']='1'
 $process.StartInfo.Environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
 $process.StartInfo.Environment['PIP_NO_INDEX']='1'
 $clock=[Diagnostics.Stopwatch]::new()
 $started=$false;$exited=$false;$timeout=$false;$memoryExceeded=$false;$peak=0L
 $primary=$null;$cleanup=$null;$out=$null;$err=$null;$exitCode=$null
 # Persist uncertainty BEFORE launch. A crash cannot leave reusable old remainder.
 $marker=[ordered]@{phase=$Phase;attempt=$Attempt;path=$folder;manifest_sha256=$manifestHash;prior_used_seconds=$ledger.used_seconds;prior_remaining_seconds=$ledger.remaining_seconds;limit_seconds=$limit;status='LAUNCH_AND_ACCOUNTING_UNRESOLVED'}
 $ledger | Add-Member -NotePropertyName in_flight -NotePropertyValue $marker -Force
 $ledger.remaining_seconds=$null
 [IO.File]::WriteAllText(($ledgerPath+'.part'),($ledger|ConvertTo-Json -Depth 20))
 Move-Item -LiteralPath ($ledgerPath+'.part') -Destination $ledgerPath -Force
 function RemainingWaitMilliseconds([int]$maximum){
  return [int][Math]::Max(0,[Math]::Min($maximum,[Math]::Floor(($limit-$clock.Elapsed.TotalSeconds)*1000)))
 }
 try {
  $clock.Start()
  $started=$process.Start()
  if(-not $started){throw 'Process start returned false'}
  [IO.File]::WriteAllText((Join-Path $folder 'owned-root.json'),([ordered]@{pid=$process.Id;creation_time=([DateTimeOffset]$process.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds()/1000.0}|ConvertTo-Json))
  $out=$process.StandardOutput.ReadToEndAsync();$err=$process.StandardError.ReadToEndAsync()
  $process.ProcessorAffinity=[IntPtr]1
  while(-not $process.WaitForExit(10)){
   $process.Refresh();$peak=[Math]::Max($peak,$process.WorkingSet64)
   if($process.WorkingSet64 -gt 2147483648){$memoryExceeded=$true;break}
   if($clock.Elapsed.TotalSeconds -ge ($limit-2.0)){$timeout=$true;break}
  }
 } catch {$primary=$_}
 finally {
  if($started){
   try {
    if(-not $process.HasExited){$process.Kill($true)}
    $exited=$process.WaitForExit((RemainingWaitMilliseconds 2000))
    if(-not $exited){throw 'Owned process remains live'}
    $exitCode=$process.ExitCode
   } catch {$cleanup=$_}
   try {
    $registry=Join-Path $folder 'owned-processes.json'
    if(-not (Test-Path -LiteralPath $registry)){throw 'Owned descendant accounting missing'}
    # Atomic replacement must not conflict with the reader's Windows sharing mode.
    $registryStream=[IO.File]::Open($registry,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try {
     $registryReader=[IO.StreamReader]::new($registryStream)
     try {$registryText=$registryReader.ReadToEnd()} finally {$registryReader.Dispose()}
    } finally {$registryStream.Dispose()}
    $rows=@($registryText | ConvertFrom-Json)
    if($rows.Count -gt 64){throw 'Owned descendant bound exceeded'}
    foreach($row in $rows){
     $child=Get-Process -Id ([int]$row.pid) -ErrorAction SilentlyContinue
     if($null -eq $child){continue}
     $created=([DateTimeOffset]$child.StartTime.ToUniversalTime()).ToUnixTimeMilliseconds()/1000.0
     if([Math]::Abs($created-[double]$row.creation_time) -ge .001){throw 'PID identity mismatch; foreign process not terminated'}
     if(-not $child.HasExited){$child.Kill($true)}
     if(-not $child.WaitForExit((RemainingWaitMilliseconds 2000))){throw 'Owned descendant remains live'}
    }
    if(Test-Path -LiteralPath (Join-Path $folder 'cleanup-uncertainty.json')){throw 'Entry reports uncertain descendant ownership or cleanup'}
   } catch {$cleanup=$_}
  }
  $clock.Stop()
  $elapsed=if($started){$clock.Elapsed.TotalSeconds}else{0.0}
  $capOverrun=$started -and $elapsed -gt $limit
  foreach($stream in @(@('stdout.log',$out),@('stderr.log',$err))){
   try {
    if($null -ne $stream[1]){
     if(-not $stream[1].Wait(2000)){throw 'Output drain incomplete'}
     [IO.File]::WriteAllText((Join-Path $folder $stream[0]),$stream[1].GetAwaiter().GetResult())
    }
   } catch {if($null -eq $cleanup){$cleanup=$_}}
  }
  $counts=$null
  if($null -eq $cleanup -and $null -eq $primary -and -not $timeout -and -not $memoryExceeded -and -not $capOverrun -and $exitCode -eq 0){
   try {
    $result=Get-Content -LiteralPath (Join-Path $folder 'result.json') -Raw | ConvertFrom-Json
    $expected=$manifest.phases.$Phase.expected_cases
    if($result.status -ne 'PASSED' -or $result.tests -lt 1 -or $result.passed -ne $result.tests -or $result.failed -ne 0 -or $result.errors -ne 0 -or $result.skipped -ne 0 -or ($null -ne $expected -and $result.tests -ne $expected)){throw 'Exit0 is insufficient: full frozen test inventory must pass'}
    if(-not (Test-Path -LiteralPath (Join-Path $folder 'runtime-after.json')) -or (Test-Path -LiteralPath (Join-Path $folder 'resource-failure.json'))){throw 'Effective runtime/resource evidence incomplete'}
    if($Phase -ne 'source-controls' -and ((Get-FileHash -LiteralPath (Join-Path $raw 'installation-v3.json') -Algorithm SHA256).Hash.ToLower() -ne $result.installation_after.receipt_sha256 -or $result.installation_after.python_files -ne $manifest.consumer_python_files)){throw 'Frozen consumer wheel/site identity evidence incomplete'}
    if($result.solver_installation_after.wheel_sha256 -ne $manifest.solver_artifact.wheel_sha256 -or $result.solver_installation_after.python_files -ne @($manifest.solver_artifact.python_files.PSObject.Properties).Count){throw 'Immutable solver wheel/site identity evidence incomplete'}
    $counts=[ordered]@{tests=$result.tests;passed=$result.passed;failed=$result.failed;errors=$result.errors;skipped=$result.skipped}
   } catch {$primary=$_}
  }
  $status=if($null -ne $cleanup){'cleanup_or_evidence_failure'}elseif($capOverrun){'cap_overrun'}elseif($null -ne $primary){'supervisor_failure'}elseif($memoryExceeded){'memory_limit'}elseif($timeout){'timeout'}elseif($exitCode -ne 0){'failed'}else{'completed'}
  $receipt=[ordered]@{command=@($process.StartInfo.FileName)+@($process.StartInfo.ArgumentList);cwd=$raw;phase=$Phase;attempt=$Attempt;manifest_sha256=$manifestHash;authority_sha256=(Get-FileHash -LiteralPath $authorityPath -Algorithm SHA256).Hash.ToLower();candidate_commit=$manifest.candidate_commit;status=$status;started=$started;owned_child_exited=$exited;exit_code=$exitCode;process_wall_seconds=$elapsed;limit_seconds=$limit;cleanup_reserve_seconds=2.0;parent_peak_sampled_working_set_bytes=$peak;counts=$counts;primary_error=$(if($primary){$primary.ToString()}else{$null});cleanup_error=$(if($cleanup){$cleanup.ToString()}else{$null})}
  [IO.File]::WriteAllText((Join-Path $folder 'execution.json'),($receipt|ConvertTo-Json -Depth 12))
  $ledger.used_seconds=[decimal]$ledger.used_seconds+[decimal]$elapsed
  $ledger.remaining_seconds=if($null -ne $cleanup -or $capOverrun){$null}else{[decimal]240-[decimal]$ledger.used_seconds}
  $ledger.attempts=@($ledger.attempts)+@([ordered]@{path=$folder;receipt=$receipt})
  $ledger.in_flight=$null
  [IO.File]::WriteAllText(($ledgerPath+'.part'),($ledger|ConvertTo-Json -Depth 20))
  Move-Item -LiteralPath ($ledgerPath+'.part') -Destination $ledgerPath -Force
  $process.Dispose()
 }
 if($status -ne 'completed'){throw ('Attempt ended '+$status+'; inspect preserved evidence before repair or continuation')}
 Write-Output ([ordered]@{attempt=$Attempt;status=$status;used_seconds=$ledger.used_seconds;remaining_seconds=$ledger.remaining_seconds}|ConvertTo-Json -Compress)
} finally {$lock.Dispose()}
