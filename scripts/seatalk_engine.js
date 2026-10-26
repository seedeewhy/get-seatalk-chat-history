#!/usr/bin/env node
/*
 * seatalk_engine.js — decrypts SeaTalk's local SQLite DBs and emits structured JSON.
 *
 * SeaTalk encrypts main_<uid>.sqlite / search_<uid>.sqlite with SQLite3MultipleCiphers
 * (default ChaCha20 scheme) via better-sqlite3-multiple-ciphers. It opens them with:
 *     db.pragma("key='40a3884b8b032e6f<uid>'")
 * i.e. a fixed 16-hex prefix embedded in SeaTalk.app + the numeric user id.
 *
 * Usage:  node seatalk_engine.js '<jsonRequest>'
 *   request = {
 *     mainDb, searchDb,          // absolute paths to the (copied) DB files
 *     key,                        // full key string (prefix + uid)
 *     op: "messages"|"conversations"|"fts",
 *     sid, limit, after, before, search, includeThreads
 *   }
 * Prints a JSON object to stdout.
 */
const path = require('path');
const os = require('os');
// os.homedir() is robust cross-platform (process.env.HOME is often unset on Windows).
const DEPS = path.join(os.homedir(), '.seatalk-skill', 'node_modules', 'better-sqlite3-multiple-ciphers');
let Database;
try { Database = require(DEPS); }
catch (e) { console.log(JSON.stringify({error: "engine_deps_missing", detail: "run scripts/setup.sh first ("+e.message+")"})); process.exit(3); }

const req = JSON.parse(process.argv[2] || '{}');
const S = v => Buffer.isBuffer(v) ? v.toString('utf8') : (v == null ? '' : String(v));

function open(file) {
  const db = new Database(file, {readonly: true});
  db.pragma(`key='${req.key}'`);
  return db;
}

// Render a message's content CLOB into a readable string + structured extras.
function render(t, cRaw) {
  const raw = S(cRaw);
  let text = '', extra = {};
  try {
    const j = JSON.parse(raw);
    if (typeof j.c === 'string') text = j.c;                     // plain text (mentions are inline "@Name")
    else if (t === 'image') { text = `[image${j.w?` ${j.w}x${j.h}`:''}]`; extra.fileId = j.url; extra.size = j.s; }
    else if (t === 'video') { text = '[video]'; extra.fileId = j.url; }
    else if (t === 'file')  { text = `[file${j.name?`: ${j.name}`:''}]`; extra.fileId = j.url; extra.fileName = j.name; }
    else if (t && t.startsWith('sticker')) text = '[sticker]';
    else if (t && t.startsWith('neocall')) text = '[call]';
    else if (t === 'interactive') text = j.title ? `[card] ${j.title}` : '[interactive card]';
    else if (typeof j.c === 'object' && j.c) text = j.c.text || JSON.stringify(j.c);
    else text = raw.length > 500 ? `[${t}]` : raw;
    if (j.l && j.l.length) extra.links = j.l;                    // link previews / urls
  } catch (e) { text = raw; }
  return {text, extra};
}

function messages(db, main) {
  const A = req.after || 0, B = req.before || 9999999999;
  let sql = "SELECT ts,u,t,c,q,mid,rtmid FROM chat_message WHERE sid=@sid AND ts BETWEEN @A AND @B";
  const p = {sid: req.sid, A, B};
  if (req.search) { sql += " AND CAST(c AS TEXT) LIKE @q"; p.q = '%'+req.search+'%'; }
  sql += " ORDER BY ts DESC LIMIT @lim"; p.lim = req.limit || 50;
  const rows = db.prepare(sql).all(p).reverse();

  // reactions + thread meta for these mids
  const react = {}, threads = {};
  const em = db.prepare("SELECT mid,emojis FROM emoji_info WHERE sid=?").all(req.sid);
  for (const e of em) { try { react[e.mid] = JSON.parse(S(e.emojis)).map(x=>({emoji:x.native,count:x.count,uids:x.uids})); } catch {} }
  for (const ti of db.prepare("SELECT mid,totalReplyCount,latestReplyTime FROM thread_info WHERE sid=?").all(req.sid))
    threads[ti.mid] = {replyCount: ti.totalReplyCount, latestReplyTime: ti.latestReplyTime};

  const out = rows.map(r => {
    const {text, extra} = render(r.t, r.c);
    const m = {ts: r.ts, uid: r.u, type: r.t, mid: r.mid, text, ...extra};
    if (S(r.q)) { try { const qj = JSON.parse(S(r.q)); m.quote = (qj.c && (qj.c.c||qj.c)) || null; } catch {} }
    if (react[r.mid]) m.reactions = react[r.mid];
    if (threads[r.mid]) m.thread = threads[r.mid];
    if (req.includeThreads && threads[r.mid]) {
      m.replies = db.prepare("SELECT ts,u,t,c,mid FROM chat_message WHERE sid=? AND rtmid=? ORDER BY ts ASC").all(req.sid, r.mid)
        .map(x => { const rr = render(x.t, x.c); return {ts:x.ts, uid:x.u, type:x.t, mid:x.mid, text: rr.text}; });
    }
    return m;
  });
  return out;
}

