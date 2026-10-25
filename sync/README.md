# Bobby Hobby stock sync

One run keeps three places in step:

| Place | What it holds | What the sync writes |
|---|---|---|
| **Card Checklist** artifact `https://claude.ai/artifact/NPLLJxCaAmZuFvhcQfrsVS` | `counts/<key>` = copies on hand (`n` non-foil, `f` foil) and prices (`pn`, `pf`) | sold copies taken off `counts`, `ledger/<orderId>`, `sync/holds`, `sync/status` |
| **Bobby Hobby Orders** artifact `https://claude.ai/artifact/BPg1B8tmAGu1J9n3Lt8m7E` | `orders/<id>` (status dibs / reserved / done) | `catalog/<SET>` and `catalog/info` (stock and prices the Orders page uses) |
| **stock.bobbyhobby.shop** (this repo, GitHub Pages) | `index.html` with a built-in `const DATA = {...}` | fresh DATA and "Last updated" time, committed and pushed |

Rules: dibs and reserved orders hold copies (shown as "reserved"). Done orders are sold: their
copies come off the checklist once, recorded in `ledger/<orderId>`; if a done order goes back to
dibs/reserved the copies are added back. Deleted orders keep whatever was already taken off.

## Runbook (for the scheduled task)

Work in `/home/claude`. Load tools first: `ToolSearch` with
`select:ArtifactData,mcp__claude-code-remote__add_repo,SendUserMessage`.

1. **Repo.** `add_repo` owner `xoenll`, repo `bobbyhobby-stock`, access `push`, then
   `git clone --depth 1 https://github.com/xoenll/bobbyhobby-stock /home/claude/bobbyhobby-stock`
   (allow up to 10 minutes). `mkdir -p /tmp/sync`.
2. **Read** (ArtifactData `list`, `query.limit` 1000, follow `next_cursor` if given, `out_dir` as shown):
   - Checklist `counts` → `out_dir` `/tmp/sync/c` (files land in `/tmp/sync/c/counts/`)
   - Checklist `ledger` → `/tmp/sync/l` (may be empty; then make `/tmp/sync/l/ledger/` yourself)
   - Orders `orders` → `/tmp/sync/o` (files in `/tmp/sync/o/orders/`)
3. **Compute.** Do NOT execute `sync/sync.py` (scheduled runs are not allowed to run code from the repo).
   Instead, write your own short Python script in your scratchpad from the **Computation spec** below
   (`sync/sync.py` is only a readable reference), and run it with `python3 -I` on the three input dirs.
   It rewrites the repo's `index.html` and writes the output files into `sync/out/` (gitignored), and prints
   `listings`, `site_cards`, `held`, `deductions` and `ledger_orders`. Sanity check before writing anything back:
   with no new done orders and no count changes, the new `DATA.rows`/`stats` must equal the committed ones
   (`git show HEAD:index.html`) apart from `updated`.
