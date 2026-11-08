#!/usr/bin/env python3
"""Bobby Hobby stock site: check every card picture against the official picture for its printing.

Runs on GitHub (workflow verify-pictures.yml), because only GitHub can reach the picture sources.
For each card on the site it downloads the picture for that exact printing (by its Riftbound id in
sync/cards.json, e.g. ven-041-166 for the regular Riven, Shattered and ven-171-166 for the
overnumbered one; "a" ids are alt arts, "*" signed) the same way images.py does, and compares it
with the sprite cell the site shows. A cell that looks like a different illustration (an alt art,
overnumbered or signed version instead of the listed one, or the other way round) is reported and,
with --apply, replaced in place; replaced sheets get their SPRV version bumped so browsers reload them.

usage: python3 verify_pictures.py --repo DIR [--apply] [--threshold 30]
Writes sync/verify-report.json: {"checked", "replaced": [...], "suspect": [...], "failed": [...], "scores": {id: score}}.
"""
import argparse, importlib.util, io, json, os, re, sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("images", os.path.join(HERE, "images.py"))
images = importlib.util.module_from_spec(spec); spec.loader.exec_module(images)


def thumb(im):
    from PIL import ImageOps
    return list(ImageOps.autocontrast(im.convert("L").resize((26, 36))).getdata())


def main():
    from PIL import Image
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--threshold", type=float, default=30.0)
    a = ap.parse_args()
    site = os.path.join(a.repo, "index.html"); sdir = os.path.join(a.repo, "sprites")
    h = open(site, encoding="utf-8").read()
    data = json.JSONDecoder().raw_decode(h[h.index("const DATA = ") + len("const DATA = "):])[0]
    m = re.search(r"const SPR\s*=\s*", h); spr, _ = json.JSONDecoder().raw_decode(h[m.end():])
    by_base = {images.listing_base(c): c for c in json.load(open(os.path.join(a.repo, "sync", "cards.json")))}
    names = {r["id"]: r["n"] for r in data["rows"]}
    ids = [r["id"] for r in data["rows"] if r["id"] in spr and r["id"] in by_base]

    sheets = {}
    def sheet(n):
        if n not in sheets:
            sheets[n] = Image.open(os.path.join(sdir, f"s{n:02d}.webp")).convert("RGB")
        return sheets[n]
    W, H = sheet(0).size; cw, ch = W // images.COLS, H // images.ROWS

    def fetch(sid):
        rid = by_base[sid]["rid"]
        try:
            im = Image.open(io.BytesIO(images.get(images.image_url(rid), False))).convert("RGB")
            if im.width > im.height:
                im = im.rotate(90, expand=True)
            return sid, rid, im.resize((cw, ch), Image.LANCZOS), None
        except Exception as e:
            return sid, rid, None, f"{type(e).__name__}: {e}"[:200]


    out = {"checked": 0, "threshold": a.threshold, "applied": a.apply, "replaced": [], "suspect": [], "failed": [], "scores": {}}
    bumped = set()
    with ThreadPoolExecutor(6) as ex:
        for sid, rid, im, err in ex.map(fetch, ids):
            if err:
                out["failed"].append({"id": sid, "rid": rid, "reason": err}); continue
            n, i = spr[sid]
            x, y = (i % images.COLS) * cw, (i // images.COLS) * ch
            cur = sheet(n).crop((x, y, x + cw, y + ch))
            ta, tb = thumb(cur), thumb(im)
            score = round(sum(abs(p - q) for p, q in zip(ta, tb)) / len(ta), 1)
            out["checked"] += 1; out["scores"][sid] = score
            if score > a.threshold:
                rec = {"id": sid, "rid": rid, "name": names.get(sid), "score": score}
                out["suspect"].append(rec)
                if a.apply:
                    sheet(n).paste(im, (x, y)); bumped.add(n); out["replaced"].append(rec)
    out["suspect"].sort(key=lambda r: -r["score"])
    if a.apply and bumped:
        for n in bumped:
            sheet(n).save(os.path.join(sdir, f"s{n:02d}.webp"), "WEBP", quality=82, method=6)
        mv = re.search(r"const SPRV = (\{[^\n]*?\});", h)
        sprv = json.loads(mv.group(1)) if mv else {}
        for n in bumped:
            sprv[str(n)] = sprv.get(str(n), 0) + 1
        if mv:
            h = h[:mv.start(1)] + json.dumps(sprv, separators=(",", ":")) + h[mv.end(1):]
            open(site, "w", encoding="utf-8").write(h)
    json.dump(out, open(os.path.join(a.repo, "sync", "verify-report.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps({k: (v if not isinstance(v, (list, dict)) else len(v)) for k, v in out.items()}))


if __name__ == "__main__":
    main()
