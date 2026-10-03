# infra — local test beds

Two **unmodified** third-party web apps, used only as things Claros watches on screen. Claros
never depends on them and nothing is installed into them. Seed scripts use the apps'
public REST APIs only.

| App | Purpose | URL | Admin login |
|---|---|---|---|
| ERPNext v16 (`erpnext/`) | main test bed: accounts payable | http://localhost:8080 | `Administrator` / `admin` |
| Zammad 7.2 (`zammad/`) | generalization test: helpdesk escalation | http://localhost:8081 | `admin@helpdesk.example` / `Claros-Demo-2026!` |

All images publish native `linux/arm64` (checked on Docker Hub / GHCR 2026-10-03), so
nothing runs under emulation on Apple Silicon.

## Prerequisites

- OrbStack (recommended on a Mac: lighter, faster file I/O) or Docker Desktop, with
  Docker Compose ≥ 2.24.
- Python 3.10+ with `httpx` (`pip install httpx`).

## RAM

| Stack | Idle | During first boot / seed |
|---|---|---|
| ERPNext (11 containers: MariaDB, 2× Redis, gunicorn, 2 workers, scheduler, socket.io, nginx) | ~2.5 GB | ~3.5 GB |
| Zammad without Elasticsearch (default) | ~2 GB | ~2.5 GB |
| + Elasticsearch (`./up.sh --search`) | +1.5 GB | +1.5 GB |

Allow the Docker VM **6 GB** for both stacks, **8 GB** with Elasticsearch. OrbStack sizes
memory dynamically; in Docker Desktop set it under *Settings > Resources*.

## ERPNext

```bash
cd infra/erpnext
docker compose up -d && docker compose logs -f create-site   # wait for exit 0 (~3-6 min)
python seed.py                 # setup wizard + API key + all demo data (idempotent)
./reset.sh snapshot            # golden state
./reset.sh                     # restore golden state between demo runs
docker compose down            # stop (add -v to wipe volumes)
```

Copy the printed `ERPNEXT_API_KEY` / `ERPNEXT_API_SECRET` into the repo-root `.env`.
Details, the seeded scenario and the traps: [erpnext/README.md](erpnext/README.md).

## Zammad

```bash
cd infra/zammad
./up.sh                        # starts the stack, waits, creates the admin (idempotent)
python seed.py                 # groups, agents, customers, refund policy, 6 tickets
docker compose down            # stop (add -v to wipe)
```

Scenario: Tier 1 (learner) handles refund requests under the *Refund policy v3* text module
(type `::refund` in a reply to insert it); anything over 50 EUR / older than 14 days /
a repeat refund goes to Tier 2 with an internal note; chargebacks go to Tier 2 at once.
Tickets: EN ×2, RU, FR, DE, plus one closed EN precedent showing the escalation pattern.

`docker-compose.yml` is the verbatim upstream file from `zammad/zammad-docker-compose`;
local changes (port 8081, Elasticsearch optional via the `search` profile) are in
`docker-compose.override.yml`, which `docker compose` loads automatically.

## UI language per user

ERPNext ships translations for `ru`, `fr`, `es`, `de` (and more):

- Per user: *User > (user) > Settings tab > Language* — or log in as the user, avatar menu
  *My Settings > Language*. Takes effect after reload.
- Via seed: `python seed.py --learner-lang ru --expert-lang de` (sets `User.language`;
  safe to re-run on an already seeded site, it only updates the language).
- Site default: *System Settings > Language*.

Zammad:

- Per user: avatar > *Profile > Language* (agents choose their own).
- Via seed: `python seed.py --learner-locale ru --expert-locale fr-fr`
  (codes: `en-us`, `ru`, `fr-fr`, `de-de`, `es-es`).

Customer-facing ticket content (RU/FR/DE tickets) is independent of the agent's UI language.