function conversations(db) {
  // per-sid message counts + latest ts, joined with unread from session_info
  const rows = db.prepare(`
    SELECT cm.sid,
           count(*) AS messages,
           max(cm.ts) AS latestTs,
           coalesce(si.unreadCount,0) AS unread
    FROM chat_message cm LEFT JOIN session_info si ON si.sid=cm.sid
    GROUP BY cm.sid ORDER BY latestTs DESC`).all();
  return rows;
}

function unread(db) {
  // conversations with unread messages (the "Unread" tab), newest activity first
  return db.prepare(`
    SELECT si.sid, si.unreadCount AS unread,
           (SELECT max(ts) FROM chat_message WHERE sid=si.sid) AS latestTs,
           (SELECT c FROM chat_message WHERE sid=si.sid ORDER BY ts DESC LIMIT 1) AS lastC,
           (SELECT t FROM chat_message WHERE sid=si.sid ORDER BY ts DESC LIMIT 1) AS lastT
    FROM session_info si WHERE si.unreadCount > 0
    ORDER BY latestTs DESC`).all().map(r => {
      const {text} = render(r.lastT, r.lastC);
      return {sid: r.sid, unread: r.unread, latestTs: r.latestTs, preview: text.slice(0, 80)};
    });
}

function threads(db) {
  // active threads (the "Threads" tab): unread replies first, then most recent
  return db.prepare(`
    SELECT ti.sid, ti.mid, ti.totalReplyCount AS replies, ti.unreadReplyCount AS unreadReplies,
           ti.unreadMentionCount AS unreadMentions, ti.latestReplyTime AS latestReplyTime,
           (SELECT c FROM chat_message WHERE sid=ti.sid AND mid=ti.mid) AS rootC,
           (SELECT t FROM chat_message WHERE sid=ti.sid AND mid=ti.mid) AS rootT,
           (SELECT u FROM chat_message WHERE sid=ti.sid AND mid=ti.mid) AS rootU
    FROM thread_info ti
    WHERE ti.unreadReplyCount > 0 OR ti.followed = 1
    ORDER BY ti.unreadReplyCount DESC, ti.latestReplyTime DESC
    LIMIT @lim`).all({lim: req.limit || 50}).map(r => {
      const {text} = render(r.rootT, r.rootC);
      return {sid: r.sid, rootMid: r.mid, uid: r.rootU, replies: r.replies,
              unreadReplies: r.unreadReplies, unreadMentions: r.unreadMentions,
              latestReplyTime: r.latestReplyTime, root: text.slice(0, 90)};
    });
}

function byMid(db) {
  // resolve a list of {sid?,mid} to full messages (for Later / @Me lists)
  const out = [];
  const q = db.prepare("SELECT sid,ts,u,t,c FROM chat_message WHERE mid=? " +
                       (req.reqSid ? "AND sid=? " : "") + "LIMIT 1");
  for (const item of (req.items || [])) {
    const row = req.reqSid ? q.get(item.mid, item.sid) : q.get(item.mid);
    if (!row) { out.push({mid: item.mid, sid: item.sid || null, found: false}); continue; }
    const {text} = render(row.t, row.c);
    out.push({mid: item.mid, sid: row.sid, ts: row.ts, uid: row.u, type: row.t, text, note: item.note});
  }
  return out;
}

function mentionsScan(db) {
  // broad "@Me" scan: messages whose text @mentions the user's display name
  const name = (req.selfName || '').split(' ')[0];   // first-name token, robust to suffixes
  if (!name) return [];
  return db.prepare(
    "SELECT sid,ts,u,t,c,mid FROM chat_message WHERE CAST(c AS TEXT) LIKE @q ORDER BY ts DESC LIMIT @lim")
    .all({q: '%@'+name+'%', lim: req.limit || 50})
    .map(r => { const {text} = render(r.t, r.c); return {sid:r.sid, ts:r.ts, uid:r.u, mid:r.mid, text}; });
}

function fts(searchDb) {
  // full-text search over the FTS5 index; c0=text, c5=sender, c7=ts, c8=sid
  let sql = "SELECT c8 sid, c5 uid, c7 ts, c0 text, c3 mid FROM fts_message_content WHERE c0 LIKE @q";
  const p = {q: '%'+req.search+'%'};
  if (req.sid) { sql += " AND c8=@sid"; p.sid = req.sid; }
  sql += " ORDER BY c7 DESC LIMIT @lim"; p.lim = req.limit || 50;
  return searchDb.prepare(sql).all(p).map(r => ({sid:r.sid, uid:r.uid, ts:r.ts, mid:r.mid, text: S(r.text)}));
}

try {
  let result;
  if (req.op === 'conversations') { const db = open(req.mainDb); result = conversations(db); db.close(); }
  else if (req.op === 'unread')   { const db = open(req.mainDb); result = unread(db); db.close(); }
  else if (req.op === 'threads')  { const db = open(req.mainDb); result = threads(db); db.close(); }
  else if (req.op === 'by_mid')   { const db = open(req.mainDb); result = byMid(db); db.close(); }
  else if (req.op === 'mentions') { const db = open(req.mainDb); result = mentionsScan(db); db.close(); }
  else if (req.op === 'fts')      { const db = open(req.searchDb); result = fts(db); db.close(); }
  else                            { const db = open(req.mainDb); result = messages(db, true); db.close(); }
  console.log(JSON.stringify({ok: true, op: req.op || 'messages', data: result}));
} catch (e) {
  console.log(JSON.stringify({error: "engine_failed", detail: e.message}));
  process.exit(1);
}
