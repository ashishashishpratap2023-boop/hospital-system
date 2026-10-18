# Implementation checklist (actual status)

| Phase | Item | Status |
|---|---|---|
| 1 | Inspect environment, plan | Done |
| 2 | Backend structure, models, Argon2 + JWT (httpOnly cookie) auth, login throttle | Done, tested |
| 2 | Alembic migrations | **Pending** (dev uses `python -m app.cli init-db`) |
| 3 | Tenants, domains, super-admin API, role guards, tenant isolation | Done, tested (SQLite) |
| 3 | Run tests against PostgreSQL | **Not run** (no Postgres in this environment) |
| 4 | Patients (create/search/duplicate check/normalised mobile) | Done, tested |
| 4 | Doctors, departments APIs (admin-only writes, tenant-scoped) | Done, tested |
| 5 | Booking, slots, reschedule, cancel, status flow, unique numbers; double-booking blocked by app check + row lock (PostgreSQL) + partial unique index | Done, tested on SQLite |
| 6 | Branding settings API, Next.js public site | Pending |
| 7 | Printable slips | Pending |
| 8 | WhatsApp Cloud API connector: encrypted credentials, honest status, templates with safe variables, consent/opt-out (STOP), queue with retry + idempotency, signed webhook, manual retry | Done, tested with a **mock provider only** |
| 8 | Real delivery through Meta | **Not tested** (needs your Meta app, approved template and a test number) |
| 8 | Free QR login (no Meta API): `whatsapp-qr/` bridge on Baileys, encrypted sessions, one-at-a-time sending with delays + daily cap, dedupe by job ref; backend `/whatsapp/qr/start`, `/qr/status`; booking `send_whatsapp` flag | Code done; 42 pytest + 2 node tests pass with mocks |
| 8 | **Real QR scan and real message send** | **Not tested** (sandbox cannot reach WhatsApp servers; test on your machine with your own number) |
| 8 | QR mode: delivered/read ticks, inbound STOP handling | Not implemented (QR messages show 'sent' only; record opt-outs manually) |
| 8 | Reminder scheduler (template exists, no automatic trigger yet) | Pending |
| 9 | Dashboard, reports, audit-log viewer | Pending (audit rows already written) |
| 10 | Frontend, Playwright E2E, production build | Pending |
| 11 | Docker, Nginx, deployment guide | Pending (no server access) |

Known gaps to address before production: CSRF protection for cookie auth (SameSite=Lax only for now),
refresh tokens, Redis-backed rate limiting, file-upload validation.
