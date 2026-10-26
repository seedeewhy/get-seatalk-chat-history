#!/usr/bin/env python3
"""
resolvers.py — build identity/group resolvers from SeaTalk's Chromium IndexedDB.

Group names, Shopee emails and rosters do NOT live in the SQLite DBs — they live in
the IndexedDB LevelDB (custom `idb_cmp1` comparator + V8 structured-clone values),
readable only with ccl_chromium_reader.

Sources (db -> object store):
  com.seatalkweb.Contacts -> userInfo2   : full directory (uid -> name, email, seatalkId)  ~34k
  contacts               -> group-info   : sid -> group name, owner, type
  contacts               -> group-members: group roster (uid + role)

Produces a JSON blob:  {users:{uid:[name,email,seatalkId]}, groups:{"group-<id>":name},
                        rosters:{"group-<id>":[[uid,role],...]}}
Cached at ~/.seatalk-skill/cache/resolvers_<uid>.json (per-machine; keeps PII out of shared folder).
"""
import sys, os, json, glob, shutil, tempfile, time

def bad(*a, **k):  # skip records ccl can't V8-deserialize (~0.4%)
    return None

def clean(x):
    """Coerce ccl values (which may be Undefined/other sentinels) to JSON-safe."""
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    s = str(x)
    return None if s in ("<Undefined>", "Undefined", "<null>") else s

def build(idb_dir):
    from ccl_chromium_reader.ccl_chromium_indexeddb import WrappedIndexDB
    w = WrappedIndexDB(idb_dir)
    dbs = {d.name: w[d.dbid_no] for d in w.database_ids}
    users, groups, rosters = {}, {}, {}

    bots = {}          # uid -> name  for role==3 accounts (bots)
    # directory (name / email / seatalkId)  +  bot classification
    try:
        st = dbs["com.seatalkweb.Contacts"].get_object_store_by_name("userInfo2")
        for rec in st.iterate_records(bad_deserializer_data_handler=bad):
            try: v = rec.value
            except Exception: continue
            if v and v.get("userid") is not None:
                uid = str(v["userid"])
                users[uid] = [clean(v.get("name")), clean(v.get("email")), clean(v.get("seatalkId"))]
                if v.get("role") == 3:          # role 3 = bot / service account
                    bots[uid] = clean(v.get("name"))
    except Exception as e:
        sys.stderr.write(f"[resolvers] userInfo2 warn: {e}\n")

    # My Contacts list (uids) — com.seatalkweb.Contacts/contactList.cl
    contacts = []
    try:
        st = dbs["com.seatalkweb.Contacts"].get_object_store_by_name("contactList")
        for rec in st.iterate_records(bad_deserializer_data_handler=bad):
            try: v = rec.value
            except Exception: continue
            if v and v.get("cl"):
                contacts = [str(u) for u in v["cl"]]
                break
    except Exception:
        pass

    # Announcement / notice channels + apps — contacts/notice-channel-info, notice-app-info
    channels = []
    for sn, fld in (("notice-channel-info", "channel"), ("notice-app-info", "app")):
        try:
            for rec in dbs["contacts"].get_object_store_by_name(sn).iterate_records(bad_deserializer_data_handler=bad):
                try: v = rec.value
                except Exception: continue
                obj = (v or {}).get(fld, {})
                if isinstance(obj, dict) and obj.get("name"):
                    channels.append({"id": clean(obj.get("id")), "name": clean(obj.get("name")),
                                     "desc": clean(obj.get("desc")), "kind": fld})
        except Exception:
            pass

    # smaller per-conversation user-info (covers a few not in userInfo2)
    try:
        st = dbs["contacts"].get_object_store_by_name("user-info")
        for rec in st.iterate_records(bad_deserializer_data_handler=bad):
            try: v = rec.value
            except Exception: continue
            u = (v or {}).get("user")
            if isinstance(u, dict) and u.get("id") is not None:
                users.setdefault(str(u["id"]), [clean(u.get("name")), clean(u.get("email")), clean(u.get("seatalkId"))])
    except Exception:
        pass

    # group id -> name / owner
    try:
        st = dbs["contacts"].get_object_store_by_name("group-info")
        for rec in st.iterate_records(bad_deserializer_data_handler=bad):
            try: v = rec.value
            except Exception: continue
            g = (v or {}).get("group")
            if g and g.get("id") is not None:
                groups[f"group-{g['id']}"] = {"name": clean(g.get("name")), "owner": clean(g.get("owner"))}
    except Exception as e:
        sys.stderr.write(f"[resolvers] group-info warn: {e}\n")

    # group roster (uid + role)
    try:
        st = dbs["contacts"].get_object_store_by_name("group-members")
        for rec in st.iterate_records(bad_deserializer_data_handler=bad):
            try: v = rec.value
            except Exception: continue
            if v and v.get("groupId") is not None and v.get("members"):
                rosters[f"group-{v['groupId']}"] = [[m.get("id"), m.get("role")] for m in v["members"]]
    except Exception:
        pass

    return {"users": users, "groups": groups, "rosters": rosters,
            "bots": bots, "contacts": contacts, "channels": channels,
            "built_at": int(time.time())}

def main():
    idb_dir = sys.argv[1]      # copied IndexedDB leveldb dir
    out_path = sys.argv[2]     # cache json path
    data = build(idb_dir)
    tmp = out_path + ".tmp"                         # atomic write — never leave a partial cache
    with open(tmp, "w", encoding="utf-8") as f:     # utf-8 explicit: Windows default cp1252 can't encode CJK/emoji
        json.dump(data, f, default=str, ensure_ascii=False)
    os.replace(tmp, out_path)
    sys.stderr.write(f"[resolvers] {len(data['users'])} users, {len(data['groups'])} groups, "
                     f"{len(data['rosters'])} rosters, {len(data['bots'])} bots, "
                     f"{len(data['contacts'])} contacts, {len(data['channels'])} channels\n")

if __name__ == "__main__":
    main()
