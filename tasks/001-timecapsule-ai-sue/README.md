# Időkapszula / Time Capsule — AI + SUE

A **fully offline Linux command-line snapshot utility** for local directories. It uses the mature `rsync` transfer engine rather than inventing another filesystem synchronization format. Each successful snapshot is a complete-looking directory tree; unmodified files can share disk blocks with earlier snapshots through hard links. Restore creates **a new directory**, never overwriting an existing one.

## Requirements

- Linux with a filesystem that supports hard links and atomic renames within a filesystem (e.g. ext4, XFS, btrfs). The snapshot repository must stay on one filesystem.
- **Python 3.9+** with the standard library. The program requires the `renameat2` function in Linux libc for no-overwrite restore/publication.
- **rsync 3.2+**, compiled with ACL and xattr support and supporting `--checksum`, `--fsync`, `--open-noatime` and `--link-dest`. Tested with **rsync 3.4.1** only; exact minimum older-version compatibility was not tested.
- Sufficient free disk space for the first full snapshot, for changed files in later snapshots, and for a full restore. Same-data hard links save space; inodes and metadata consume some space per snapshot.

On Debian/Ubuntu, typical setup:

```sh
sudo apt-get install python3 rsync
```

**No daemon, network access, root privileges, database, Python packages or GitHub CI are required.** Files with ownership/group that the current user cannot preserve may require appropriate privileges; this is reported as an error, not silently ignored. ACLs and xattrs are copied by rsync when supported by the source and destination filesystems. Execute only in a trusted local single-user environment.

## Usage

From this directory, use `python3 timecapsule.py`:

```sh
python3 timecapsule.py init /home/me/capsule-repo
python3 timecapsule.py snapshot /home/me/capsule-repo /home/me/my-project
python3 timecapsule.py list /home/me/capsule-repo
python3 timecapsule.py verify /home/me/capsule-repo              # every snapshot
python3 timecapsule.py verify /home/me/capsule-repo SNAPSHOT_ID  # one snapshot
python3 timecapsule.py restore /home/me/capsule-repo SNAPSHOT_ID /home/me/restore-here
```

Restore requires that `/home/me/restore-here` **does not exist** and its parent **does exist**. It refuses an existing directory, regular file or dangling symlink, even when the target appears empty. Paths should be absolute in scripts. The source and repository must not contain one another; use a distinct location for the repository.

### Reproducible example

```sh
set -eu
TC="$PWD/timecapsule.py"  # run from the directory containing timecapsule.py
TMP="$(mktemp -d)"
mkdir -p "$TMP/project"
printf 'version 1\n' > "$TMP/project/note.txt"
python3 "$TC" init "$TMP/repo"
python3 "$TC" snapshot "$TMP/repo" "$TMP/project"
FIRST="$(python3 "$TC" list "$TMP/repo" | head -n 1 | cut -f 1)"
printf 'version 2\n' > "$TMP/project/note.txt"
printf 'added\n' > "$TMP/project/new.txt"
python3 "$TC" snapshot "$TMP/repo" "$TMP/project"
python3 "$TC" restore "$TMP/repo" "$FIRST" "$TMP/restored-first"
cat "$TMP/restored-first/note.txt"  # prints: version 1
[ ! -e "$TMP/restored-first/new.txt" ]
python3 "$TC" verify "$TMP/repo"
```

## Guarantees and limits

- **Atomic publication:** work happens under `.staging/`, then a same-filesystem no-replace rename publishes a fully built snapshot. Snapshots are listed only when they have a committed marker and metadata. A killed process or rsync error cannot publish a partial snapshot. Existing snapshots are never edited by Time Capsule. Leftover `.staging/pending-*` directories from abruptly killed processes can be deleted manually when no operation is running.
- **Restores are separately staged and atomically published** only after a full snapshot integrity check, transfer and post-copy content/essential-metadata check. The target must not exist; atomic `renameat2(RENAME_NOREPLACE)` closes the race with another creator of that pathname. Failed restores can leave an internal temporary directory if cleanup is prevented by permissions.
- **Manifest integrity:** for every regular file, record SHA-256, size, mode, owner/group and nanosecond mtime; for directories and links record significant stat metadata and symlink targets. `verify` checks these records against disk. Creation checks the source entry list twice to detect ordinary concurrent edits; it does **not** provide a fully atomic point-in-time capture of a concurrently changing filesystem. Quiesce writers for important backups or use a filesystem snapshot as the source.
- **Symlinks:** stored as links, never followed into other source trees. Dangling and circular links remain links. Restoring a symlink that points outside the restored tree preserves that link's target; do not dereference it if undesired.
- **Metadata:** `rsync -aHAX --numeric-ids` preserves file mode, mtime, directory structure, symlink targets, hardlink relationships, ACLs and supported xattrs. Owner/group preservation is subject to privileges. Access times, Linux capabilities on unrelated filesystems, immutable flags, birth/creation times, sparse layout, reflink topology and every filesystem-specific attribute are **not guaranteed**. Hash-based `verify` checks content and the recorded stat metadata, but does **not independently validate ACLs, xattrs or source hardlink topology**. `rsync --open-noatime` attempts to minimize access-time changes, **but source atime can still change even for owner-readable files** on the tested Linux filesystem. The backup does not intentionally write source data, permissions or modification timestamps. Strict atime preservation is **not guaranteed**.
- **Cost:** unchanged file content is hard-linked between versions if rsync considers the file and required metadata identical. File content is checked with `--checksum` even when size and mtime are unchanged; SHA-256 manifests and automatic restore verification require additional sequential disk reads. This prioritizes correctness over the fastest possible 50k-file or 10GB run. **No compression, encryption, retention pruning, deduplication of changed blocks, network sync or snapshot deletion command** is provided. These are deliberate scope boundaries, not implied guarantees.
- **Security and operations:** repositories are intended to be private and trusted. Keep the repository outside the source, restrict permissions to the owner (new repositories are created with private permissions), and **never edit, chmod, delete, or hardlink-write data inside snapshots**. Editing a hard-linked snapshot file can corrupt several versions. A cryptographic manifest detects accidental corruption, but it is not a malicious-tamper-proof signature. Back up the backup repository separately if desired; this is not a replacement for independent off-machine disaster recovery. No traversal of source symlinks is performed. Unsupported sockets, FIFOs and device files cause a clear failure rather than risky or blocking reads.
- **Failure semantics:** disk full, permission denial, missing source, bad IDs and partially modified sources produce a nonzero exit and an error on stderr. `fsync` is used on rsync-written files, metadata and relevant directories before and after publication, but hardware/OS defects and arbitrary external modification cannot be eliminated by application code.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The integration tests use real local files and real rsync, including versions, restored contents, Unicode/space names, empty files, symlink escape prevention, unchanged hard-link sharing, size/mtime collisions, metadata, simulated transfer failure, source mutation during copy, repository corruption, no-overwrite race and multiple hundreds of files. Check `TEST-RESULTS.md` for the actual executed environment and outcomes. Tests do not assert 10GB/50k-file throughput or broad filesystem/OS portability.

For the design and alternative evaluation, see [`DESIGN.md`](DESIGN.md). For the AI + SUE development continuity, see `DEVELOPMENT-BASELINE.md`, `STATUS.md` and `tasks/`.
