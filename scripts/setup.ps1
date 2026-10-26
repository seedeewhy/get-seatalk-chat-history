<#
  setup.ps1 — one-time (per-machine) Windows setup for get-seatalk-chat-history.
  Windows counterpart of setup.sh. Installs the two proven toolchains into
  %USERPROFILE%\.seatalk-skill\ so the synced shared folder stays free of native
  binaries. Idempotent — safe to re-run.

    1. Node engine  : better-sqlite3-multiple-ciphers (the ChaCha20 cipher SeaTalk uses)
    2. Python reader: ccl_chromium_reader (names, Shopee emails, group rosters)

  Requirements: Node >= 18 and Python >= 3.10 (or the `uv` CLI, which can fetch a
  managed Python). If Node is missing, install it first, e.g.:
      winget install OpenJS.NodeJS.LTS
  Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#>
$ErrorActionPreference = "Stop"
$DEPS = Join-Path $env:USERPROFILE ".seatalk-skill"
New-Item -ItemType Directory -Force (Join-Path $DEPS "cache") | Out-Null

function Find-Node {
  $c = Get-Command node -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  foreach ($p in @("$env:ProgramFiles\nodejs\node.exe", "${env:ProgramFiles(x86)}\nodejs\node.exe",
                   "$env:LOCALAPPDATA\Programs\nodejs\node.exe")) {
    if (Test-Path $p) { return $p }
  }
  return $null
}

Write-Host "> [1/2] Node engine: better-sqlite3-multiple-ciphers"
$node = Find-Node
if (-not $node) {
  Write-Host "   X Node.js not found. Install it (e.g. 'winget install OpenJS.NodeJS.LTS'), reopen the shell, and re-run." -ForegroundColor Red
  exit 1
}
$nodeDir = Split-Path $node
$npm = Join-Path $nodeDir "npm.cmd"
# Ensure the node dir leads PATH so npm's shelled-out prebuild/node-gyp steps resolve `node`.
$env:PATH = "$nodeDir;$env:PATH"
Write-Host "   using node $(& $node --version) at $node"

if (-not (Test-Path (Join-Path $DEPS "package.json"))) {
  '{"name":"seatalk-skill-deps","private":true}' | Out-File -FilePath (Join-Path $DEPS "package.json") -Encoding utf8
}
$binFile = Join-Path $DEPS "node_modules\better-sqlite3-multiple-ciphers\build\Release\better_sqlite3.node"
if (Test-Path $binFile) {
  Write-Host "   already installed (native binary present)"
} else {
  Write-Host "   installing better-sqlite3-multiple-ciphers ..."
  & $npm install --prefix $DEPS better-sqlite3-multiple-ciphers 2>&1 | Out-Null
  # npm's allow-scripts policy may skip the package install script, so fetch the
  # prebuilt native binary explicitly (no C++ compiler needed if a prebuild exists).
  $pkg = Join-Path $DEPS "node_modules\better-sqlite3-multiple-ciphers"
  $pi  = Join-Path $DEPS "node_modules\prebuild-install\bin.js"
  if (-not (Test-Path $binFile) -and (Test-Path $pi)) {
    Push-Location $pkg
    try { & $node $pi 2>&1 | Out-Null } finally { Pop-Location }
  }
  if (-not (Test-Path $binFile)) {
    Write-Host "   X could not obtain the native binary. If no prebuild exists for this Node version," -ForegroundColor Red
    Write-Host "     a build needs Visual Studio C++ Build Tools. See references/RUNBOOK.md." -ForegroundColor Red
    exit 1
  }
  Write-Host "   installed OK"
}
# Record the working Node path (Windows-style) so get_seatalk_history.py finds it regardless of PATH.
$node | Out-File -FilePath (Join-Path $DEPS "node_bin") -Encoding ascii -NoNewline

Write-Host "> [2/2] Python IndexedDB reader: ccl_chromium_reader"
$venv   = Join-Path $DEPS "venv"
$venvPy = Join-Path $venv "Scripts\python.exe"
$cclUrl = "git+https://github.com/cclgroupltd/ccl_chromium_reader.git"

function Py-HasCcl($py) { try { & $py -c "import ccl_chromium_reader" 2>$null; return ($LASTEXITCODE -eq 0) } catch { return $false } }

if ((Test-Path $venvPy) -and (Py-HasCcl $venvPy)) {
  Write-Host "   already installed (venv at $venv)"
} else {
  $uv = Get-Command uv -ErrorAction SilentlyContinue
  if ($uv) {
    Write-Host "   using uv to create venv (Python 3.12) + install reader"
    if (-not (Test-Path $venvPy)) { & uv venv $venv --python 3.12 | Out-Null }
    & uv pip install --python $venvPy $cclUrl | Out-Null
  } else {
    # Fall back to a system Python >= 3.10.
    $py = $null
    foreach ($cand in @("py -3.12","py -3.11","py -3.10","py -3","python")) {
      $parts = $cand.Split(" "); $exe = $parts[0]; $rest = $parts[1..($parts.Length-1)]
      if (Get-Command $exe -ErrorAction SilentlyContinue) {
        $v = & $exe @rest -c "import sys;print(1 if sys.version_info>=(3,10) else 0)" 2>$null
        if ($v -eq "1") { $py = $cand; break }
      }
    }
    if (-not $py) {
      Write-Host "   X need Python >= 3.10 (or the 'uv' CLI). Install one and re-run." -ForegroundColor Red
      exit 1
    }
    $parts = $py.Split(" "); $exe = $parts[0]; $rest = $parts[1..($parts.Length-1)]
    if (-not (Test-Path $venvPy)) { & $exe @rest -m venv $venv }
    & $venvPy -m pip install --quiet --upgrade pip | Out-Null
    & $venvPy -m pip install --quiet $cclUrl | Out-Null
  }
  if (Py-HasCcl $venvPy) { Write-Host "   installed OK (venv at $venv)" }
  else { Write-Host "   X ccl_chromium_reader import failed after install. See references/RUNBOOK.md." -ForegroundColor Red; exit 1 }
}

Write-Host ""
Write-Host "OK setup complete. Deps live in $DEPS (per-machine, not synced)."
Write-Host "   Run with the venv Python, e.g.:"
Write-Host "     & `"$venvPy`" scripts\get_seatalk_history.py --list-groups"
