#!/bin/bash
# One-time (per-machine) setup for get-seatalk-chat-history.
# Installs the two proven decryption toolchains into ~/.seatalk-skill/ so the
# shared skill folder stays free of native binaries. Idempotent — safe to re-run.
#
# Self-healing: if the machine's default `node`/`python3` can't load the
# compiled cipher module or the IndexedDB reader (too new to have a prebuilt
# binary, or too old to meet the reader's Python floor), this installs a
# side-by-side, keg-only Homebrew toolchain instead of touching the user's
# default `node`/`python3`. The working binaries are recorded so
# get_seatalk_history.py can find them regardless of the caller's PATH.
set -euo pipefail

DEPS="$HOME/.seatalk-skill"
mkdir -p "$DEPS/cache"

# A bare `require()` is not enough to prove the module works: better-sqlite3's
# file-backed (non-memory) DB open lazily touches a second binding that a
# require() alone doesn't exercise, so the smoke test must open a real file.
node_module_works() {
  local node_bin="$1"
  "$node_bin" -e "
    const Database = require('$DEPS/node_modules/better-sqlite3-multiple-ciphers');
    const fs = require('fs'), os = require('os'), path = require('path');
    const f = path.join(os.tmpdir(), 'seatalk-skill-smoketest-'+process.pid+'.sqlite');
    try {
      const w = new Database(f); w.pragma(\"key='x'\"); w.exec('CREATE TABLE t(a int)'); w.close();
      const r = new Database(f, {readonly:true}); r.pragma(\"key='x'\"); r.prepare('SELECT 1').get(); r.close();
    } finally { try { fs.unlinkSync(f); } catch (e) {} }
  " >/dev/null 2>&1
}

install_module() {
  local node_bin="$1" npm_bin node_dir
  node_dir="$(dirname "$node_bin")"
  npm_bin="$node_dir/npm"
  # SeaTalk uses SQLite3MultipleCiphers (ChaCha20), not SQLCipher. The npm package
  # has no arm64/node-20 prebuilt, so it builds from source. On machines with only
  # Command Line Tools (no full Xcode), node-gyp's `xcodebuild -version` probe fails —
  # shim it so the CLT compiler is used.
  local shim="$DEPS/.shim"; mkdir -p "$shim"
  printf '#!/bin/bash\necho "Xcode 15.0"\necho "Build version 15A240d"\n' > "$shim/xcodebuild"
  chmod +x "$shim/xcodebuild"
  ( cd "$DEPS" && [ -f package.json ] || echo '{"name":"seatalk-skill-deps","private":true}' > package.json )
  # npm's own shebang is `#!/usr/bin/env node`, so node-gyp re-resolves `node` via
  # PATH regardless of which npm binary we invoke directly — $node_dir must lead
  # PATH or the build/prebuild-install step silently targets the wrong Node.
  PATH="$shim:$node_dir:$PATH" "$npm_bin" install --prefix "$DEPS" better-sqlite3-multiple-ciphers@latest >/dev/null 2>&1
}

find_brew_node() {
  # Look for an already-installed keg-only Homebrew Node across common versions.
  for v in 22 20 24 23 21; do
    for prefix in /opt/homebrew /usr/local; do
      [ -x "$prefix/opt/node@$v/bin/node" ] && echo "$prefix/opt/node@$v/bin/node" && return 0
    done
  done
  return 1
}

echo "▸ [1/2] Node engine: better-sqlite3-multiple-ciphers (the cipher SeaTalk uses)"
NODE_BIN_FILE="$DEPS/node_bin"
WORKING_NODE=""

# Recorded binary from a previous run, still present and still working?
if [ -f "$NODE_BIN_FILE" ]; then
  cand="$(cat "$NODE_BIN_FILE")"
  [ -x "$cand" ] && node_module_works "$cand" && WORKING_NODE="$cand"
fi

if [ -z "$WORKING_NODE" ] && [ -f "$DEPS/node_modules/better-sqlite3-multiple-ciphers/package.json" ]; then
  # Module already installed — see if PATH's node (or a known Homebrew keg) can load it.
  if command -v node >/dev/null 2>&1 && node_module_works "$(command -v node)"; then
    WORKING_NODE="$(command -v node)"
  elif b="$(find_brew_node)" && node_module_works "$b"; then
    WORKING_NODE="$b"
  fi
fi

if [ -n "$WORKING_NODE" ]; then
  echo "   already installed ✓ (using $WORKING_NODE)"
else
  if command -v node >/dev/null 2>&1; then
    install_module "$(command -v node)" || true
    node_module_works "$(command -v node)" && WORKING_NODE="$(command -v node)"
  fi
  if [ -z "$WORKING_NODE" ]; then
    # Default Node couldn't load the module — e.g. too new for the available
    # prebuilt binary, with no local compiler toolchain to build from source.
    # Fall back to a Homebrew Node LTS installed side-by-side: keg-only, so it
    # never touches (or shadows) the user's default `node`.
    echo "   default Node incompatible — trying a side-by-side Homebrew node@22 (keg-only, won't touch your default node)"
    if b="$(find_brew_node)"; then
      install_module "$b" || true
      node_module_works "$b" && WORKING_NODE="$b"
    fi
    if [ -z "$WORKING_NODE" ] && command -v brew >/dev/null 2>&1; then
      brew install node@22 >/dev/null 2>&1 || true
      if b="$(find_brew_node)"; then
        install_module "$b" || true
        node_module_works "$b" && WORKING_NODE="$b"
      fi
    fi
  fi
  if [ -z "$WORKING_NODE" ]; then
    echo "   ✗ could not get a working Node + cipher module. See references/RUNBOOK.md."
    exit 1
  fi
  echo "   installed ✓ (using $WORKING_NODE)"
fi
echo "$WORKING_NODE" > "$NODE_BIN_FILE"

echo "▸ [2/2] Python IndexedDB reader: ccl_chromium_reader (names, emails, group rosters)"
VENV="$DEPS/venv"
py_is_new_enough() { "$1" -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; }
find_brew_python() {
  for v in 3.12 3.11 3.13 3.10; do
    for prefix in /opt/homebrew /usr/local; do
      [ -x "$prefix/bin/python$v" ] && echo "$prefix/bin/python$v" && return 0
    done
  done
  return 1
}

if [ -x "$VENV/bin/python3" ] && "$VENV/bin/python3" -c "import ccl_chromium_reader" 2>/dev/null; then
  echo "   already installed ✓ (venv at $VENV)"
elif py_is_new_enough python3 && python3 -c "import ccl_chromium_reader" 2>/dev/null; then
  echo "   already installed ✓ (system python3)"
elif py_is_new_enough python3; then
  python3 -m pip install --user --quiet "git+https://github.com/cclgroupltd/ccl_chromium_reader.git" >/dev/null 2>&1 \
    || python3 -m pip install --quiet "git+https://github.com/cclgroupltd/ccl_chromium_reader.git"
  python3 -c "import ccl_chromium_reader" && echo "   installed ✓ (system python3)"
else
  # System python3 is below the reader's Python ≥3.10 floor — use (or install)
  # a Homebrew Python in a dedicated venv instead of touching the default python3.
  PY="$(find_brew_python || true)"
  if [ -z "$PY" ] && command -v brew >/dev/null 2>&1; then
    echo "   system python3 is too old (needs ≥3.10) — installing python@3.12 via Homebrew"
    brew install python@3.12 >/dev/null 2>&1 || true
    PY="$(find_brew_python || true)"
  fi
  if [ -z "$PY" ]; then
    echo "   ✗ could not find or install Python ≥3.10. See references/RUNBOOK.md."
    exit 1
  fi
  [ -x "$VENV/bin/python3" ] || "$PY" -m venv "$VENV"
  "$VENV/bin/python3" -m pip install --quiet --upgrade pip >/dev/null 2>&1
  "$VENV/bin/python3" -m pip install --quiet "git+https://github.com/cclgroupltd/ccl_chromium_reader.git"
  "$VENV/bin/python3" -c "import ccl_chromium_reader" && echo "   installed ✓ (venv at $VENV, using $PY)"
fi

echo ""
echo "✅ setup complete. Deps live in $DEPS (per-machine, not synced)."
