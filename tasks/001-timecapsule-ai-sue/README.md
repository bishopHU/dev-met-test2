# Time Capsule — AI + SUE

Offline Linux snapshots using Python 3.9+ and rsync 3.2+.

The full runnable source bundle, CLI usage and integration test suite were developed locally. This repository currently contains the architecture, test evidence and development history; upload of the complete source code is pending.

Commands once source is present:

```sh
python3 timecapsule.py init /path/to/repo
python3 timecapsule.py snapshot /path/to/repo /path/to/source
python3 timecapsule.py list /path/to/repo
python3 timecapsule.py verify /path/to/repo
python3 timecapsule.py restore /path/to/repo SNAPSHOT_ID /path/to/new-directory
```

See `DESIGN.md`, `TEST-RESULTS.md`, `DEVELOPMENT-BASELINE.md` and `STATUS.md`.
