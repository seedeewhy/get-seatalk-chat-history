# Proven runbook — what was tested, and how

This is the exact, validated path that produced working results (SeaTalk v2.9.5, macOS, arm64, Node 20, Python 3.12). Follow it to reproduce or to debug when something drifts.

## The 6 discovery steps that made it work (history of the solve)

1. **Locate the stores.** `~/Library/Application Support/SeaTalk/` → `main_<uid>.sqlite`, `search_<uid>.sqlite`, and `IndexedDB/https_web.haiserve.com_0.indexeddb.leveldb/`. `config.json` → `LAST_LOGIN_USER_ID` confirms the uid.
2. **Confirm encryption.** File headers are random (no `SQLite format 3\0`) ⇒ encrypted. System `sqlite3` → "file is not a database".
3. **Find the key + cipher in the app.** `strings` on `SeaTalk.app/Contents/Resources/2_9_5_bundle.asar` reveals the open code:
   `new (better-sqlite3-multiple-ciphers)(file,{readonly}); r.pragma("key='40a3884b8b032e6f${e}'")`.
   ⇒ key = `40a3884b8b032e6f` + userId; cipher = SQLite3MultipleCiphers **default (ChaCha20)**, *not* SQLCipher.
4. **Build the matching engine.** No arm64/node-20 prebuilt for `better-sqlite3-multiple-ciphers`; it builds from source. With only CLT (no full Xcode), node-gyp's `xcodebuild -version` fails → **shim `xcodebuild`** to print a version (see `setup.sh`). Then it decrypts cleanly.
5. **Decrypt & read.** Open read-only, `pragma key`, then query `chat_message`, `thread_info`, `emoji_info`, `search`'s FTS, etc.
6. **Resolve identities.** Names/emails/group names are **not** in SQLite — they're in the Chromium IndexedDB (custom `idb_cmp1` comparator + V8 values). Generic LevelDB libs can't open it; `ccl_chromium_reader` can. `userInfo2` (uid→name/email), `group-info` (sid→name), `group-members` (roster).

## Reproduce end-to-end (what the skill automates)

```bash
# 0. setup once
bash scripts/setup.sh

# 1. snapshot (skill does this to a temp dir; shown here for clarity)
#    copy main_<uid>.sqlite(+wal/shm), search_<uid>.sqlite, and the IndexedDB leveldb (minus LOCK)

# 2. run
cd scripts
python3 get_seatalk_history.py --list-groups
python3 get_seatalk_history.py --sid group-<id> --limit 50 --threads
python3 get_seatalk_history.py --email <name>@shopee.com --limit 40
python3 get_seatalk_history.py --search "<kw>"
python3 get_seatalk_history.py --sid group-<id> --roster
python3 get_seatalk_history.py --sid group-<id> --images ./imgs
```

## Validated results (acceptance evidence)

- Decryption of `main`/`search` with the baked key — ✅ (315k FTS rows, 331k messages).
- Group name ↔ sid (`group-4003550` = "AI Champions!!") — ✅.
- Shopee emails via `userInfo2` (34k directory) — ✅ (e.g. `stella.simsx@shopee.com`).
- Roster + roles (owner = Clarence Chng) — ✅.
- Thread reconstruction (9-reply thread via `rtmid`) — ✅.
- Reactions / quotes / edits / recalls / pins — ✅.
- Image recovery from Chromium cache — ✅ (483 valid PNG/JPEG carved on the test run).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `dependencies not installed` | setup not run | `bash scripts/setup.sh` |
| engine `file is not a database` | wrong key/cipher, or SeaTalk updated | check the warn line; re-extract prefix from the new `*_bundle.asar` (`grep -aoE "key='[0-9a-f]+" *_bundle.asar`) and update `KEY_PREFIX` |
| all names blank | resolver cache empty/corrupt | delete `~/.seatalk-skill/cache/resolvers_<uid>.json` and re-run (it self-heals now) |
| `node-gyp`/`xcodebuild` build error | only CLT installed | the setup shim handles it; ensure `setup.sh` ran, not a raw `npm install` |
| ccl import error | reader missing | `pip install --user "git+https://github.com/cclgroupltd/ccl_chromium_reader.git"` |
| group name matches 2+ groups | substring hit | re-run with `--sid <id>` (tool prints the candidates) |
| images missing | never viewed / evicted | expected — only recently-viewed images are cached locally |

## Security notes for maintainers

- Baked `KEY_PREFIX` is an app-embedded constant (same for all installs of the version), not a personal secret; it only unlocks the local user's own `0600` files.
- Keep native binaries + the name/email cache under `~/.seatalk-skill/` (per-machine) — **never** commit them into the synced shared folder.
- Read-only by contract: the skill snapshots to a temp dir and deletes it after each run.
