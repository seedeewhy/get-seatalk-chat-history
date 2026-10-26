#!/usr/bin/env python3
"""
views_idb.py — read the *dynamic* chat-list views that live in IndexedDB (not SQLite):
  later   : com.seatalkweb.LaterMessages/laterMessages  ("For Later" tab)
  followed: messages/followed-threads                    (followed threads for the "Threads" tab)
  mentions: messages/mentions                            (recent @mention records)

Usage: python3 views_idb.py <idb_dir> <later|followed|mentions>
Prints JSON list to stdout. Each item carries a `sid` and `mid` so the caller can
resolve the actual message text via the (SQLite) engine's by_mid op.
"""
import sys, json

def bad(*a, **k):
    return None

def main():
    idb_dir, which = sys.argv[1], sys.argv[2]
    from ccl_chromium_reader.ccl_chromium_indexeddb import WrappedIndexDB
    w = WrappedIndexDB(idb_dir)
    bn = {d.name: w[d.dbid_no] for d in w.database_ids}
    out = []

    if which == "later":
        st = bn["com.seatalkweb.LaterMessages"].get_object_store_by_name("laterMessages")
        for rec in st.iterate_records(bad_deserializer_data_handler=bad):
            try: v = rec.value
            except Exception: continue
            for e in v.get("lm", []):
                cid = e.get("cid")
                sid = f"group-{cid}" if e.get("ct") == 1 else f"buddy-{cid}"   # ct 1=group, 0=buddy
                out.append({"sid": sid, "mid": e.get("mid"), "savedAt": e.get("st")})
            break

    elif which == "followed":
        st = bn["messages"].get_object_store_by_name("followed-threads")
        for rec in st.iterate_records(bad_deserializer_data_handler=bad):
            try: v = rec.value
            except Exception: continue
            if v and v.get("sid"):
                out.append({"sid": v.get("sid"), "mid": v.get("rootMid"),
                            "latestReplyMid": str(v.get("latestReplyMid"))})

    elif which == "mentions":
        st = bn["messages"].get_object_store_by_name("mentions")
        for rec in st.iterate_records(bad_deserializer_data_handler=bad):
            try: v = rec.value
            except Exception: continue
            sid = (v.get("skey") or "").split("-", 1)[-1]   # strip leading "<selfuid>-"
            out.append({"sid": sid, "mid": v.get("mid"), "note": "unread-mention" if v.get("type") == 2 else str(v.get("type")),
                        "at": v.get("_createAt")})

    print(json.dumps(out, default=str))

if __name__ == "__main__":
    main()
