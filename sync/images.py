#!/usr/bin/env python3
"""Bobby Hobby stock sync: card pictures.

Run after sync.py has rewritten index.html. Every card row on the stock site needs a cell in a
sprite sheet (sprites/sNN.webp, 8 x 3 cells, mapped by `const SPR = {...}` in index.html), or the
site shows a placeholder. For each row with no SPR entry this script:
  1. looks the card up by its Riftbound id (cards.json "rid") in the Riftcodex API, or, when
     Riftcodex doesn't have it (e.g. a new set), in Riot's official card gallery page,
  2. downloads the card picture,
  3. pastes it into the next free cell (last sheet, or a new sheet when it is full),
  4. adds the id to SPR in index.html.
A picture placed in --local DIR as <rid>.png/.jpg/.webp (or <site id>.*) is used instead of the
download. Prints JSON: {"added": [...], "failed": [{"id", "rid", "reason"}], "sheets": [...]}.
Exit code 0 even when some pictures fail; the agent reports the failures.
"""
import argparse, glob, io, json, os, re, ssl, sys, urllib.request

REPO = "/home/claude/bobbyhobby-stock"
API = "https://api.riftcodex.com/cards/riftbound/"
GALLERY = "https://playriftbound.com/en-us/card-gallery/"
COLS, ROWS = 8, 3
PER = COLS * ROWS
CA = "/root/.ccr/ca-bundle.crt"
UA = {"User-Agent": "Mozilla/5.0 (bobbyhobby-stock-sync)", "Accept": "application/json"}
_gallery = None


def ctx():
    return ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()


def get(url, accept_json):
    req = urllib.request.Request(url, headers=UA if accept_json else {"User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=40, context=ctx()) as r:
        return r.read()


def riftcodex_url(rid):
    data = json.loads(get(API + rid, True))
    items = data if isinstance(data, list) else data.get("items") or data.get("data") or ([data] if "riftbound_id" in data else [])
    exact = [c for c in items if (c.get("riftbound_id") or "").lower() == rid.lower()]
    if not exact:
        raise LookupError("not in Riftcodex")
    url = (exact[0].get("media") or {}).get("image_url")
    if not url:
        raise LookupError("Riftcodex has no image")
    return url


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def gallery_url(rid):
    """Riot's card gallery embeds every card (id like "rad-133a-167") in its __NEXT_DATA__ JSON."""
    global _gallery
    if _gallery is None:
        h = get(GALLERY, False).decode("utf-8", "replace")
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
        if not m:
            raise LookupError("card gallery has no card data")
        _gallery = {}
        for d in _walk(json.loads(m.group(1))):
            if isinstance(d.get("id"), str) and "publicCode" in d and d["id"] not in _gallery:
                _gallery[d["id"].lower()] = d
    card = _gallery.get(rid.lower())
    if not card:
        raise LookupError("not in Riftcodex or the card gallery")
    best, area = None, 0   # the card picture = largest portrait image in the card (icons are 64x64)
    for d in _walk(card):
        url, dim = d.get("url"), d.get("dimensions") or {}
        w, h = dim.get("width") or 0, dim.get("height") or 0
        if isinstance(url, str) and url.startswith("http") and h > w >= 300 and w * h > area:
            best, area = url, w * h
    if not best:
        raise LookupError("card gallery has no picture for it")
    return best


def image_url(rid):
    try:
        return riftcodex_url(rid)
    except LookupError:
        return gallery_url(rid)


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
