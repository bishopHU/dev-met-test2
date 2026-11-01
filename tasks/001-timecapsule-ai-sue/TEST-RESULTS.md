# Actual verification evidence — 2026-11-01

## Environment

- **Execution:** local agent container, real Linux kernel 6.18.57, x86_64, `overlay` filesystem; runtime root user in container.
- **Python:** 3.13.5.
- **rsync:** 3.4.1 protocol 32 (supports ACLs, xattrs, fsync, hardlink and checksum options).
- **External network:** not used by the program or behavioral tests.
- **CI:** NOT RUN; adequate local integration testing used for this initial deliverable.

## Successful execution

All **14** test cases were run and passed in four separate subsets to fit the per-command execution window. A full single-process suite attempt exceeded the execution command time budget while reaching the multi-file test; it was **not** counted as a successful full-suite run. These subsets independently executed every test case:

| Group | Count | Result | Test coverage |
| --- | ---: | --- | --- |
| 1 | 4 | PASS | history, spaces/Unicode/empty, symlink boundaries, mode/mtime/hardlinks/xattr, unchanged-data inode reuse |
| 2 | 4 | PASS | same-size/same-mtime changed content, refusal to overwrite user data, atomic no-replace, failed transfer protection |
| 3 | 4 | PASS | concurrent source edit detection, corrupted snapshot refusal, invalid paths and overlapping repos, source non-write invariant |
| 4 | 2 | PASS | 250-file two-snapshot plus verify+restore scenario, unsupported FIFO rejection |

A dedicated **`sh examples/demo.sh`** run passed: created two snapshots, changed a file, added a file, restored both versions, compared actual file contents and ran `verify` for both. Its terminal output ended: `DEMO PASS: both versions restored; added file only in second snapshot`.

Earlier **exploratory test failure**: an overly strict assertion required unchanged source file access time (`atime`). On the actual Linux container this advanced during backup even when `rsync --open-noatime` was enabled; the assumption was corrected and the non-guarantee documented. This did not indicate a mutation of file contents, mtime or ctime; the corrected test passed.

## Not executed / not established

- 50,000-file / 10GB benchmark; no throughput, latency or space-efficiency numbers are claimed.
- Power-loss testing on physical storage, special ACL policy permutations, unprivileged foreign-owner files, all Linux filesystems, Python 3.9-3.12, older rsync releases, Solaris/BSD/macOS, Windows. None of these are reported as PASS.
- GitHub Actions/CI build: not needed, not run. GitHub persistence check is separate from application tests.

## Reproduce

```sh
python3 -m py_compile timecapsule.py
python3 -m unittest discover -s tests -v
sh examples/demo.sh
```