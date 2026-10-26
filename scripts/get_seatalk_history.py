#!/usr/bin/env python3
"""
get_seatalk_history.py — read your own SeaTalk chat history (decrypted, resolved).

Everything needed is baked in (key derivation, cipher, data-model mapping) so no
re-discovery is required. Read-only: never writes to the live SeaTalk files.

Examples
  # list your groups/chats (names resolved), newest first
  python3 get_seatalk_history.py --list-groups

  # latest 50 messages in a group, with names + emails + threads
  python3 get_seatalk_history.py --group "AI Champions" --limit 50 --threads

  # a 1:1 chat by the person's Shopee email
  python3 get_seatalk_history.py --email amanda.thng@shopee.com --limit 40

  # keyword search (FTS5) across everything, or within one group
  python3 get_seatalk_history.py --search "legal compliance" --group "AI Champions"

  # show a group's roster (members + roles + emails)
  python3 get_seatalk_history.py --group "AI Champions" --roster

  # also recover viewed images from cache
  python3 get_seatalk_history.py --group "AI Champions" --images ./out_imgs

  # machine-readable
  python3 get_seatalk_history.py --group "AI Champions" --format json
"""
import argparse, os, sys, glob, json, shutil, tempfile, subprocess, time, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
# SeaTalk data dir + venv python location differ per OS.
if sys.platform == "win32":
    ST = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~/AppData/Roaming")), "SeaTalk")
    _VENV_SUB = ("venv", "Scripts", "python.exe")
elif sys.platform == "darwin":
    ST = os.path.expanduser("~/Library/Application Support/SeaTalk")
    _VENV_SUB = ("venv", "bin", "python3")
else:  # linux / other
    ST = os.path.expanduser("~/.config/SeaTalk")
    _VENV_SUB = ("venv", "bin", "python3")
DEPS = os.path.expanduser("~/.seatalk-skill")
CACHE = os.path.join(DEPS, "cache")
VENV_PY = os.path.join(DEPS, *_VENV_SUB)
NODE_BIN_FILE = os.path.join(DEPS, "node_bin")

# Force UTF-8 for our own stdio and for every child we spawn, so non-Latin
# group names / emoji / CJK don't crash on Windows' default cp1252 locale.
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except Exception: pass

def _reexec_into_venv_if_needed():
    """setup.sh installs ccl_chromium_reader into ~/.seatalk-skill/venv whenever the
    caller's python3 is below the reader's 3.10 floor. Re-exec under that venv so
    this script (and its sys.executable-based children: resolvers.py, views_idb.py,
    extract_images.py) work no matter which python3 invoked it or from where."""
    if os.environ.get("_SEATALK_SKILL_REEXEC"):
        return
    try:
        import ccl_chromium_reader  # noqa: F401
        return
    except Exception:
        pass
    if os.path.exists(VENV_PY) and os.path.abspath(sys.executable) != os.path.abspath(VENV_PY):
        os.environ["_SEATALK_SKILL_REEXEC"] = "1"
        os.execv(VENV_PY, [VENV_PY] + sys.argv)
    # No venv to fall back to — let resolvers.py's own import error surface later.

_reexec_into_venv_if_needed()

def node_bin():
    """Resolve a Node binary known (by setup.sh) to load the compiled cipher module.
    Falls back to plain 'node' on PATH if setup.sh hasn't recorded one yet."""
    if os.path.exists(NODE_BIN_FILE):
        p = open(NODE_BIN_FILE).read().strip()
        if p and os.path.exists(p):
            return p
    return "node"

# ── BAKED-IN SECRETS / CONSTANTS (approved) ─────────────────────────────────
# SeaTalk opens its SQLite DBs with:  key = KEY_PREFIX + <userId>
# KEY_PREFIX is a 16-hex constant embedded in SeaTalk.app (*_bundle.asar),
# identical across installs of this app version — not a personal secret, and
# useless without OS access to your own (0600) SeaTalk files.
KEY_PREFIX = "40a3884b8b032e6f"
# Cipher: SQLite3MultipleCiphers default (ChaCha20) via better-sqlite3-multiple-ciphers.

ROLE = {0: "member", 1: "admin", 2: "owner", 10: "owner"}

def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr); sys.exit(code)

