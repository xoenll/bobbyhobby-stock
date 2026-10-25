#!/usr/bin/env python3
"""Bobby Hobby stock sync.

Reads saved data from the Card Checklist and Orders artifacts (as JSON files written by
ArtifactData list ... out_dir), works out stock, and writes:
  out/plan.json        everything the agent must write back (see README.md)
  ../index.html        stock site with fresh DATA and "Last updated" time

Model
  * Checklist counts = copies on hand.
  * Orders with status dibs/reserved hold copies (shown as "reserved").
  * Orders marked done are sold: their quantities are taken off the checklist count once,
    tracked per order in the checklist's ledger collection, so nothing is deducted twice.
    If a done order goes back to dibs/reserved, the copies are added back.
"""
import argparse, glob, json, os, re, sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
SETORDER = ["OGN", "OGS", "SFD", "UNL", "VEN", "RAD"]
SETNAME = {"OGN": "Origins", "OGS": "Origins: Proving Grounds", "SFD": "Spiritforged", "UNL": "Unleashed", "VEN": "Vendetta", "RAD": "Radiance"}
CATTYPE = {"legends": "Legend", "sigspells": "Signature Spell", "champunits": "Champion Unit", "spells": "Spell", "gear": "Gear",
           "units": "Unit", "spcards": "SP Card", "battlefields": "Battlefield", "runes": "Rune", "tokens": "Token"}
TYPE_ORDER = ["Legend", "Champion Unit", "Signature Spell", "Spell", "Gear", "Unit", "SP Card", "Battlefield", "Rune", "Token"]
HIGHR = {"Rare", "Epic", "Showcase", "Promo"}
TAGNAME = {"Alternate Art": "Alt Art"}
HELD = {"dibs", "reserved"}


def load_dir(d):
    out = {}
    for p in glob.glob(os.path.join(d, "*.json")):
        out[os.path.basename(p)[:-5]] = json.load(open(p))
    return out


def listing_base(c):
    return (c["set"] + "-" + c["code"]).lower().replace("*", "s")


