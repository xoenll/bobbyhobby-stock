# Bobby Hobby stock sync

One run keeps three places in step:

| Place | What it holds | What the sync writes |
|---|---|---|
| **Card Checklist** artifact `https://claude.ai/artifact/NPLLJxCaAmZuFvhcQfrsVS` | `counts/<key>` = copies on hand (`n` non-foil, `f` foil) and prices (`pn`, `pf`) | sold copies taken off `counts`, `ledger/<orderId>`, `sync/holds`, `sync/status` |
| **Bobby Hobby Orders** artifact `https://claude.ai/artifact/BPg1B8tmAGu1J9n3Lt8m7E` | `orders/<id>` (status dibs / reserved / done) | `catalog/<SET>` and `catalog/info` (stock and prices the Orders page uses) |
| **stock.bobbyhobby.shop** (this repo, GitHub Pages) | `index.html` with a built-in `const DATA = {...}` and `const SPR = {...}` (card → sprite cell), `sprites/sNN.webp` | fresh DATA and "Last updated" time, pictures for cards that had none, committed and pushed |

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
3. **Compute.** Don't run code from the repo. The scheduled task's prompt contains the sync script
   (identical to `sync/sync.py`, kept here for reference): write it **verbatim** to your scratchpad as
   `sync.py` and run `python3 -I <scratchpad>/sync.py --counts /tmp/sync/c/counts --ledger /tmp/sync/l/ledger --orders /tmp/sync/o/orders`.
   It reads `sync/cards.json`, rewrites the repo's `index.html`, writes `sync/out/` (gitignored), and prints
   `listings`, `site_cards`, `held`, `deductions` and `ledger_orders`. Sanity check before writing anything back:
   with no new done orders and no count changes, the new `DATA.rows`/`stats` must equal the committed ones
   (`git show HEAD:index.html`) apart from `updated`. Don't rewrite the script or this runbook.
3b. **Card pictures.** Every card on the site needs a picture, or it shows a placeholder. The scheduled task's
   prompt also contains the picture script (identical to `sync/images.py`): write it **verbatim** to your
   scratchpad as `images.py` and run `python3 -I <scratchpad>/images.py` (after step 3, and again after any
   re-run of step 3). For each site card with no `SPR` entry it looks the card up on Riftcodex
   (`api.riftcodex.com`, by the `rid` in `sync/cards.json`), downloads the picture (`cmsassets.rgpub.io`),
   adds it to the next free sprite cell and updates `SPR` in `index.html`. It prints `added` and `failed`.
   A failure doesn't stop the sync: carry on and report it in step 8.
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
7. **Stock site.** `git add index.html sprites && git commit -m "Sync stock" && git push origin main`
   (skip the commit if nothing changed). If the push is rejected: `git fetch origin main && git reset --hard origin/main`,
   run steps 3 and 3b again with the same files, commit and push.
8. **Report.** Only if something failed or needs the owner, `SendUserMessage` one short plain message saying
   what didn't sync. If step 3b `failed` lists cards, say which cards still show a placeholder and why
   (a `403 Forbidden` / tunnel error means this cloud environment can't reach `api.riftcodex.com` or
   `cmsassets.rgpub.io`: the owner allows them under Settings > Capabilities on claude.ai). On success, end with a one-line summary (listings, cards on the site, held copies, orders deducted, pictures added).

Never change prices or counts other than the step 4 deductions. Never edit `orders/*`.