def detect_user_id():
    files = glob.glob(os.path.join(ST, "main_*.sqlite"))
    ids = [os.path.basename(f).split("_")[1].split(".")[0] for f in files]
    if not ids:
        die(f"no SeaTalk DB found under {ST} — is SeaTalk installed / logged in?")
    return ids[0], files[0]

def verify_prefix():
    """Best-effort: confirm the baked prefix still matches this app version."""
    if sys.platform == "win32":
        asar_globs = [os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~/AppData/Local")),
                                   "SeaTalk", "app-*", "resources", "*_bundle.asar")]
    elif sys.platform == "darwin":
        asar_globs = ["/Applications/SeaTalk.app/Contents/Resources/*_bundle.asar"]
    else:
        asar_globs = []
    for asar in [p for g in asar_globs for p in glob.glob(g)]:
        try:
            data = open(asar, "rb").read()
        except Exception:
            continue
        import re
        m = re.search(rb"key='([0-9a-f]{8,})", data)
        if m:
            found = m.group(1).decode()[:16]
            return found
    return None

def snapshot(user_id):
    """Copy the encrypted DBs + IndexedDB to a temp dir so the live app is untouched."""
    work = tempfile.mkdtemp(prefix="seatalk_snap_")
    for base in (f"main_{user_id}.sqlite", f"search_{user_id}.sqlite"):
        for suf in ("", "-wal", "-shm"):
            src = os.path.join(ST, base + suf)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(work, base + suf))
    # IndexedDB leveldb (skip the LOCK so a copy is openable)
    idb_src = os.path.join(ST, "IndexedDB", "https_web.haiserve.com_0.indexeddb.leveldb")
    idb_dst = os.path.join(work, "idb")
    os.makedirs(idb_dst, exist_ok=True)
    for f in glob.glob(os.path.join(idb_src, "*")):
        if os.path.basename(f) == "LOCK":
            continue
        try: shutil.copy2(f, idb_dst)
        except Exception: pass
    return work, os.path.join(work, f"main_{user_id}.sqlite"), os.path.join(work, f"search_{user_id}.sqlite"), idb_dst

