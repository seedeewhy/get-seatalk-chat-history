---
name: get-seatalk-chat-history
description: "Read YOUR OWN SeaTalk chat history on YOUR OWN machine (macOS or Windows) — decrypted, with names, Shopee emails, threads, reactions, edits, group rosters, and best-effort image recovery. Also reads the chat-list tabs (Unread, @Me/mentions, For Later, Threads) and the Contacts directory (My Contacts, Group Chats, Bots, Announcements) with accurate name/email search. Everything (key derivation, cipher, full data-model mapping) is baked in, so no re-discovery is needed. Use when the user says: 'read my seatalk', 'get my seatalk chat history', 'what did <person> say on seatalk', 'summarise the <group> seatalk group', 'what are my unread seatalk chats', 'what seatalk messages mention me', 'what did I save for later on seatalk', 'my seatalk threads', 'search my seatalk for <keyword>', 'who is in the <group> seatalk group / their emails', 'find <person> in my seatalk contacts', 'export a seatalk thread', 'pull seatalk images'. Read-only; never writes to the live app. On ambiguous group or contact names it lists candidates and asks the user to confirm — never guesses. Cannot read anyone else's chats — only the locally logged-in user's own data. Sending messages is NOT covered here (see the SENDING note at the bottom)."
---

> **Maintainers — this skill is mirrored; keep the adapter in sync.** `.claude/skills/get-seatalk-chat-history/` is the **single source of truth**. `.agents/skills/get-seatalk-chat-history/` is the **auto-generated wrapper** used by Codex and other compatible agent runners — never hand-edit it. After changing **any** part of this skill (this `SKILL.md`, anything under `scripts/`, or `references/`), you **must** regenerate the adapter so it does not silently drift:
> ```bash
> python3 Wiki/pipeline/scripts/sync_project_skills.py            # regenerates .agents
> python3 Wiki/pipeline/scripts/sync_project_skills.py --check    # verify: exit 0 = in sync
> ```
> `bash Wiki/pipeline/scripts/sync_agents_md.sh` also runs this sync (and regenerates `AGENTS.md`). Needs Python ≥3.12. A launchd auto-sync runs on a **weekday 12:30 schedule only** (not per-edit), so any edit made between runs — evenings, weekends — leaves the mirrors stale until the next run. Treat the manual sync as a **required step**, not something to wait for.

# get-seatalk-chat-history

Read the **local user's own** SeaTalk history, fully decrypted and human-readable. SeaTalk stores every chat in encrypted local databases on the machine (macOS or Windows); this skill opens them the same way the app does and joins in names, emails, threads, reactions and group info.

> **Scope & safety (read once).** This only works on the machine where the user is logged into SeaTalk, against files owned by that user. It **cannot** decrypt anyone else's chats. It is **read-only** — it snapshots the DBs to a temp dir and never writes to the live app. Treat everything it returns as confidential (real colleagues, real emails). Don't paste chat contents into shared/synced docs without the user asking.

> **Platform note.** The scripts are cross-platform. On macOS the SeaTalk data dir is `~/Library/Application Support/SeaTalk` and the venv Python is `venv/bin/python3`; on Windows it's `%APPDATA%\SeaTalk` and `venv\Scripts\python.exe`. UTF-8 for CJK/emoji is forced by the scripts, so no `PYTHONUTF8` is needed. Everything else (key derivation, cipher, data-model mapping) is identical.

## Setup (once per machine)

**macOS / Linux:**
```bash
bash scripts/setup.sh
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```
Requires **Node ≥18** already installed (`winget install OpenJS.NodeJS.LTS`) and either the **`uv`** CLI or **Python ≥3.10**. The prebuilt native cipher binary is fetched automatically; no C++ compiler is needed unless no prebuild exists for your Node version.

Both installers put the two proven toolchains into `~/.seatalk-skill/` (per-machine, kept out of the synced shared folder): the SeaTalk cipher engine (`better-sqlite3-multiple-ciphers`) and the IndexedDB reader (`ccl_chromium_reader`). Idempotent. If SeaTalk isn't running, that's fine — this reads files, not the live app.

> On Windows, invoke the entrypoint with the venv Python: `& "$HOME\.seatalk-skill\venv\Scripts\python.exe" scripts\get_seatalk_history.py ...` (the `python3 …` examples below are the macOS/Linux form).

## Usage

All commands go through one entrypoint. Output is a Markdown transcript by default; add `--format json` for structured data.

