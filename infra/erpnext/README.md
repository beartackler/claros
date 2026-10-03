# ERPNext v16 test bed (accounts payable)

Unmodified ERPNext `v16.37.0` from frappe_docker's `pwd.yml`, all images native arm64.
Claros never talks to it except through the screen (and this seed script); nothing is
installed into ERPNext.

## Up / seed / snapshot

```bash
cd infra/erpnext
docker compose up -d                      # first boot: ~3-6 min (create-site installs ERPNext)
docker compose logs -f create-site        # wait for it to exit 0
open http://localhost:8080                # Administrator / admin (don't run the wizard by hand)

pip install httpx                         # or: uv pip install httpx
python seed.py --dry-run                  # optional: see every planned request
python seed.py                            # runs the setup wizard via API, creates an API key,
                                          # seeds everything (idempotent, ~2 min)
./reset.sh snapshot                       # save the seeded state as "golden"
```

`seed.py` prints `ERPNEXT_API_KEY` / `ERPNEXT_API_SECRET` (also written to
`infra/erpnext/.erpnext-keys.env`, gitignored). Copy them into the repo-root `.env`.
If the key ever gets lost, regenerate by hand: *User > Administrator > Settings > API Access >
Generate Keys* (this rotates the secret).

## Between demo runs

```bash
./reset.sh            # = ./reset.sh restore  -> back to the golden snapshot (~30-60 s)
./reset.sh list
```

## Down / wipe

```bash
docker compose stop           # keep data
docker compose down           # remove containers, keep volumes
docker compose down -v        # wipe everything (host copy in ./snapshots survives;
                              # after `up` + site creation, `./reset.sh` restores it)
```

After a `down -v` + restore the new site has a new encryption key, so the old API secret
stops working: blank `ERPNEXT_API_KEY/SECRET` in `.env`, delete `.erpnext-keys.env`, run
`python seed.py` once (it re-generates keys; everything else is skipped), then
`./reset.sh snapshot` again.

## What gets seeded

Fictional group, EUR parent + GBP subsidiary (names in `seed.py` constants):

| | |
|---|---|
| Companies | **Ostwind Precision Parts GmbH** (OPP, Germany, EUR, group) → **Ostwind Precision Parts Ltd** (OPPUK, UK, GBP) |
| Cost centers | OPP: Production, Maintenance, Administration, Logistics · OPPUK: Production, Administration |
| Ledgers (added) | Repairs and Maintenance, Tools and Small Equipment, Production Consumables, Software Subscriptions, Operating Bank Account |
| Asset categories | Production Machinery (→ Plants and Machineries), IT Hardware (→ Electronic Equipment) |
| Items | 6 fixed-asset items (`FA-*`), 9 opex items with per-company default ledger + cost center |
| Suppliers | 8 EUR (DE/NL), 2 GBP (UK) |
| History | 26 submitted + paid invoices Dec 2025 – Sep 2026, incl. capex precedents (>5k equipment → fixed assets) and maintenance >5k kept as opex |
| Settings | Accounts Settings → *Check Supplier Invoice Number Uniqueness* = on |
| Workflow | *Purchase Invoice Approval*: OPP drafts → **Submit** → Approved; OPPUK drafts → **Request Approval** → *Pending Second Approval* → Approver **Approve/Reject** |
| Users | `expert@ostwind.example`, `learner@ostwind.example` (Accounts User), `approver@ostwind.example` (Accounts Manager) — password `Claros-Demo-2026!` |

Draft invoices (the scenario):

| Supplier invoice no. | Role | Trap |
|---|---|---|
| FMT-2026-0261 · 8,400 EUR | expert demo | air compressor booked to *Tools and Small Equipment* / *Administration* → should be fixed asset `FA-COMP-15KW`, cost center Production |
| VIS-25-1187-A · 2,850 EUR · 2025-12-22 | expert demo | re-billing of paid VIS-25-1187 (2025-12-04); suffix slips past the uniqueness check |
| PFL-3622 · 4,250 GBP | expert demo | subsidiary invoice → must go through second approval |
| FMT-2026-0274 · 7,200 EUR | held-out learner | tool presetter, same mis-coding pattern → capitalise |
| VIS-26-0731 · 6,100 EUR | held-out learner | spindle overhaul: >5,000 but maintenance → stays opex |

The 5,000 capitalisation threshold is deliberately **not** written anywhere in ERPNext —
it is the tacit rule Claros must learn from the expert.

Field names were checked against `frappe/erpnext@version-16` and `frappe/frappe@version-16`
doctype JSONs; see the module docstring in `seed.py`.
