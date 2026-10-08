# Partial-history paper recovery implementation plan

> **For agentic workers:** Use isolated agents for incident validation, public management collection and independent continuity review. Root owns integration, private candidate preparation and deployment.

**Goal:** Recover the existing personal paper account with owner-accepted missing historical public prices/reporting valuations, while retaining financial and AI state and keeping AI paused.

**Architecture:** Extend the existing immutable authenticated recovery receipt with a bounded typed incident and a new evaluation-period boundary. Keep the original witness scopes and all financial-prefix checks. Public management collects fresh inputs; the dashboard exposes authenticated incomplete-history metadata. New private state and owner bundles preserve all original files.

**Tech stack:** Python 3.12, SQLite 3.53.4, Decimal, pytest, protected Docker runtime.

- [x] Independently verify financial/AI continuity using exact records and retained external incident evidence, not counts; stop for a specific unresolved financial/AI category.
- [x] Add failing incident-schema tests for accepted observation-only gaps, invalid financial/AI categories, timestamps, period/reset claims and tampering. Implement `kernel/recovery_history.py` and recovery approval v2 with v1 compatibility; no schema migration or weakened prefix validation.
- [x] Add failing dashboard tests showing an incomplete-history warning and new evaluation start without replacing lifetime account totals or claiming completeness when verification is unavailable. Attach a read-only authenticated helper in `dashboard.py`, project in `api/app.py`, and render in `web/templates/overview.html`.
- [x] Add failing management tests for completed-hour history refresh and zero AI dispatch. Reuse the bounded public proxy transport and existing hourly barrier in `application/subscription_management.py`.
- [x] Run focused recovery/checkpoint/transition/management/dashboard tests, lint and hygiene; obtain independent source review; commit sanitized checkpoints.
- [x] Create a new private candidate from the verified backup, append only independently validated surviving newer public rows, preserve original row identities/receipt times, and record all missing IDs/interval/uncertainty in a protected owner incident.
- [x] Build/recheck the exact immutable image, pin the target owner manifest while retaining deployment identity/key/model/quota policy, then inspect/apply authenticated identity recovery followed by the required manifest transition.
- [x] Independently inspect the created production container, start one MANAGE_ONLY management worker and dashboard, collect fresh prices/completed-hour history/FX, and verify locks, accounting, unchanged 15 attempts/13 invocations, feature readiness, dashboard, actual deployed identity and database health.
- [ ] Publish only sanitized source and update status/private owner handoff with accepted gaps, new period and exact separate owner-resume action. Retain originals, previous image and all failures.

Actual command evidence and decisions belong in the dated review/status. No record is marked complete before observed results. User authorization accepts historical observations only and does not authorize AI resume, API spend, real orders or fresh financial authority.
