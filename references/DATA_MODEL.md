# SeaTalk local data model — the baked mapping

Verified by reverse-engineering + decryption on SeaTalk desktop **v2.9.5** (Electron 39.8.10 / Chrome 142) on macOS, user id `334439`. This is the map the scripts rely on; keep it with the code.

## 0. Locations & the key

- Data root: macOS `~/Library/Application Support/SeaTalk/`; Windows `%APPDATA%\SeaTalk\` (e.g. `C:\Users\<you>\AppData\Roaming\SeaTalk\`). Same file names, same layout, same cipher/key — verified on Windows (SeaTalk v2.6.1) with identical decryption. The image cache also lives under `Cache\Cache_Data\` and `Network\`.
- Encrypted SQLite DBs: `main_<uid>.sqlite`, `search_<uid>.sqlite`, `rag_vec_<uid>.sqlite` (+ `-wal`/`-shm`).
- **Cipher:** SQLite3MultipleCiphers **default scheme (ChaCha20)** via `better-sqlite3-multiple-ciphers`. *Not* SQLCipher (that's why the SQLCipher CLI fails with "file is not a database").
- **Key:** `key = KEY_PREFIX + <uid>` where `KEY_PREFIX = "40a3884b8b032e6f"`.
  - Source of truth: `SeaTalk.app/Contents/Resources/*_bundle.asar`, which opens the DB with `db.pragma("key='40a3884b8b032e6f${e}'")` (`e` = userId). The prefix is a 16-hex constant; the userId comes from the DB filename.
  - Open read-only: `new Database(file, {readonly:true}); db.pragma("key='"+KEY+"'")`. No other cipher pragma needed (defaults match).

## 1. `main_<uid>.sqlite` — authoritative message store (17 tables)

| Table | Rows* | What it holds / key columns |
|---|---|---|
| `chat_message` | 331k | **The messages.** `sid` (conversation), `mid` (msg id), `u` (sender uid), `t` (type), `c` (content CLOB, JSON), `q` (quoted msg JSON), `rtmid` (thread root mid — non-`'0'` ⇒ a thread reply), `ts` (unix secs) |
| `chat_message_view` | — | Assembled view: chat_message + `_threadInfo`, `emojis`, `_messageEditHistory`, `_quoteEditHistory`, `_linkMetadata`, `_messageTranslation` |
| `thread_info` | 24k | Thread metadata per root `mid`: `totalReplyCount`, `latestReplyMid/Time`, `info` (JSON) |
| `emoji_info` | 17k | Reactions: `sid`,`mid`,`emojis` (JSON array `[{native,count,uids,selfClicked}]`) |
| `edit_history` | 94k | Message edit revisions (`editedMid`, `message`) |
| `recalled_message` | 8.8k | Un-sent/recalled messages (`sid`,`mid`) |
| `pinned_message` | 1.5k | Pins (`sid`,`mid`,`user`,`message`) |
| `link_metadata` | 16k | Link previews (`sid`,`mid`,`message`) |
| `message_translation` | 4.5k | Inline translations |
| `file_info` / `file_reference` | 29k | `fileId → mime, location` and `fileId → mid`. **`location` is empty except for files sent/downloaded from local disk** (points to `~/Downloads/...`), so it is *not* a media store |
| `notice_message` | 125 | System/channel notices |
| `session_info` | 1.2k | Per-`sid` read state: `unreadCount`, `consume` pointer |

\* counts from the reference machine; indicative only.

**Content (`c`) JSON:** text = `{"c":"...","tag":[...],"l":[urls]}` (mentions are inline `@Name` text). image = `{"w","h","url":<fileId>,"s":size}`. Similar shapes for `file`/`video`/`sticker`/`interactive`.

**Message types (`t`) seen:** `text, interactive, image, c.g.m, sticker.c, history, file, c.g.i.j, video, custom.deleted, sys.c.g.u.p, group.invitation, article, neocall.*, contact, audio, image.gif, …` (30 total). `c.g.*`/`sys.*` are group/system events.

**sid grammar:** `buddy-<uid>` = 1:1 chat · `group-<gid>` = group · `notice-…` = channels.

## 2. `search_<uid>.sqlite` — FTS5 keyword index

`fts_message` (FTS5 virtual table) + shadow tables. Columns map to `fts_message_content` as: `c0`=text, `c3`=mid, `c5`=sender uid, `c6`=type, `c7`=ts, `c8`=sid. Query with `LIKE` on `c0` (used by the skill) or `MATCH` on `fts_message` for ranked BM25.

## 3. `rag_vec_<uid>.sqlite` — on-device semantic index (bonus, unused)

Opens with the same key. Holds sqlite-vec embeddings (`messages_vector_chunks00`, `messages_metadatatext*`, `rag_session_sync`) for in-app semantic search. Not needed for history extraction.

## 4. IndexedDB — identity & group resolvers (Chromium LevelDB)

Path: `~/Library/Application Support/SeaTalk/IndexedDB/https_web.haiserve.com_0.indexeddb.leveldb/`
Uses Chromium's custom `idb_cmp1` comparator + V8 structured-clone values ⇒ **generic LevelDB readers fail**; use `ccl_chromium_reader`. Copy the dir (minus `LOCK`) before reading (the app locks it).

| IndexedDB DB → object store | What it gives |
|---|---|
| `com.seatalkweb.Contacts → userInfo2` (~34k) | **Full directory:** `userid → {name, email, seatalkId}` — the email/name resolver |
| `contacts → group-info` (~375) | `group-<id> → {name, owner, groupType}` — group name resolver |
| `contacts → group-members` (~350) | Roster: `groupId → [{id, role}]`. Role: `0`=member, `1`=admin, `2`/`10`=owner |
| `contacts → user-info` (~500) | Smaller per-conversation user map (fallback) |
| `contacts → expiring-groups` | Group retention/expiry + member counts |
| `com.seatalkweb.Credentials → seatalkCredential` | `fileServerToken` + self profile (auth is a token here, **not** a cookie) |
| `messages → chat-messages / thread-reply-messages` | A working cache of recent messages (SQLite is authoritative — prefer it) |

### 4a. Chat-list tabs & Contacts directory (the app's top filters + Contacts tab)

The tabs are defined in `contacts → chatlist-labels`: type `1`=All, `2`=Unread, `3`=Mentioned(@Me), `4`=For Later, `5`=Private, `7`=Threads. **Membership is NOT stored** (`chatlist-label-chats` is empty) — each view is *computed*:

| View | Computed from |
|---|---|
| **My Contacts** | `com.seatalkweb.Contacts → contactList.cl` (array of uids) → resolve via `userInfo2` |
| **Group Chats** | `contacts → group-list` (+ `group-info` for names) |
| **Bots** | `userInfo2` where `role == 3` (and `botInfo.serviceState == 1`). Directory has ~1.6k bots; the app's "Bots" tab is the followed/added subset |
| **Announcements** | `contacts → notice-channel-info` + `notice-app-info` (name/desc/kind) |
| **Unread** | SQLite `session_info.unreadCount > 0` (engine `op:unread`) |
| **@Me / Mentioned** | `messages → mentions` (recent, keyed `<self>-<sid>-<mid>`, `type 2`=unread mention) **and** a broad scan of `chat_message.c LIKE '%@<firstName>%'` (engine `op:mentions`) |
| **For Later** | `com.seatalkweb.LaterMessages → laterMessages.lm[]` — each `{ct,cid,mid,st}`; **`ct==1` ⇒ `group-<cid>`, `ct==0` ⇒ `buddy-<cid>`**; resolve `mid` → text via engine `op:by_mid` |
| **Threads** | SQLite `thread_info` (`unreadReplyCount>0` or `followed=1`, engine `op:threads`) + `messages → followed-threads` |
| **Private** | **Not resolvable offline** — server-side lock feature; no local membership. Return a limitation note. |

**Bot classifier:** person `role=0`, bot `role=3` (`botInfo.serviceState=1`, `hasSubsAuthority=true`, no email). Verified against JARVIS/Retriever (bots) vs Amanda/Clarence (people).

## 5. Images

Not stored as files. Served from `f.haiserve.com/download/?userid=<uid>&…&trace_id=<hex>` and cached transiently in the Chromium disk cache: `Cache/Cache_Data/` and `Partitions/*/Cache/Cache_Data/`. Cached bodies are **unencrypted** PNG/JPEG (carve by magic bytes → real files). No stored key links a cached image to a `mid` (cache key uses `trace_id`, message uses `fileId`) ⇒ best-effort match by `w×h` + byte size.

## 6. Multi-account & versioning

- Multiple logged-in users ⇒ multiple `main_<uid>.sqlite`; each uses its own uid in the key.
- `KEY_PREFIX` is per app **version**. `get_seatalk_history.py` re-reads the current `*_bundle.asar` prefix and warns if it drifts from the baked value — update the constant if SeaTalk changes it.
