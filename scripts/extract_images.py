#!/usr/bin/env python3
"""
extract_images.py — recover chat images from SeaTalk's Chromium HTTP cache.

SeaTalk does NOT store chat images as files on disk; it fetches them from
f.haiserve.com and keeps them transiently in the Chromium disk cache
(unencrypted). Any image VIEWED in the app recently is recoverable here.
Images never opened, or evicted from the ~20MB rolling cache, are NOT local
(they'd need a live authenticated re-fetch — out of scope).

There is no stored key linking a cached image to a specific message (the cache
URL carries a request `trace_id`, the message carries a `fileId`). So this dumps
ALL recoverable images; best-effort message linkage is by width×height + byte size.

Usage: python3 extract_images.py <out_dir>
"""
import sys, os, glob, re

if sys.platform == "win32":
    ST = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~/AppData/Roaming")), "SeaTalk")
elif sys.platform == "darwin":
    ST = os.path.expanduser("~/Library/Application Support/SeaTalk")
else:
    ST = os.path.expanduser("~/.config/SeaTalk")

def caches():
    yield os.path.join(ST, "Cache", "Cache_Data")
    yield os.path.join(ST, "Network")          # Windows/newer Chromium also caches bodies here
    for p in glob.glob(os.path.join(ST, "Partitions", "*", "Cache", "Cache_Data")):
        yield p
    for p in glob.glob(os.path.join(ST, "Partitions", "*", "Network")):
        yield p

def carve(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    manifest = []
    for cdir in caches():
        if not os.path.isdir(cdir):
            continue
        for f in glob.glob(os.path.join(cdir, "*")):
            if os.path.isdir(f):
                continue
            try:
                data = open(f, "rb").read()
            except Exception:
                continue
            url_m = re.search(rb'https?://[ -~]{6,300}', data[:600])
            url = url_m.group(0).decode("latin1") if url_m else ""
            pi = data.find(b'\x89PNG\r\n\x1a\n')
            ji = data.find(b'\xff\xd8\xff')
            body = ext = None
            if pi != -1:
                end = data.find(b'IEND', pi)
                if end != -1:
                    body, ext = data[pi:end + 8], "png"
            elif ji != -1:
                end = data.rfind(b'\xff\xd9')
                if end != -1 and end > ji:
                    body, ext = data[ji:end + 2], "jpg"
            if body:
                name = f"seatalk_img_{n:04d}.{ext}"
                open(os.path.join(out_dir, name), "wb").write(body)
                manifest.append({"file": name, "bytes": len(body), "url": url[:200]})
                n += 1
    with open(os.path.join(out_dir, "_manifest.json"), "w", encoding="utf-8") as mf:
        import json
        json.dump(manifest, mf, indent=2, ensure_ascii=False)
    return n

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "./seatalk_images"
    count = carve(out)
    print(f"recovered {count} image(s) -> {out}")
    print("note: best-effort; only images viewed in-app & still cached are present.")
