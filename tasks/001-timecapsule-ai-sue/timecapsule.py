#!/usr/bin/env python3
"""Time Capsule: local transactional, hard-link-based snapshots using rsync."""

import argparse
import contextlib
import datetime as dt
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile

FORMAT = 1
SNAPSHOT_ID = re.compile(r"\A\d{8}T\d{6}Z-[0-9a-f]{12}\Z")
CHUNK = 1024 * 1024


class CapsuleError(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def real_dir(path, description):
    p = Path(path).expanduser().resolve(strict=True)
    if not p.is_dir():
        raise CapsuleError(f"{description} is not a directory: {path}")
    return p


def require_real_directory(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        raise CapsuleError(f"required directory missing: {path}") from None
    if not stat.S_ISDIR(mode):
        raise CapsuleError(f"not a real directory (possible symlink): {path}")


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_all_dirs(root):
    for here, dirs, _ in os.walk(root, followlinks=False, topdown=False):
        for name in dirs:
            p = Path(here) / name
            if stat.S_ISDIR(p.lstat().st_mode):
                sync_dir(p)
        sync_dir(here)


def write_json_atomic(path, obj):
    raw = (json.dumps(obj, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with open(path, "xb") as fp:
        fp.write(raw)
        fp.flush()
        os.fsync(fp.fileno())


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (ValueError, UnicodeError) as exc:
        raise CapsuleError(f"invalid JSON in {path}: {exc}") from exc


def snapshot_name():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(6)


def entries(root, hash_files=False):
    """Yield metadata of every path without following links, including the root.

    Reject special files instead of accidentally reading from devices or copying
    sockets. Sorting by raw encoded names makes manifests deterministic.
    """
    pending = [(root, ".")]
    while pending:
        path, rel = pending.pop()
        st = path.lstat()
        mode = st.st_mode
        common = {
            "path": rel,
            "mode": stat.S_IMODE(mode),
            "mtime_ns": st.st_mtime_ns,
            "uid": st.st_uid,
            "gid": st.st_gid,
        }
        if stat.S_ISDIR(mode):
            common["kind"] = "directory"
            # Avoid updating source directory access times when supported.
            try:
                dirfd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
            except PermissionError:
                # Reading a directory not owned by us may disallow O_NOATIME.
                dirfd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                with os.scandir(dirfd) as it:
                    children = sorted((x.name for x in it), key=os.fsencode, reverse=True)
            finally:
                os.close(dirfd)
            for child in children:
                child_path = path / child
                child_rel = child if rel == "." else rel + "/" + child
                pending.append((child_path, child_rel))
        elif stat.S_ISREG(mode):
            common.update(kind="file", size=st.st_size)
            if hash_files:
                digest = hashlib.sha256()
                # O_NOFOLLOW prevents a swapped-in symlink from redirecting reads.
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    fst = os.fstat(fd)
                    if not stat.S_ISREG(fst.st_mode):
                        raise CapsuleError(f"file changed type during scan: {path}")
                    with os.fdopen(fd, "rb", closefd=False) as fp:
                        while True:
                            block = fp.read(CHUNK)
                            if not block:
                                break
                            digest.update(block)
                    if os.fstat(fd).st_size != st.st_size:
                        raise CapsuleError(f"file modified while hashing: {path}")
                finally:
                    os.close(fd)
                common["sha256"] = digest.hexdigest()
        elif stat.S_ISLNK(mode):
            common.update(kind="symlink", target=os.readlink(path))
        else:
            raise CapsuleError(f"unsupported special file (FIFO/socket/device): {path}")
        if not hash_files:
            # ctime/device/inode help detect in-flight edits, including mtime resets.
            common.update(ctime_ns=st.st_ctime_ns, dev=st.st_dev, ino=st.st_ino)
        yield common


def fingerprint(root):
    return list(entries(root, hash_files=False))


def manifest(root):
    return list(entries(root, hash_files=True))


def manifest_bytes(items):
    return b"".join((json.dumps(x, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8") for x in items)


def save_manifest(path, items):
    data = manifest_bytes(items)
    with open(path, "xb") as fp:
        fp.write(data)
        fp.flush()
        os.fsync(fp.fileno())
    return hashlib.sha256(data).hexdigest()


def read_manifest(path, expected_digest):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_digest:
        raise CapsuleError(f"manifest checksum mismatch: {path}")
    try:
        return [json.loads(line) for line in data.splitlines()]
    except (ValueError, UnicodeError) as exc:
        raise CapsuleError(f"invalid manifest {path}: {exc}") from exc


def compare_manifest(root, expected):
    actual = manifest(root)
    if actual != expected:
        a = {x["path"]: x for x in actual}
        e = {x["path"]: x for x in expected}
        changed = next((key for key in sorted(a.keys() | e.keys()) if a.get(key) != e.get(key)), "unknown")
        raise CapsuleError(f"snapshot verification failed at {changed!r}")
    return len(actual)


def run_rsync(source, destination, link_dest=None):
    args = ["rsync", "-aHAX", "--numeric-ids", "--fsync", "--checksum", "--open-noatime"]
    if link_dest is not None:
        args.append("--link-dest=" + str(link_dest))
    args += [str(source) + "/", str(destination) + "/"]
    try:
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    except FileNotFoundError:
        raise CapsuleError("rsync not installed; install rsync with ACL and xattr support") from None
    if proc.returncode:
        detail = (proc.stderr or proc.stdout).strip()
        raise CapsuleError(f"rsync failed (exit {proc.returncode}): {detail[-3000:]}")


def ensure_rsync():
    try:
        result = subprocess.run(["rsync", "--version"], capture_output=True, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise CapsuleError("requires rsync with --fsync, --link-dest, ACL and xattr support") from exc
    capabilities = result.stdout
    if "ACLs" not in capabilities or "xattrs" not in capabilities:
        raise CapsuleError("installed rsync lacks ACL or xattr support")


def overlaps(a, b):
    return a == b or a in b.parents or b in a.parents


class Repository:
    def __init__(self, path):
        self.root = real_dir(path, "repository")
        for dirname in ("snapshots", ".staging"):
            require_real_directory(self.root / dirname)
        if not stat.S_ISREG((self.root / "config.json").lstat().st_mode):
            raise CapsuleError("invalid repository config")
        cfg = read_json(self.root / "config.json")
        if cfg.get("format_version") != FORMAT:
            raise CapsuleError("unsupported repository format")
        self.snapshots = self.root / "snapshots"
        self.staging = self.root / ".staging"

    @contextlib.contextmanager
    def locked(self):
        fd = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise CapsuleError("unsafe repository lock")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def snapshots_info(self):
        result = []
        for path in sorted(self.snapshots.iterdir()):
            if not SNAPSHOT_ID.fullmatch(path.name):
                continue
            require_real_directory(path)
            meta_path = path / "meta.json"
            marker = path / "COMPLETE"
            if not meta_path.is_file() or not marker.is_file() or marker.read_bytes() != b"complete\n":
                continue  # Not a committed snapshot.
            info = read_json(meta_path)
            if info.get("id") != path.name or info.get("format_version") != FORMAT:
                raise CapsuleError(f"invalid committed snapshot metadata: {path.name}")
            require_real_directory(path / "data")
            result.append(info)
        return result

    def find_snapshot(self, snapshot_id):
        if not SNAPSHOT_ID.fullmatch(snapshot_id):
            raise CapsuleError("invalid snapshot ID")
        result = next((x for x in self.snapshots_info() if x["id"] == snapshot_id), None)
        if result is None:
            raise CapsuleError(f"snapshot not found: {snapshot_id}")
        return self.snapshots / snapshot_id, result


def init_repo(path):
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise CapsuleError("repository path must not be a symlink")
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise CapsuleError("repository path must be missing or an empty directory")
    else:
        path.mkdir(mode=0o700, parents=True)
    (path / "snapshots").mkdir(mode=0o700)
    (path / ".staging").mkdir(mode=0o700)
    write_json_atomic(path / "config.json", {"format_version": FORMAT, "created_at": now()})
    sync_dir(path)
    sync_dir(path.parent)
    print(f"Initialized repository: {path.resolve()}")


def snapshot(repo, source):
    src = real_dir(source, "source")
    if overlaps(repo.root, src):
        raise CapsuleError("source and repository must not contain each other")
    ensure_rsync()
    with repo.locked():
        previous = repo.snapshots_info()
        parent = previous[-1]["id"] if previous else None
        sid = snapshot_name()
        stage = Path(tempfile.mkdtemp(prefix="pending-", dir=repo.staging))
        try:
            (stage / "data").mkdir()
            before = fingerprint(src)
            ref = repo.snapshots / parent / "data" if parent else None
            run_rsync(src, stage / "data", ref)
            after = fingerprint(src)
            if before != after:
                raise CapsuleError("source changed during snapshot; no snapshot published")
            items = manifest(stage / "data")
            digest = save_manifest(stage / "manifest.jsonl", items)
            info = {"format_version": FORMAT, "id": sid, "created_at": now(), "source": str(src),
                    "parent": parent, "entries": len(items), "files": sum(x["kind"] == "file" for x in items),
                    "logical_bytes": sum(x.get("size", 0) for x in items), "manifest_sha256": digest}
            write_json_atomic(stage / "meta.json", info)
            with open(stage / "COMPLETE", "xb") as fp:
                fp.write(b"complete\n")
                fp.flush()
                os.fsync(fp.fileno())
            sync_all_dirs(stage / "data")
            sync_dir(stage)
            sync_dir(repo.staging)
            rename_noreplace(stage, repo.snapshots / sid)
            sync_dir(repo.snapshots)
            sync_dir(repo.staging)
        except BaseException:
            try:
                shutil.rmtree(stage)
            except OSError:
                print(f"WARNING: incomplete staging directory remains: {stage}", file=sys.stderr)
            raise
        print(f"Created {sid}: {info['files']} files, {info['logical_bytes']} logical bytes")


def verify_snapshot(root, meta):
    expected = read_manifest(root / "manifest.jsonl", meta["manifest_sha256"])
    count = compare_manifest(root / "data", expected)
    if count != meta["entries"]:
        raise CapsuleError("manifest entry count differs from snapshot metadata")
    return expected


def rename_noreplace(source, target):
    """Linux atomic rename that MUST NOT replace a concurrently created target."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        func = libc.renameat2
    except AttributeError:
        raise CapsuleError("Linux libc.renameat2 is required for safe restore") from None
    func.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    func.restype = ctypes.c_int
    at_fdcwd = -100
    result = func(at_fdcwd, os.fsencode(source), at_fdcwd, os.fsencode(target), 1)
    if result != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), str(target))


def restore(repo, sid, target):
    target = Path(target).expanduser().absolute()
    if os.path.lexists(target):
        raise CapsuleError(f"restore target already exists (will not overwrite): {target}")
    parent = real_dir(target.parent, "restore target parent")
    target = parent / target.name
    if not target.name or target.name in (".", ".."):
        raise CapsuleError("invalid restore destination")
    if repo.root == target or repo.root in target.parents:
        raise CapsuleError("restore destination must be outside the repository")
    ensure_rsync()
    with repo.locked():
        root, meta = repo.find_snapshot(sid)
        expected = verify_snapshot(root, meta)
        stage = Path(tempfile.mkdtemp(prefix=".timecapsule-restore-", dir=parent))
        try:
            run_rsync(root / "data", stage)
            compare_manifest(stage, expected)
            sync_all_dirs(stage)
            # Another process may have created the final path in the meantime.
            rename_noreplace(stage, target)
            sync_dir(parent)
        except BaseException:
            try:
                shutil.rmtree(stage)
            except OSError:
                print(f"WARNING: incomplete restore directory remains: {stage}", file=sys.stderr)
            raise
    print(f"Restored {sid} to {target}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline transactional file snapshots (Linux, rsync)")
    sub = parser.add_subparsers(dest="action", required=True)
    ip = sub.add_parser("init", help="initialize an empty repository")
    ip.add_argument("repo")
    sp = sub.add_parser("snapshot", help="create a complete snapshot")
    sp.add_argument("repo")
    sp.add_argument("source")
    lp = sub.add_parser("list", help="list complete snapshots")
    lp.add_argument("repo")
    vp = sub.add_parser("verify", help="check snapshot contents and metadata")
    vp.add_argument("repo")
    vp.add_argument("id", nargs="?", help="snapshot ID, or all if omitted")
    rp = sub.add_parser("restore", help="restore to a NEW destination directory")
    rp.add_argument("repo")
    rp.add_argument("id")
    rp.add_argument("target")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action == "init":
            init_repo(args.repo)
            return 0
        repo = Repository(args.repo)
        with repo.locked() if args.action in ("list", "verify") else contextlib.nullcontext():
            if args.action == "snapshot":
                snapshot(repo, args.source)
            elif args.action == "restore":
                restore(repo, args.id, args.target)
            elif args.action == "list":
                for info in repo.snapshots_info():
                    print(f"{info['id']}\t{info['created_at']}\t{info['files']} files\t{info['logical_bytes']} bytes")
            elif args.action == "verify":
                chosen = [repo.find_snapshot(args.id)[1]] if args.id else repo.snapshots_info()
                for info in chosen:
                    verify_snapshot(repo.snapshots / info["id"], info)
                    print(f"OK {info['id']}")
    except (CapsuleError, OSError, subprocess.SubprocessError) as exc:
        print(f"timecapsule: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