```bash
cd scripts

# 1. See what conversations exist (names resolved, newest first)
python3 get_seatalk_history.py --list-groups

# 2. Read a group (name is a case-insensitive substring; it disambiguates if >1 match)
python3 get_seatalk_history.py --group "AI Champions!!" --limit 50
python3 get_seatalk_history.py --sid group-4003550 --limit 50 --threads      # exact id + expand threads

# 3. A 1:1 chat by the person's Shopee email
python3 get_seatalk_history.py --email amanda.thng@shopee.com --limit 40

# 4. Keyword search (SeaTalk's own FTS5 index) — all chats, or scoped to one
python3 get_seatalk_history.py --search "legal compliance"
python3 get_seatalk_history.py --search "vpn" --sid group-4003550

# 5. Group roster: members + roles + emails
python3 get_seatalk_history.py --sid group-4003550 --roster

# 6. Date-bounded
python3 get_seatalk_history.py --group "AI Champions!!" --after 2026-07-01 --before 2026-07-05

# 7. Recover images that were viewed in-app (best-effort — see limitations)
python3 get_seatalk_history.py --sid group-4003550 --images ./seatalk_images

# 8. Machine-readable
python3 get_seatalk_history.py --sid group-4003550 --format json
```

### Chat-list tabs (the top filters: All / Unread / @Me / Later / Private / Threads)

```bash
python3 get_seatalk_history.py --tab unread          # conversations with unread messages
python3 get_seatalk_history.py --tab mentions        # @Me — messages that @mention you (who + where)
python3 get_seatalk_history.py --tab later            # "For Later" — messages you saved, resolved to text
python3 get_seatalk_history.py --tab threads          # active/unread threads (with a drill-down hint per thread)
python3 get_seatalk_history.py --tab private          # (server-side lock feature — returns a limitation note)
```
Each supports `--limit` and `--format json`. For a thread the output prints the exact `--sid … --threads` command to drill into it.

### Contacts tab directory (My Contacts / Group Chats / Bots / Announcements)

```bash
python3 get_seatalk_history.py --directory contacts --search "amanda"    # your contacts (name/email search)
python3 get_seatalk_history.py --directory groups   --search "champion"  # groups you're in
python3 get_seatalk_history.py --directory bots      --search "aurora"   # bots (role-3 accounts)
python3 get_seatalk_history.py --directory announcements                 # notice/announcement channels
```

### Ambiguity — ALWAYS confirm, never guess

- **Group names** (`--group`) and **contacts** (`--email`) that match more than one target print the candidates to **stderr** and stop. **Do not pick for the user** — show them the list and ask which one, then re-run with the exact `--sid` (groups) or exact email (contacts). For contacts, a `★` marks people actually in the user's contact list (vs the wider 34k directory).
- To help the user choose, you can first run `--directory contacts --search "<name>"` to show matches with emails.

**Agent tips.** If a group name is ambiguous, the tool prints the matches to stderr — pick one and re-run with `--sid`. To find someone's `sid`/uid, use `--list-groups` or `--email`. For a "summarise the last week of <group>" request: run with `--after <date> --threads --format json`, then summarise the JSON.

## How it works (one paragraph)

SeaTalk encrypts `main_<uid>.sqlite` / `search_<uid>.sqlite` with **SQLite3MultipleCiphers (ChaCha20)** and opens them with `key = "40a3884b8b032e6f" + <userId>` — a 16-hex constant embedded in `SeaTalk.app` plus the numeric user id from the DB filename. That constant and the full table/field mapping are **baked into these scripts** (approved), so no re-derivation happens. Messages, threads, reactions, edits, quotes and links come from the SQLite `main` DB; keyword search from the `search` FTS5 DB; and **names, Shopee emails and group rosters** come from the Chromium **IndexedDB** (`userInfo2`, `group-info`, `group-members`), which needs the `ccl_chromium_reader` parser. Full detail in `references/`.

- `references/HOW_IT_WORKS.md` — plain-English explainer (non-technical).
- `references/DATA_MODEL.md` — the complete baked mapping (every DB, table, column, IndexedDB store).
- `references/RUNBOOK.md` — the proven, tested step-by-step (what was validated, and how).

## Limitations (be honest with the user)

- **Images: best-effort.** SeaTalk keeps **no** image files on disk; images live on `f.haiserve.com` and are cached transiently (~20 MB rolling Chromium cache). Any image **viewed in-app recently** is recoverable as a clean PNG/JPEG; images never opened or since evicted are **not** local. There's no stored key tying a cached image to a specific message (best-effort match is by dimensions + byte size), so `--images` dumps all recoverable images with a manifest rather than inline-per-message.
- **Names/emails** resolve from a directory snapshot (~34k users); a brand-new contact may show as `uid <n>` until SeaTalk syncs.
- **App-version coupling.** If SeaTalk changes its key prefix or cipher in a future update, decryption fails loudly; the script auto-checks the app's current prefix and warns if the baked one drifted. Re-run against the new `*_bundle.asar` if so (see RUNBOOK).
- **`~0.4%` of IndexedDB records** are un-parseable by ccl and skipped (logged).

## SENDING is not part of this skill

This skill only **reads**. Sending a message **as you** cannot be done headlessly (SeaTalk's session is a device-bound token, not a portable cookie) — the only clean options are driving the real desktop app (computer-use / Electron CDP, needs your machine) or the **SeaTalk Bot API** (sends as a *bot*, headless, team-scalable — see the existing `seatalk-bot-chat` skill). Keep read and send as separate skills.