4. **Sold copies first** (skip if `deductions` is empty). For every entry in
   `sync/out/plan.json` → `checklist_count_deductions`: ArtifactData `get` Checklist `counts/<doc>`
   for its current `n`, `f` and `version`; new `n = max(0, n - take_off_nonfoil)`,
   new `f = max(0, f - take_off_foil)` (a negative take-off adds copies back). Then ONE ArtifactData
   `batch` on the Checklist containing every `update` of `counts/<doc>` `{n, f}` pinned with its
   `if_version` (or `set` `{n, f, pn: 0, pf: 0}` without `if_version` if the doc doesn't exist) AND every
   `set` of `ledger/<orderId>` from `sync/out/ledger-<orderId>.json` (`file_path`; pin with the version
   from a `get` when that ledger doc already exists). If the batch is refused because something changed,
   go back to step 2. More than 50 entries: stop and message the user.
5. **Orders catalog.** `get` each Orders `catalog/<SET>` and `catalog/info` for versions, then one
   `batch` on the Orders artifact: `set` `catalog/<SET>` from `sync/out/catalog-<SET>.json` for every
   file present, `set` `catalog/info` from `sync/out/catalog-info.json`, and `delete` any other
   `catalog/<SET>` doc that has no file. Pin every existing doc with `if_version`.
6. **Checklist status.** `get` Checklist `sync/holds` and `sync/status` (versions if they exist), then
   one `batch`: `set` `sync/holds` from `sync/out/holds.json`, `set` `sync/status` from `sync/out/status.json`.
7. **Stock site.** `git add index.html && git commit -m "Sync stock" && git push origin main`
   (skip the commit if nothing changed). If the push is rejected: `git fetch origin main && git reset --hard origin/main`,
   run step 3 again with the same files, commit and push.
8. **Report.** Only if something failed or needs the owner, `SendUserMessage` one short plain message saying
   what didn't sync. On success, end with a one-line summary (listings, cards on the site, held copies, orders deducted).

Never change prices or counts other than the step 4 deductions. Never edit `orders/*`.

## Computation spec (step 3)

Inputs: `sync/cards.json` (card list), checklist `counts/*.json` and `ledger/*.json`, Orders `orders/*.json`
(file name without `.json` = doc id). Constants:
`SETS = OGN, OGS, SFD, UNL, VEN, RAD`;
`cat → type`: legends Legend, sigspells Signature Spell, champunits Champion Unit, spells Spell, gear Gear,
units Unit, spcards SP Card, battlefields Battlefield, runes Rune, tokens Token;
type order: Legend, Champion Unit, Signature Spell, Spell, Gear, Unit, SP Card, Battlefield, Rune, Token;
high rarities: Rare, Epic, Showcase, Promo.

1. **Listing keys.** A card's base is `lower(set + "-" + code)` with `*` → `s`. Listing key = base + `-nf`
   (non-foil, checklist field `n`) or base + `-f` (foil, field `f`). Keys that don't match a card are ignored.
2. **Orders.** Status missing = dibs. For each item with `k` and `qty > 0`: dibs/reserved → *held*, done →
   *done*, both summed per order per listing key. Other statuses are ignored.
3. **Deductions.** For every order id in done ∪ ledger that still exists in orders: diff = done qty − ledger
   `items` qty per key. Non-zero diffs add to that card's take-off (`n`/`f`, by checklist `key`); such an order
   gets a ledger write `{"items": {its done items with valid keys}}`. Deleted orders are skipped.
4. **On hand.** From counts: `n`, `f` (ints, default 0), `pn`, `pf` (falsy → null). Apply take-offs:
   `n = max(0, n − take_n)`, `f = max(0, f − take_f)` (a card missing from counts starts at 0 with null prices).
   `plan.json` → `checklist_count_deductions` lists `{doc, take_off_nonfoil, take_off_foil, expected_new_n, expected_new_f}`.
5. **Catalog.** sold[k] / heldq[k] = totals over all done / held orders. For each on-hand doc that is a card:
   name = card name (Champion Unit: first `" - "` → `", "`), plus ` (<tag>)` if tag (`Alternate Art` → `Alt Art`).
   common = `{n: name, d: domain, set, no: code, t: type, r: rarity_full}`. `sn = n + sold[base-nf]`,
   `sf = f + sold[base-f]`. If sn > 0 add `{k: base-nf, v: "nf", p: pn, s: sn, ...common}`; if sf > 0 add
   `{k: base-f, v: ("" if rarity_full is high and sn == 0 else "foil"), p: pf, s: sf, ...common}`.
   Sort by (set index, lower(name)); group by set.
6. **Holds.** heldq per listing key → per checklist key `{n, f}`.
7. **Site rows.** Skip Battlefields. One row per base `{id: base, n, d, set, no, t, r}`; slot `nf` (for v nf) or
   `f` (otherwise) = `{s, p, res: heldq[k], sold: sold[k]}`; v "" also sets `single: true`.
   Sort by (type order, lower(name)). left(slot) = max(s − res − sold, 0).
   stats = `{cards: rows, copies: Σ left over both slots, reserved: Σ res, priced: catalog entries with truthy p}`.
   stamp = now in UTC+8 as `Oct 5, 2026 · 4:07 AM` (`%b %-d, %Y · %-I:%M %p`); at = now in ms.
8. **index.html.** Replace the JSON right after `const DATA = ` with
   `{"rows", "stats", "updated": stamp, "icons": <old icons>}` (compact, `ensure_ascii=False`), and the text
   inside `<time id="upd">…</time>` with stamp.
9. **sync/out/ files.** `catalog-<SET>.json` `{cards, at}` per set present; `catalog-info.json` `{at, cards: total}`;
   `holds.json` `{items, at}`; `status.json` `{at, stamp, listings, site_cards, held: stats.reserved,
   deducted_orders: #ledger writes}`; `ledger-<orderId>.json` per ledger write; `plan.json` with
   `checklist_count_deductions` and `checklist_ledger`.