def get_resolvers(user_id, idb_dir, max_age_h=12):
    os.makedirs(CACHE, exist_ok=True)
    cache_path = os.path.join(CACHE, f"resolvers_{user_id}.json")
    def load_valid():
        try:
            with open(cache_path, encoding="utf-8") as f:   # cache is utf-8; parent open() defaults to cp1252 on Windows
                return json.load(f)
        except Exception:
            return None
    fresh = os.path.exists(cache_path) and (time.time() - os.path.getmtime(cache_path) < max_age_h * 3600)
    if fresh:
        cached = load_valid()
        if cached is not None:
            return cached
    r = subprocess.run([sys.executable, os.path.join(HERE, "resolvers.py"), idb_dir, cache_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    sys.stderr.write(r.stderr)
    cached = load_valid()
    return cached if cached is not None else {"users": {}, "groups": {}, "rosters": {}}

def engine(req):
    r = subprocess.run([node_bin(), os.path.join(HERE, "seatalk_engine.js"), json.dumps(req)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        die(f"engine error: {r.stdout}\n{r.stderr}")
    if out.get("error"):
        die(f"engine: {out['error']} — {out.get('detail','')}", 3)
    return out["data"]

def views_idb(idb_dir, which):
    r = subprocess.run([sys.executable, os.path.join(HERE, "views_idb.py"), idb_dir, which],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return json.loads(r.stdout.strip() or "[]")
    except Exception:
        sys.stderr.write(r.stderr); return []

def conv_name(res, sid):
    nm = res["groups"].get(sid, {}).get("name")
    if not nm and sid.startswith("buddy-"):
        nm = who(res, sid.split("-")[1])[0]
    return nm or sid

def who(res, uid):
    u = res["users"].get(str(uid))
    if not u: return (f"uid {uid}", "")
    return (u[0] or f"uid {uid}", u[1] or "")

def find_group(res, name):
    name_l = name.lower()
    exact = [(sid, g["name"]) for sid, g in res["groups"].items() if (g.get("name") or "").lower() == name_l]
    if exact: return exact
    return [(sid, g["name"]) for sid, g in res["groups"].items() if name_l in (g.get("name") or "").lower()]

def fmt_ts(ts):
    try: return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception: return str(ts)

def main():
    ap = argparse.ArgumentParser(description="Read your own SeaTalk chat history (decrypted).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--group", help="group name (substring, case-insensitive)")
    g.add_argument("--email", help="1:1 chat partner's Shopee email")
    g.add_argument("--sid", help="explicit conversation id, e.g. group-4003550 or buddy-73711")
    g.add_argument("--list-groups", action="store_true", help="list conversations, newest first")
    g.add_argument("--tab", choices=["unread", "mentions", "later", "threads", "private"],
                   help="a chat-list view tab: unread / mentions(@Me) / later / threads / private")
    g.add_argument("--directory", choices=["contacts", "groups", "bots", "announcements"],
                   help="a Contacts-tab directory: contacts / groups / bots / announcements")
    ap.add_argument("--roster", action="store_true", help="show group members + roles + emails")
    ap.add_argument("--search", help="keyword filter (FTS5 across all chats if no target given)")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--after", help="only messages after YYYY-MM-DD")
    ap.add_argument("--before", help="only messages before YYYY-MM-DD")
    ap.add_argument("--threads", action="store_true", help="expand thread replies")
    ap.add_argument("--images", metavar="DIR", help="also recover viewed images into DIR")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    args = ap.parse_args()

    if not os.path.isdir(os.path.join(DEPS, "node_modules")):
        die("dependencies not installed — run:  bash scripts/setup.sh", 2)

    user_id, _ = detect_user_id()
    key = KEY_PREFIX + user_id
    warn = ""
    fp = verify_prefix()
    if fp and fp != KEY_PREFIX:
        warn = f"⚠ baked key prefix ({KEY_PREFIX}) != app prefix ({fp}); using app value.\n"
        key = fp + user_id

    work, main_db, search_db, idb_dir = snapshot(user_id)
    try:
        res = get_resolvers(user_id, idb_dir)

        def to_ts(d):
            return int(datetime.datetime.strptime(d, "%Y-%m-%d").timestamp()) if d else None

        # ---- list-groups ----
        if args.list_groups:
            convos = engine({"mainDb": main_db, "key": key, "op": "conversations"})
            lines = ["# SeaTalk conversations (newest first)\n"]
            for c in convos[:args.limit]:
                nm = res["groups"].get(c["sid"], {}).get("name")
                if not nm and c["sid"].startswith("buddy-"):
                    nm = who(res, c["sid"].split("-")[1])[0]
                unread = f"  🔴{c['unread']}" if c.get("unread") else ""
                lines.append(f"- {c['sid']:16}  {fmt_ts(c['latestTs'])}  ({c['messages']} msgs){unread}  {nm or ''}")
            print(warn + ("\n".join(lines) if args.format == "md" else json.dumps(convos, indent=2)))
            return

        # ---- directory views (Contacts tab): contacts / groups / bots / announcements ----
        if args.directory:
            q = (args.search or "").lower()
            rows = []
            if args.directory == "contacts":
                for uid in res.get("contacts", []):
                    nm, em = who(res, uid)
                    if not q or q in (nm or "").lower() or q in (em or "").lower():
                        rows.append({"uid": uid, "name": nm, "email": em})
            elif args.directory == "bots":
                for uid, nm in res.get("bots", {}).items():
                    if not q or q in (nm or "").lower():
                        rows.append({"uid": uid, "name": nm})
            elif args.directory == "groups":
                for sid, gmeta in res.get("groups", {}).items():
                    nm = gmeta.get("name") or ""
                    if not q or q in nm.lower():
                        rows.append({"sid": sid, "name": nm})
            elif args.directory == "announcements":
                for ch in res.get("channels", []):
                    if not q or q in (ch.get("name") or "").lower():
                        rows.append(ch)
            if args.format == "json":
                print(json.dumps(rows, indent=2, ensure_ascii=False)); return
            head = {"contacts": "My Contacts", "groups": "Group Chats", "bots": "Bots",
                    "announcements": "Announcements"}[args.directory]
            out = [f"# {head}" + (f' matching "{args.search}"' if args.search else "") + f" — {len(rows)}\n"]
            for r in rows[:args.limit]:
                if args.directory == "contacts":
                    out.append(f"- {r['name']:34} {r['email'] or ''}")
                elif args.directory == "bots":
                    out.append(f"- 🤖 {r['name']}  (uid {r['uid']})")
                elif args.directory == "groups":
                    out.append(f"- {r['sid']:16} {r['name']}")
                else:
                    out.append(f"- 📣 {r['name']}  {('— '+r['desc']) if r.get('desc') else ''}")
            if len(rows) > args.limit:
                out.append(f"… +{len(rows)-args.limit} more (raise --limit)")
            print(warn + "\n".join(out)); return

        # ---- chat-list view tabs: unread / mentions / later / threads / private ----
        if args.tab:
            if args.tab == "unread":
                data = engine({"mainDb": main_db, "key": key, "op": "unread"})
                out = [f"# Unread — {len(data)} conversations\n"]
                for c in data:
                    out.append(f"- 🔴{c['unread']:<3} {fmt_ts(c['latestTs'])}  {conv_name(res, c['sid'])}: {c['preview']}")
                print(warn + ("\n".join(out) if args.format == "md" else json.dumps(data, indent=2, ensure_ascii=False))); return

            if args.tab == "threads":
                data = engine({"mainDb": main_db, "key": key, "op": "threads", "limit": args.limit})
                out = [f"# Threads (active/unread) — {len(data)}\n"]
                for t in data:
                    flag = f"🔴{t['unreadReplies']} " if t.get("unreadReplies") else ""
                    out.append(f"- {flag}{t['replies']} replies · {conv_name(res, t['sid'])} · {who(res, t['uid'])[0]}: {t['root']}")
                    out.append(f"    ↪ read with: --sid {t['sid']} --search <kw> --threads   (root mid {t['rootMid']})")
                print(warn + ("\n".join(out) if args.format == "md" else json.dumps(data, indent=2, ensure_ascii=False))); return

            if args.tab == "mentions":
                # broad scan: messages that @mention the user's name
                self_name = who(res, user_id)[0]
                data = engine({"mainDb": main_db, "key": key, "op": "mentions", "selfName": self_name, "limit": args.limit})
                out = [f"# @Me — messages mentioning {self_name} — {len(data)}\n"]
                for m in data:
                    out.append(f"- [{fmt_ts(m['ts'])}] {who(res, m['uid'])[0]} in {conv_name(res, m['sid'])}: {m['text'][:150]}")
                print(warn + ("\n".join(out) if args.format == "md" else json.dumps(data, indent=2, ensure_ascii=False))); return

            if args.tab == "later":
                items = views_idb(idb_dir, "later")
                resolved = engine({"mainDb": main_db, "key": key, "op": "by_mid", "items": items}) if items else []
                out = [f"# For Later — {len(resolved)} saved messages\n"]
                for m in sorted(resolved, key=lambda x: x.get("ts") or 0, reverse=True):
                    if m.get("found") is False:
                        out.append(f"- (message not in local store: {m['mid']})"); continue
                    out.append(f"- [{fmt_ts(m['ts'])}] {who(res, m['uid'])[0]} in {conv_name(res, m['sid'])}: {m['text'][:150]}")
                print(warn + ("\n".join(out) if args.format == "md" else json.dumps(resolved, indent=2, ensure_ascii=False))); return

            if args.tab == "private":
                print(warn + "# Private Chats\n\n"
                      "SeaTalk's 'Private Chats' is a server-side lock feature; membership is NOT stored "
                      "locally (the chatlist-label-chats table is empty), so it cannot be listed reliably "
                      "offline. Use the SeaTalk app for the authoritative Private list. "
                      "(1:1 direct chats are readable via --email / --sid buddy-<uid>.)")
                return

        # ---- resolve target sid ----
        sid, title = args.sid, args.sid
        if args.group:
            hits = find_group(res, args.group)
            if not hits: die(f'no group matching "{args.group}". Try --list-groups.')
            if len(hits) > 1:
                print("Multiple groups match — pick one with --sid:", file=sys.stderr)
                for s, n in hits[:12]: print(f"  {s}  {n}", file=sys.stderr)
                die("ambiguous group name")
            sid, title = hits[0]
        elif args.email:
            q = args.email.lower()
            # 1) exact email match wins outright
            uid = next((u for u, v in res["users"].items() if (v[1] or "").lower() == q), None)
            if not uid:
                # 2) otherwise fuzzy match on email OR name — and CONFIRM if ambiguous
                cand = [(u, v) for u, v in res["users"].items()
                        if q in (v[1] or "").lower() or q in (v[0] or "").lower()]
                # prefer contacts you actually have, to cut directory noise
                contacts_set = set(res.get("contacts", []))
                cand.sort(key=lambda x: (x[0] not in contacts_set, x[1][0] or ""))
                if not cand:
                    die(f'no contact matching "{args.email}". Try: --directory contacts --search "<name>"')
                if len(cand) > 1:
                    print(f'"{args.email}" matches multiple contacts — pick one and re-run with the exact email:', file=sys.stderr)
                    for u, v in cand[:15]:
                        mark = "★" if u in contacts_set else " "
                        print(f"  {mark} {v[0] or ('uid '+u):34} {v[1] or '(no email)'}", file=sys.stderr)
                    die("ambiguous contact — clarify with the user (★ = in your contacts)")
                uid = cand[0][0]
            sid, title = f"buddy-{uid}", who(res, uid)[0]

        # ---- roster ----
        if args.roster:
            if not sid or not sid.startswith("group-"): die("--roster needs a --group/--sid group")
            members = res["rosters"].get(sid, [])
            gmeta = res["groups"].get(sid, {})
            lines = [f"# Roster — {gmeta.get('name', sid)} ({sid}) — {len(members)} members\n"]
            for uid, role in members:
                nm, em = who(res, uid)
                lines.append(f"- {ROLE.get(role, role):7} {nm:34} {em}")
            print(warn + ("\n".join(lines) if args.format == "md" else json.dumps(
                [{"uid": u, "role": ROLE.get(r, r), "name": who(res, u)[0], "email": who(res, u)[1]} for u, r in members], indent=2)))
            return

        # ---- FTS-only (no target) ----
        if args.search and not sid:
            data = engine({"searchDb": search_db, "key": key, "op": "fts", "search": args.search, "limit": args.limit})
            lines = [f'# Search "{args.search}" (all chats)\n']
            for m in data:
                gnm = res["groups"].get(m["sid"], {}).get("name") or m["sid"]
                lines.append(f"[{fmt_ts(m['ts'])}] {who(res, m['uid'])[0]} in {gnm}: {m['text'][:160]}")
            print(warn + ("\n".join(lines) if args.format == "md" else json.dumps(data, indent=2)))
            return

        if not sid:
            die("say what to read: --group / --email / --sid / --list-groups")

        # resolve a friendly title (esp. when --sid was passed directly)
        if not title or title == sid:
            if sid.startswith("group-"):
                title = res["groups"].get(sid, {}).get("name") or sid
            elif sid.startswith("buddy-"):
                title = who(res, sid.split("-")[1])[0]

        # ---- messages in a conversation ----
        data = engine({"mainDb": main_db, "key": key, "op": "messages", "sid": sid,
                       "limit": args.limit, "after": to_ts(args.after), "before": to_ts(args.before),
                       "search": args.search, "includeThreads": bool(args.threads)})
        if args.format == "json":
            enriched = []
            for m in data:
                nm, em = who(res, m["uid"]); m["name"], m["email"] = nm, em
                enriched.append(m)
            print(json.dumps({"sid": sid, "title": title, "messages": enriched}, indent=2, ensure_ascii=False))
        else:
            out = [f"# {title or sid}  ({sid}) — latest {len(data)} messages\n"]
            last_day = ""
            for m in data:
                day = fmt_ts(m["ts"])[:10]
                if day != last_day: out.append(f"\n── {day} ──"); last_day = day
                nm = who(res, m["uid"])[0]
                react = ""
                if m.get("reactions"): react = "  " + " ".join(f"{r['emoji']}{r['count']}" for r in m["reactions"])
                thr = f"  🧵{m['thread']['replyCount']}" if m.get("thread") else ""
                q = f"  ⤷quote: {str(m['quote'])[:50]}" if m.get("quote") else ""
                out.append(f"  {fmt_ts(m['ts'])[11:]}  {nm:22} {m['text']}{react}{thr}{q}")
                for rep in m.get("replies", []):
                    out.append(f"       ⤷ {fmt_ts(rep['ts'])[11:]}  {who(res, rep['uid'])[0]:20} {rep['text']}")
            print(warn + "\n".join(out))

        if args.images:
            r = subprocess.run([sys.executable, os.path.join(HERE, "extract_images.py"), args.images],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            print("\n" + r.stdout.strip(), file=sys.stderr)
    finally:
        shutil.rmtree(work, ignore_errors=True)

if __name__ == "__main__":
    main()
