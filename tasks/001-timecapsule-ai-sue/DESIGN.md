# Pre-implementation architecture evaluation — AI + SUE

**Status:** autonomous benchmark choice; the user explicitly waived the plan-approval stop in this task, but not the requirement to consider real alternatives before implementing. **Project runtime:** Linux only; Windows not required. User supplied no language or storage format.

## Outcome and constraints

A small offline CLI keeps several faithful historical states of a folder, restores each to an untouched new destination, conserves repeated disk content where practicable, and ensures that incomplete copies never masquerade as complete versions. Single user, 50,000 files / 10GB upper representative workload, with few changes between runs. The program must be reproducible, readily testable, and honestly document its dependencies and limitations.

## Genuine candidates assessed before main implementation

1. **`rsync` + hard-link snapshot directories (selected).** The mature `rsync -aHAX --checksum --link-dest` transfer engine handles Unix metadata, hardlink groups, symlinks, unusual filenames and unchanged-file reuse. A modest Python control layer adds locking, staging, atomic publication, validation, SHA-256 manifests and non-destructive restore. Plain directories are transparent and inspectable with standard tools. The tradeoff is a system `rsync` requirement, 1 inode per file per snapshot, full hashing cost, and snapshots that must not be directly edited because hard links share inodes.
2. **`restic` or `borg` (strongest alternative).** Mature content-addressed backup programs offer useful corruption checks, block-level deduplication, retention management, encryption and repository management with fewer new ownership responsibilities. They are appealing if users already have them or need encryption or managed pruning. For this narrowly scoped new command-line program, their backup-repository format, separate installation and CLI integration, archive management and restore semantics complicate delivering the exact specified `snapshot`/`list`/`restore to absent folder` contract. They should replace this implementation if the scope expands to encrypted, retained, or portable backup management. This assessment does **not** claim that custom code is inherently safer than those tools.
3. **Custom content-addressed Python storage engine.** Might save disk at block granularity, avoid `rsync`, and potentially ship one script, but requires independently implementing inode/link/ACL/xattr semantics, manifests, content publication, garbage collection, efficient comparison and recovery. This creates more failure modes than the requirements currently justify. Rejected on reliability and lifecycle ownership grounds.
4. **Full `tar` archives or straightforward `cp -a`.** Mechanically simple and transparent but repeats roughly 10GB of data for each mostly-unchanged snapshot unless the filesystem happens to provide cheap reflinks. Rejected on recurring storage and copying cost. `cp --reflink=auto` is strong on CoW-only storage, but cannot guarantee efficient operation on generic Linux filesystems.

**Decision-relevant assumption:** available Linux installation has rsync with suitable options, and owners can operate the small Python wrapper. Testable through `rsync --version` and real filesystem integration tests. If user target machines lack rsync, or require encryption, portable self-contained delivery, pruning or large-file block-delta space efficiency, revisit the decision (most likely adopting restic/borg). Python 3.9+ is agent-chosen, *not* mandated by the user.

## Data flow and consistency

```
SOURCE --[read-only rsync --checksum]--> REPO/.staging/pending-*/data
                             ^           | manifests, compare source scan
   previous snapshot/data ---+           v
                                      fsync, COMPLETE
                                          |
                           atomic rename without replacement
                                          v
                                 REPO/snapshots/<id>/
                                          |
                            verify SHA-256 + metadata
                                          v
                              staged restore in target parent
                                          |
                            verify + rename NOREPLACE
                                          v
                                  NEW target directory
```

A repository-wide advisory `flock` coordinates readers/writers and avoids concurrent publish/restore in this CLI. Prior versions stay untouched. A restore is created on the target filesystem, then atomically renamed into position. Unexpected existing targets, even dangling symlinks, fail closed; for Linux `renameat2(RENAME_NOREPLACE)` prevents a last-moment overwrite race. Failure in staging does not change existing snapshots or expose an incomplete restore as the requested target.

`rsync -aHAX` copies metadata and links without following symlinks; changed files are written separately. `--checksum` avoids missing changed bytes when size and mtime are deliberately unchanged, but reads all input data each run. A separate SHA-256 manifest makes retrospective integrity checking possible. Source scans before/after transfer detect most live edits and removal, but no userspace directory walk can promise a coherent instantaneous snapshot during arbitrary concurrent writes. External trusted edits to snapshot files can compromise hard-linked versions; a private repository is mandatory operational guidance.

## Verification and falsification

Real local Linux `unittest` integration tests cover failure before publication, source churn, checksum-vs-mtime collision, hardlink reuse, source/restore isolation, symlinks, Unicode names, unsupported paths, content corruption and atomic target protection. Report actual runner and tool versions and mark unexecuted large-scale/platform tests as unverified. If evidence shows rsync's per-file fsync or full-checksum scan unsuitable for 50k files / 10GB, measure representative data then consider a mature backup executable or a separate optional performance profile; do not claim a benchmark not performed.

**Deliberately absent:** custom datastore, daemon, database, background scheduler, GUI, GitHub CI, automatic pruning, encryption, filesystem freeze, cross-platform abstraction. These add complexity and contracts that are not currently required.