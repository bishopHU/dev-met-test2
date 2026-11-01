"""Integration tests use real rsync and actual Linux filesystem operations."""
import errno
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "timecapsule.py"
SPEC = importlib.util.spec_from_file_location("timecapsule", CLI)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@unittest.skipUnless(sys.platform == "linux" and shutil.which("rsync"), "Linux and rsync required")
class TimeCapsuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / "repo"
        self.source = self.base / "source"
        self.source.mkdir()
        self.call("init", self.repo)

    def call(self, *args, ok=True, env=None):
        p = subprocess.run([sys.executable, str(CLI), *map(str, args)],
                           capture_output=True, text=True, env=env)
        if ok:
            self.assertEqual(p.returncode, 0, f"stdout={p.stdout}\nstderr={p.stderr}")
        else:
            self.assertNotEqual(p.returncode, 0, "expected command failure")
            self.assertIn("timecapsule:", p.stderr)
        return p

    def ids(self):
        return [line.split("\t")[0] for line in self.call("list", self.repo).stdout.splitlines()]

    def make_snapshot(self):
        self.call("snapshot", self.repo, self.source)
        return self.ids()[-1]

    def test_history_changed_added_removed_and_spaces_unicode_empty(self):
        (self.source / "nested folder").mkdir()
        (self.source / "nested folder" / "á 🚀.txt").write_bytes(b"original")
        (self.source / "empty").touch()
        (self.source / "obsolete").write_text("delete me")
        sid1 = self.make_snapshot()
        (self.source / "nested folder" / "á 🚀.txt").write_bytes(b"updated value")
        (self.source / "newfile").write_bytes(b"new")
        (self.source / "obsolete").unlink()
        sid2 = self.make_snapshot()
        self.assertEqual(len(self.ids()), 2)
        self.call("verify", self.repo)
        a, b = self.base / "result1", self.base / "result2"
        self.call("restore", self.repo, sid1, a)
        self.call("restore", self.repo, sid2, b)
        self.assertEqual((a / "nested folder" / "á 🚀.txt").read_bytes(), b"original")
        self.assertEqual((b / "nested folder" / "á 🚀.txt").read_bytes(), b"updated value")
        self.assertTrue((a / "obsolete").exists())
        self.assertFalse((b / "obsolete").exists())
        self.assertEqual((b / "newfile").read_bytes(), b"new")
        self.assertEqual((a / "empty").stat().st_size, 0)
        self.assertEqual((a / "nested folder").stat().st_mtime_ns,
                         (self.repo / "snapshots" / sid1 / "data" / "nested folder").stat().st_mtime_ns)

    def test_symlinks_are_preserved_not_traversed(self):
        external = self.base / "secret-outside.txt"
        external.write_text("NEVER COPIED")
        (self.source / "outside").symlink_to(external)
        (self.source / "broken").symlink_to("does-not-exist")
        (self.source / "cycle").symlink_to("cycle")
        (self.source / "subdir").mkdir()
        (self.source / "subdir" / "up").symlink_to("../subdir")
        sid = self.make_snapshot()
        out = self.base / "restored"
        self.call("restore", self.repo, sid, out)
        self.assertTrue((out / "outside").is_symlink())
        self.assertEqual(os.readlink(out / "outside"), str(external))
        self.assertTrue((out / "broken").is_symlink())
        self.assertEqual(os.readlink(out / "cycle"), "cycle")
        self.assertEqual(os.readlink(out / "subdir" / "up"), "../subdir")
        data = self.repo / "snapshots" / sid / "data"
        self.assertTrue((data / "outside").is_symlink())
        self.assertFalse((data / "secret-outside.txt").exists())

    def test_mode_mtime_hardlinks_and_xattr(self):
        a = self.source / "a"
        a.write_bytes(b"hardlink contents")
        a.chmod(0o640)
        os.utime(a, ns=(1590000000123456789, 1590000000123456789))
        os.link(a, self.source / "b")
        xattr_supported = False
        try:
            os.setxattr(a, "user.capsule_test", b"value")
            xattr_supported = True
        except OSError as exc:
            if exc.errno not in (errno.EOPNOTSUPP, errno.ENOTSUP, errno.EPERM):
                raise
        sid = self.make_snapshot()
        out = self.base / "restored"
        self.call("restore", self.repo, sid, out)
        self.assertEqual((out / "a").stat().st_mode & 0o777, 0o640)
        self.assertEqual((out / "a").stat().st_mtime_ns, 1590000000123456789)
        self.assertEqual((out / "a").stat().st_ino, (out / "b").stat().st_ino)
        if xattr_supported:
            self.assertEqual(os.getxattr(out / "a", "user.capsule_test"), b"value")

    def test_unchanged_files_hardlinked_across_snapshots(self):
        (self.source / "stable").write_bytes(b"a" * (2 * 1024 * 1024))
        (self.source / "changed").write_bytes(b"old")
        first = self.make_snapshot()
        (self.source / "changed").write_bytes(b"new")
        second = self.make_snapshot()
        s1 = self.repo / "snapshots" / first / "data"
        s2 = self.repo / "snapshots" / second / "data"
        self.assertEqual((s1 / "stable").stat().st_ino, (s2 / "stable").stat().st_ino)
        self.assertNotEqual((s1 / "changed").stat().st_ino, (s2 / "changed").stat().st_ino)
        self.assertEqual((s1 / "changed").read_bytes(), b"old")
        self.assertEqual((s2 / "changed").read_bytes(), b"new")

    def test_same_size_same_mtime_content_change_not_missed(self):
        a = self.source / "content"
        a.write_bytes(b"AAAA")
        sid1 = self.make_snapshot()
        old = a.stat()
        a.write_bytes(b"BBBB")
        os.utime(a, ns=(old.st_atime_ns, old.st_mtime_ns))
        sid2 = self.make_snapshot()
        old_data = self.repo / "snapshots" / sid1 / "data" / "content"
        new_data = self.repo / "snapshots" / sid2 / "data" / "content"
        self.assertEqual(old_data.read_bytes(), b"AAAA")
        self.assertEqual(new_data.read_bytes(), b"BBBB")
        self.assertNotEqual(old_data.stat().st_ino, new_data.stat().st_ino)

    def test_restore_refuses_existing_directory_file_and_dangling_link(self):
        (self.source / "file").write_text("safe")
        sid = self.make_snapshot()
        for name in ("existingdir", "existingfile", "dangling"):
            target = self.base / name
            if name == "existingdir":
                target.mkdir()
                (target / "valuable").write_text("keep")
            elif name == "existingfile":
                target.write_text("keep")
            else:
                target.symlink_to("nothing")
            self.call("restore", self.repo, sid, target, ok=False)
            if name == "existingdir":
                self.assertEqual((target / "valuable").read_text(), "keep")
            if name == "existingfile":
                self.assertEqual(target.read_text(), "keep")
            if name == "dangling":
                self.assertEqual(os.readlink(target), "nothing")

    def test_rename_noreplace_is_atomic(self):
        src, dst = self.base / "from", self.base / "to"
        src.mkdir()
        dst.mkdir()
        (dst / "important").write_text("unchanged")
        with self.assertRaises(OSError) as raised:
            module.rename_noreplace(src, dst)
        self.assertEqual(raised.exception.errno, errno.EEXIST)
        self.assertTrue(src.is_dir())
        self.assertEqual((dst / "important").read_text(), "unchanged")

    def test_failure_does_not_publish_partial_or_destroy_previous(self):
        (self.source / "data").write_text("one")
        first = self.make_snapshot()
        (self.source / "data").write_text("two")
        wrapper = self.base / "bin"
        wrapper.mkdir()
        real_rsync = shutil.which("rsync")
        fake = wrapper / "rsync"
        fake.write_text("#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then exec '" + real_rsync + "' \"$@\"; fi\necho 'simulated disk full' >&2\nexit 11\n")
        fake.chmod(0o755)
        env = dict(os.environ, PATH=str(wrapper) + os.pathsep + os.environ["PATH"])
        p = self.call("snapshot", self.repo, self.source, ok=False, env=env)
        self.assertIn("simulated disk full", p.stderr)
        self.assertEqual(self.ids(), [first])
        self.call("verify", self.repo, first)
        self.call("restore", self.repo, first, self.base / "safe")
        self.assertEqual((self.base / "safe" / "data").read_text(), "one")
        # Simulate a hard-killed parent leaving unfinished work in staging.
        (self.repo / ".staging" / "pending-after-crash").mkdir()
        self.assertEqual(self.ids(), [first])

    def test_source_change_during_copy_aborts_publish(self):
        (self.source / "file").write_text("initial")
        first = self.make_snapshot()
        wrapper = self.base / "bin"
        wrapper.mkdir()
        real_rsync = shutil.which("rsync")
        fake = wrapper / "rsync"
        fake.write_text("#!/bin/sh\n'" + real_rsync + "' \"$@\" || exit $?\n"
                        "if [ \"$1\" != \"--version\" ]; then printf 'race' > '" + str(self.source / "file") + "'; fi\n")
        fake.chmod(0o755)
        env = dict(os.environ, PATH=str(wrapper) + os.pathsep + os.environ["PATH"])
        self.call("snapshot", self.repo, self.source, ok=False, env=env)
        self.assertEqual(self.ids(), [first])
        self.call("verify", self.repo)

    def test_corruption_detected_and_restore_refused(self):
        (self.source / "file").write_bytes(b"correct")
        sid = self.make_snapshot()
        snap_file = self.repo / "snapshots" / sid / "data" / "file"
        snap_file.write_bytes(b"CORRUPT")
        self.call("verify", self.repo, sid, ok=False)
        dest = self.base / "should-not-exist"
        self.call("restore", self.repo, sid, dest, ok=False)
        self.assertFalse(dest.exists())

    def test_invalid_snapshot_ids_and_source_overlap(self):
        self.call("restore", self.repo, "../unsafe", self.base / "dest", ok=False)
        self.call("snapshot", self.repo, self.repo, ok=False)
        self.call("snapshot", self.repo, self.base, ok=False)
        self.call("snapshot", self.repo, self.base / "missing", ok=False)

    def test_source_contents_and_modification_metadata_not_rewritten(self):
        sourcefile = self.source / "stable"
        sourcefile.write_bytes(b"do not change source")
        ts = 1500000000000000000
        os.utime(sourcefile, ns=(ts, ts))
        os.utime(self.source, ns=(ts, ts))
        before_file = sourcefile.stat()
        before_dir = self.source.stat()
        self.make_snapshot()
        after_file = sourcefile.stat()
        after_dir = self.source.stat()
        self.assertEqual(before_file.st_mtime_ns, after_file.st_mtime_ns)
        self.assertEqual(before_file.st_ctime_ns, after_file.st_ctime_ns)
        self.assertEqual(before_dir.st_mtime_ns, after_dir.st_mtime_ns)
        self.assertEqual(before_dir.st_ctime_ns, after_dir.st_ctime_ns)
        # Access times are read-side effects on some filesystems even with O_NOATIME.
        self.assertEqual(sourcefile.read_bytes(), b"do not change source")

    def test_special_fifo_fails_safely(self):
        os.mkfifo(self.source / "fifo")
        self.call("snapshot", self.repo, self.source, ok=False)
        self.assertEqual(self.ids(), [])

    def test_scale_many_files(self):
        folder = self.source / "many"
        folder.mkdir()
        for n in range(250):
            (folder / f"file-{n:04d}").write_bytes(bytes([n % 256]) * (n % 99))
        first = self.make_snapshot()
        for n in range(0, 250, 25):
            (folder / f"file-{n:04d}").write_bytes(b"changed")
        second = self.make_snapshot()
        self.call("verify", self.repo)
        self.call("restore", self.repo, second, self.base / "many-restored")
        self.assertEqual(len(list((self.base / "many-restored" / "many").iterdir())), 250)
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