def listing_name(c):
    name = c["name"]
    if CATTYPE[c["cat"]] == "Champion Unit":
        name = name.replace(" - ", ", ", 1)
    tag = TAGNAME.get(c["tag"], c["tag"])
    return name + (f" ({tag})" if tag else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--counts", required=True, help="dir of checklist counts/*.json")
    ap.add_argument("--ledger", required=True, help="dir of checklist ledger/*.json (may be empty)")
    ap.add_argument("--orders", required=True, help="dir of Orders orders/*.json")
    ap.add_argument("--site", default=os.path.join(HERE, "..", "index.html"))
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    a = ap.parse_args()

    cards = json.load(open(os.path.join(HERE, "cards.json")))
    by_key = {c["key"]: c for c in cards}
    by_base = {listing_base(c): c for c in cards}
    counts = load_dir(a.counts)
    ledger = load_dir(a.ledger) if os.path.isdir(a.ledger) else {}
    orders = load_dir(a.orders)

    # ---- quantities per listing key (e.g. "rad-035-nf") from orders
    held, done = {}, {}
    for oid, o in orders.items():
        st = o.get("status") or "dibs"
        for it in o.get("items") or []:
            k, q = it.get("k"), int(it.get("qty") or 0)
            if not k or q <= 0:
                continue
            tgt = held if st in HELD else done if st == "done" else None
            if tgt is not None:
                tgt.setdefault(oid, {})
                tgt[oid][k] = tgt[oid].get(k, 0) + q

    def split(k):  # "rad-035-nf" -> (checklist card, "n"|"f")
        m = re.match(r"^(.*)-(nf|f)$", k)
        if not m or m.group(1) not in by_base:
            return None, None
        return by_base[m.group(1)], ("n" if m.group(2) == "nf" else "f")

    # ---- deductions: done orders vs what the ledger already took off
    deltas = {}          # checklist key -> {"n": x, "f": y}  (positive = take off)
    ledger_writes = {}   # order id -> new items map
    for oid in set(done) | set(ledger):
        if oid not in orders:          # deleted order: keep whatever was already deducted
            continue
        want = done.get(oid, {})
        had = (ledger.get(oid) or {}).get("items", {})
        changed = False
        for k in set(want) | set(had):
            d = want.get(k, 0) - had.get(k, 0)
            if not d:
                continue
            c, fld = split(k)
            if not c:
                continue
            deltas.setdefault(c["key"], {"n": 0, "f": 0})[fld] += d
            changed = True
        if changed:
            ledger_writes[oid] = {"items": {k: v for k, v in want.items() if split(k)[0]}}

    # ---- on-hand after deductions
    onhand = {}
    for k, v in counts.items():
        onhand[k] = {"n": int(v.get("n") or 0), "f": int(v.get("f") or 0), "pn": v.get("pn") or None, "pf": v.get("pf") or None}
    count_writes = []
    for k, d in deltas.items():
        cur = onhand.setdefault(k, {"n": 0, "f": 0, "pn": None, "pf": None})
        new = {"n": max(0, cur["n"] - d["n"]), "f": max(0, cur["f"] - d["f"])}
        count_writes.append({"doc": k, "take_off_nonfoil": d["n"], "take_off_foil": d["f"], "expected_new_n": new["n"], "expected_new_f": new["f"]})
        cur.update(new)

    # copies of existing done orders that are now deducted (added back so the Orders page,
    # which still counts done orders as allocated, shows the right number left)
    sold_live = {}
    for oid, items in done.items():
        for k, q in items.items():
            sold_live[k] = sold_live.get(k, 0) + q
    held_live = {}
    for items in held.values():
        for k, q in items.items():
            held_live[k] = held_live.get(k, 0) + q

    # ---- Orders catalog (same shape as the old CSV import)
    catalog = []
    for k, v in onhand.items():
        c = by_key.get(k)
        if not c:
            continue
        base = listing_base(c)
        common = {"n": listing_name(c), "d": c["domain"], "set": c["set"], "no": c["code"], "t": CATTYPE[c["cat"]], "r": c["rarity_full"]}
        sn = v["n"] + sold_live.get(base + "-nf", 0)
        sf = v["f"] + sold_live.get(base + "-f", 0)
        if sn > 0:
            catalog.append(dict(k=base + "-nf", v="nf", p=v["pn"], s=sn, **common))
        if sf > 0:
            catalog.append(dict(k=base + "-f", v="" if (c["rarity_full"] in HIGHR and not sn) else "foil", p=v["pf"], s=sf, **common))
    catalog.sort(key=lambda x: (SETORDER.index(x["set"]), x["n"].lower()))
    by_set = {}
    for x in catalog:
        by_set.setdefault(x["set"], []).append(x)

    # ---- checklist holds (per checklist key)
    holds = {}
    for k, q in held_live.items():
        c, fld = split(k)
        if c:
            holds.setdefault(c["key"], {"n": 0, "f": 0})[fld] += q

    # ---- stock site rows
    rows = {}
    for x in catalog:
        if x["t"] == "Battlefield":
            continue
        base = x["k"].rsplit("-", 1)[0]
        r = rows.setdefault(base, {"id": base, "n": x["n"], "d": x["d"], "set": x["set"], "no": x["no"], "t": x["t"], "r": x["r"]})
        slot = {"s": x["s"], "p": x["p"], "res": held_live.get(x["k"], 0), "sold": sold_live.get(x["k"], 0)}
        r["nf" if x["v"] == "nf" else "f"] = slot
        if x["v"] == "":
            r["single"] = True
    rows = sorted(rows.values(), key=lambda r: (TYPE_ORDER.index(r["t"]), r["n"].lower()))
    left = lambda s: max(s["s"] - s["res"] - s["sold"], 0) if s else 0
    now = datetime.now(timezone(timedelta(hours=8)))
    stamp = now.strftime("%b %-d, %Y · %-I:%M %p")
    stats = {"cards": len(rows), "copies": sum(left(r.get("nf")) + left(r.get("f")) for r in rows),
             "reserved": sum((r.get("nf") or {}).get("res", 0) + (r.get("f") or {}).get("res", 0) for r in rows),
             "priced": sum(1 for x in catalog if x["p"])}

    # ---- rewrite the stock site
    h = open(a.site, encoding="utf-8").read()
    i = h.index("const DATA = ") + len("const DATA = ")
    old, end = json.JSONDecoder().raw_decode(h[i:])
    new = {"rows": rows, "stats": stats, "updated": stamp, "icons": old["icons"]}
    h = h[:i] + json.dumps(new, ensure_ascii=False, separators=(",", ":")) + h[i + end:]
    h = re.sub(r'(<time id="upd">)[^<]*(</time>)', lambda m: m.group(1) + stamp + m.group(2), h)
    open(a.site, "w", encoding="utf-8").write(h)

    # ---- plan for the agent
    os.makedirs(a.out, exist_ok=True)
    at = int(now.timestamp() * 1000)
    plan = {
        "checklist_count_deductions": count_writes,
        "checklist_ledger": ledger_writes,
        "checklist_holds": {"items": holds, "at": at},
        "checklist_status": {"at": at, "stamp": stamp, "listings": len(catalog), "site_cards": len(rows),
                             "held": stats["reserved"], "deducted_orders": len(ledger_writes)},
        "orders_catalog": {s: {"cards": v, "at": at} for s, v in by_set.items()},
        "orders_catalog_info": {"at": at, "cards": len(catalog)},
    }
    for name, body in [("plan", plan)]:
        json.dump(body, open(os.path.join(a.out, name + ".json"), "w"), ensure_ascii=False)
    for s, body in plan["orders_catalog"].items():
        json.dump(body, open(os.path.join(a.out, f"catalog-{s}.json"), "w"), ensure_ascii=False)
    json.dump(plan["orders_catalog_info"], open(os.path.join(a.out, "catalog-info.json"), "w"))
    json.dump(plan["checklist_holds"], open(os.path.join(a.out, "holds.json"), "w"))
    json.dump(plan["checklist_status"], open(os.path.join(a.out, "status.json"), "w"))
    for oid, body in ledger_writes.items():
        json.dump(body, open(os.path.join(a.out, f"ledger-{oid}.json"), "w"))
    print(json.dumps({"listings": len(catalog), "site_cards": len(rows), "held": stats["reserved"],
                      "deductions": count_writes, "ledger_orders": list(ledger_writes), "sets": sorted(by_set)}, indent=1))


if __name__ == "__main__":
    main()
