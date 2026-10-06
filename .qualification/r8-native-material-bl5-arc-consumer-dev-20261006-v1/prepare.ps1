$ErrorActionPreference='Stop'
$raw=$PSScriptRoot
$repo=Split-Path (Split-Path $raw)
$solver='C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001'
$solverRaw=Join-Path $solver '.qualification/b3-ge-e4-pl/r8-native-material-bl5-arc-dev-20261006-v1'
$femRaw=Join-Path $repo '.qualification/r8-b2-physical-footprint-consumer-20261006-v1'
$snapshot=Join-Path $raw 'snapshot-v1'
if(Test-Path -LiteralPath $snapshot){throw 'Frozen preparation already exists; never overwrite'}
[void](New-Item -ItemType Directory -Path $snapshot)
function Hash($p){(Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLower()}
function Save($name,$value){[IO.File]::WriteAllText((Join-Path $raw $name),($value|ConvertTo-Json -Depth 60))}
$inputs=[ordered]@{}
foreach($name in @('tests/test_stiffened_shell_native_material_bl5_arc_consumer.py','examples/stiffened_shell_native_material_bl5_arc_consumer.py','examples/stiffened_shell_native_fibre_bl5_mixed_j2_consumer.py','examples/stiffened_shell_native_fibre_bl5_consumer.py','pyproject.toml')){
 $source=Join-Path $repo $name;$target=Join-Path $snapshot $name
 [void](New-Item -ItemType Directory -Force -Path (Split-Path $target))
 Copy-Item -LiteralPath $source -Destination $target
 $digest=Hash $source;if((Hash $target) -ne $digest){throw 'Input copy changed'}
 $inputs[$name]=[ordered]@{source=$source;sha256=$digest}
}
$artifacts=[ordered]@{}
foreach($spec in @(@('anyfem',$femRaw,'anyfem-0.4.1-py3-none-any.whl','d1c9714d7ca3e21d5d95c4ab9813bbf40d635c99cfa732a5f9284242f45d8daf'),@('anysolver',$solverRaw,'anysolver-0.4.7-py3-none-any.whl','4c027af197b7d8337e9ceb1d106e2b77befbb493179a7e6528ba9fb6e6cbf98a'))){
 $wheel=Join-Path $spec[1] ('wheel-v1/'+$spec[2]);$site=Join-Path $spec[1] 'site-v1'
 if((Hash $wheel) -ne $spec[3]){throw 'Frozen wheel identity mismatch'}
 $payload=[ordered]@{};$archive=[IO.Compression.ZipFile]::OpenRead($wheel)
 try{foreach($entry in $archive.Entries){if($entry.FullName.StartsWith($spec[0]+'/') -and $entry.FullName.EndsWith('.py')){
  $stream=$entry.Open();$algorithm=[Security.Cryptography.SHA256]::Create()
  try{$digest=[Convert]::ToHexString($algorithm.ComputeHash($stream)).ToLower()}finally{$stream.Dispose();$algorithm.Dispose()}
  if((Hash (Join-Path $site $entry.FullName)) -ne $digest){throw 'Installed module differs from wheel'}
  if($spec[0] -eq 'anyfem' -and (Hash (Join-Path ($repo+'/src') $entry.FullName)) -ne $digest){throw 'Current consumer product differs from frozen wheel'}
  if($spec[0] -eq 'anysolver' -and (Hash (Join-Path ($solver+'/src') $entry.FullName)) -ne $digest){throw 'Current solver product differs from frozen wheel'}
  $payload[$entry.FullName]=$digest
 }}}finally{$archive.Dispose()}
 $artifacts[$spec[0]]=[ordered]@{wheel=$wheel;sha256=$spec[3];site=$site;payload=$payload}
}
$dependencies=[ordered]@{};$evidence=[ordered]@{}
foreach($record in @((Join-Path $femRaw 'installed-dependency-bindings-v1.json'),(Join-Path $solverRaw 'installed-v1/loaded-modules.json'))){
 $evidence[$record]=Hash $record
 $parsed=Get-Content -LiteralPath $record -Raw|ConvertFrom-Json
 foreach($property in $parsed.PSObject.Properties){
  if($property.Name -match '^(anyfem|anysolver)(\.|$)' -or $property.Name -notmatch '^(any|numpy|scipy|numba|llvmlite)'){continue}
  $row=$property.Value
  if($dependencies.Contains($property.Name) -and (($dependencies[$property.Name].path -ne $row.path) -or ($dependencies[$property.Name].sha256 -ne $row.sha256))){throw 'Conflicting frozen dependency binding'}
  if((Hash $row.path) -ne $row.sha256){throw "Readonly dependency changed: $($property.Name)"}
  $dependencies[$property.Name]=$row
 }
}
Save 'frozen-manifest.json' ([ordered]@{status='SOURCE_ONLY_UNRUN';consumer_commit='58a1d3d4f6ace545959a212859f3055fd30fc6ba';solver_product_commit='9bc30096';solver_evidence_commit='4bce5e4f';inputs=$inputs;artifacts=$artifacts;dependencies=$dependencies;evidence=$evidence;ci=(Join-Path $solver '.qualification/b3-ge-e4-pl/r8-native-s3-restart-binding-dev-20261005-v1/ci-site-network-v2');readonly_source_roots=@('C:/Github/ANYmaterial/src','C:/Github/ANYmesh/src','C:/Github/ANYgeometry/src','C:/Github/ANYfileIO/src')})
Save 'selectors.json' @('tests/test_stiffened_shell_native_material_bl5_arc_consumer.py::test_project_native_material_arc_prefix_cancel_restore_continue','tests/test_stiffened_shell_native_material_bl5_arc_consumer.py::test_existing_v6_project_ownership_and_strict_input_refusals')
Save 'authority.json' ([ordered]@{status='PENDING_USER_APPROVAL';total_seconds=150;reserved_for_final_verification_seconds=30;approval_evidence=$null;scope='Only the two frozen installed consumer tests; no build/install or solver campaign';memory_bytes=2147483648;affinity=@(0);one_job=$true})
Save 'ledger.json' ([ordered]@{status='PENDING_USER_APPROVAL';total_seconds=150;used_seconds=0;attempts=@();old_solver_allowance_untouched=$true})
Save 'preparation.json' ([ordered]@{status='SOURCE_ONLY_UNRUN';method='Native PowerShell/.NET copies and hashes only; no interpreter';prepare_sha256=(Hash $PSCommandPath);manifest_sha256=(Hash (Join-Path $raw 'frozen-manifest.json'));selectors_sha256=(Hash (Join-Path $raw 'selectors.json'))})
