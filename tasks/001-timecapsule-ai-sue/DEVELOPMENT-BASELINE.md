# Development baseline — Time Capsule, test 001

**Date:** 2026-11-01  
**Project:** `bishopHU/dev-met-test2/tasks/001-timecapsule-ai-sue`  
**Status:** ACCEPTED under explicit user autonomy delegation for this benchmark; no separate plan approval required.

## Current approach

- **Method:** AI operating principles (Outcome-First, continuity, pre-implementation architecture challenge and risk-based tests) **+ SUE** (simplicity, understanding, evidence), from `bishopHU/development-methodologies` (main, as read 2026-11-01).
- **Start mode:** exploratory-then-specify, agent-selected for this autonomous standalone task; the user supplied complete functional and Linux requirements. Meaningful architecture comparison was made before coding, and concrete acceptance checks captured in `DESIGN.md`.
- **Source of authority:** user's Time Capsule requirements; most recent user instruction explicitly waives the plan-approval checkpoint despite previous initial workflow request. Technical choices delegated, not externally prescribed.
- **Architecture:** Python standard-library CLI over local `rsync -aHAX --checksum --link-dest`, staged hardlink snapshots with SHA-256 manifests and Linux no-replace atomic rename; see `DESIGN.md`.
- **Supported environment:** Linux, Python 3.9+ intended (only Python 3.13.5 verified), rsync 3.2+ intended (only rsync 3.4.1 verified); Linux libc exposing renameat2. Windows and non-Linux excluded. Single user, no adversarial writers inside repository.
- **Security/metadata:** snapshots private, no symlink traversal, restore only to nonexistent path, reject special files; preserve Unix mode/mtime/uid/gid, ACL/xattrs/hardlinks subject to OS/privileges. Atime not guaranteed unchanged.
- **Verification:** agent-local real integration tests are sufficient for this first Linux deliverable; GitHub CI not required. No 50k-file/10GB or OS matrix claims. Distinguish GitHub read-back from local behavior tests.
- **Project continuity:** `STATUS.md`, `tasks/2026-11-01-timecapsule.md`, `history/2026-11-01-session.md`, `TEST-RESULTS.md`.
- **Reassessment:** measured poor throughput on realistic 50k/10GB workload, need for encryption/retention, cross-platform requirements, or unavailable rsync features prompts reconsideration of restic/borg or design change.

## Change history

### 2026-11-01 — Initial autonomous architecture
- **Previous:** empty repository; no program or project baseline.
- **New:** AI + SUE, rsync-assisted transactionally staged hardlink snapshots, SHA-256 integrity and safe restore.
- **Reason:** meet the core requirements with tested mature file-transfer semantics and smaller owned complexity than a custom engine, while limiting repeated data copies.
- **Alternatives:** restic/borg backup engines; tar/cp full copies; own content-addressed engine (see `DESIGN.md`).
- **Authority:** direct user methodology and autonomous benchmark instructions.
- **Status:** ACCEPTED under delegated authority; source was written and locally exercised as documented in `TEST-RESULTS.md`.