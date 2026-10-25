#!/usr/bin/env python3
"""Bobby Hobby stock sync: card pictures.

Run after sync.py has rewritten index.html. Every card row on the stock site needs a cell in a
sprite sheet (sprites/sNN.webp, 8 x 3 cells, mapped by `const SPR = {...}` in index.html), or the
site shows a placeholder. For each row with no SPR entry this script:
  1. looks the card up in the Riftcodex API by its Riftbound id (cards.json "rid"),
  2. downloads media.image_url,
  3. pastes it into the next free cell (last sheet, or a new sheet when it is full),
  4. adds the id to SPR in index.html.
A picture placed in --local DIR as <rid>.png/.jpg/.webp (or <site id>.*) is used instead of the
download. Prints JSON: {"added": [...], "failed": [{"id", "rid", "reason"}], "sheets": [...]}.
Exit code 0 even when some pictures fail; the agent reports the failures.
"""
import argparse, glob, io, json, os, re, ssl, sys, urllib.request

REPO = "/home/claude/bobbyhobby-stock"
API = "https://api.riftcodex.com/cards/riftbound/"
COLS, ROWS = 8, 3
PER = COLS * ROWS
CA = "/root/.ccr/ca-bundle.crt"
UA = {"User-Agent": "bobbyhobby-stock-sync/1.0", "Accept": "application/json"}


def ctx():
    return ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()


def get(url, accept_json):
    req = urllib.request.Request(url, headers=UA if accept_json else {"User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=40, context=ctx()) as r:
        return r.read()


def image_url(rid):
    data = json.loads(get(API + rid, True))
    items = data if isinstance(data, list) else data.get("items") or data.get("data") or ([data] if "riftbound_id" in data else [])
    exact = [c for c in items if (c.get("riftbound_id") or "").lower() == rid.lower()]
    if not exact:
        raise LookupError("not in Riftcodex")
    url = (exact[0].get("media") or {}).get("image_url")
    if not url:
        raise LookupError("Riftcodex has no image")
    return url


def listing_base(c):
    return (c["set"] + "-" + c["code"]).lower().replace("*", "s")


def main():
    from PIL import Image
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--local", default=None, help="dir of pictures to use instead of downloading")
    ap.add_argument("--dry-run", action="store_true", help="only list rows without a picture")
    a = ap.parse_args()
    site = os.path.join(a.repo, "index.html")
    sdir = os.path.join(a.repo, "sprites")

    h = open(site, encoding="utf-8").read()
    data = json.JSONDecoder().raw_decode(h[h.index("const DATA = ") + len("const DATA = "):])[0]
    m = re.search(r"const SPR\s*=\s*", h)
    spr, spr_len = json.JSONDecoder().raw_decode(h[m.end():])
    by_base = {listing_base(c): c for c in json.load(open(os.path.join(a.repo, "sync", "cards.json")))}

    missing = [r["id"] for r in data["rows"] if r["id"] not in spr]
    out = {"added": [], "failed": [], "sheets": []}
    if a.dry_run or not missing:
        out["missing"] = missing
        print(json.dumps(out, indent=1))
        return

    # next free cell = one after the highest used cell across all sheets
    used = max((s * PER + i for s, i in spr.values()), default=-1)
    nxt = used + 1
    first = Image.open(os.path.join(sdir, "s00.webp"))
    W, H = first.size
    cw, ch = W // COLS, H // ROWS
    sheets = {}

    def sheet(n):
        if n not in sheets:
            p = os.path.join(sdir, f"s{n:02d}.webp")
            sheets[n] = Image.open(p).convert("RGB") if os.path.exists(p) else Image.new("RGB", (W, H), (0, 0, 0))
        return sheets[n]

    for sid in missing:
        c = by_base.get(sid)
        rid = c["rid"] if c else None
        try:
            if not c:
                raise LookupError("not in cards.json")
            src = None
            if a.local:
                for stem in (rid, sid):
                    hit = sorted(glob.glob(os.path.join(a.local, glob.escape(stem) + ".*")))
                    if hit:
                        src = open(hit[0], "rb").read()
                        break
            if src is None:
                src = get(image_url(rid), False)
            im = Image.open(io.BytesIO(src)).convert("RGB")
            if im.width > im.height:   # landscape art (battlefields): fit to the cell's width
                im = im.rotate(90, expand=True)
            im = im.resize((cw, ch), Image.LANCZOS)
        except Exception as e:  # network blocked, 404, bad image ...
            out["failed"].append({"id": sid, "rid": rid, "reason": f"{type(e).__name__}: {e}"[:200]})
            continue
        n, i = divmod(nxt, PER)
        sheet(n).paste(im, ((i % COLS) * cw, (i // COLS) * ch))
        spr[sid] = [n, i]
        out["added"].append({"id": sid, "rid": rid, "cell": [n, i]})
        nxt += 1

    for n, im in sheets.items():
        im.save(os.path.join(sdir, f"s{n:02d}.webp"), "WEBP", quality=82, method=6)
        out["sheets"].append(f"sprites/s{n:02d}.webp")
    if out["added"]:
        h = h[:m.end()] + json.dumps(spr, separators=(",", ":")) + h[m.end() + spr_len:]
        open(site, "w", encoding="utf-8").write(h)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
