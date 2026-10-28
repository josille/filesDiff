import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "find_duplicates.py"
sys.path.insert(0, str(ROOT))

import find_duplicates as fd  # noqa: E402


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # Resolve so paths match the script's output (/var -> /private/var on macOS).
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, rel: str, data: bytes = b"") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class FindDuplicatesTest(TempDirTestCase):
    def test_same_name_and_size_in_different_folders(self):
        a = self.make("a/file.txt", b"abc")
        b = self.make("b/c/file.txt", b"xyz")
        self.assertEqual(fd.find_duplicates(self.root), {("file.txt", 3): [a, b]})

    def test_same_name_different_size_not_reported(self):
        self.make("a/file.txt", b"abc")
        self.make("b/file.txt", b"abcd")
        self.assertEqual(fd.find_duplicates(self.root), {})

    def test_different_name_same_content_not_reported(self):
        self.make("one.txt", b"same")
        self.make("two.txt", b"same")
        self.assertEqual(fd.find_duplicates(self.root), {})

    def test_more_than_two_locations(self):
        paths = [self.make(f"{d}/x.bin", b"12") for d in ("a", "b", "c")]
        self.assertEqual(fd.find_duplicates(self.root), {("x.bin", 2): paths})

    def test_exclude_path_is_ignored(self):
        self.make("a/result_log.txt", b"1")
        report = self.make("result_log.txt", b"1")
        self.assertEqual(fd.find_duplicates(self.root, exclude=report), {})

    def test_symlinks_are_skipped(self):
        target = self.make("a/file.txt", b"abc")
        (self.root / "b").mkdir()
        try:
            os.symlink(target, self.root / "b" / "file.txt")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported")
        self.assertEqual(fd.find_duplicates(self.root), {})

    def test_empty_folder(self):
        self.assertEqual(fd.find_duplicates(self.root), {})


class HashTest(TempDirTestCase):
    def test_full_hash_matches_blake2b(self):
        data = os.urandom(3 * fd.CHUNK_SIZE + 123)
        path = self.make("f.bin", data)
        self.assertEqual(fd.full_hash(path, len(data)), hashlib.blake2b(data).hexdigest())

    def test_partial_hash_ignores_middle_of_large_file(self):
        size = 4 * fd.PARTIAL_BLOCK
        data = bytearray(os.urandom(size))
        a = self.make("a.bin", bytes(data))
        data[size // 2] ^= 0xFF
        b = self.make("b.bin", bytes(data))
        self.assertEqual(fd.partial_hash(a, size), fd.partial_hash(b, size))
        self.assertNotEqual(fd.full_hash(a, size), fd.full_hash(b, size))

    def test_partial_hash_detects_different_end(self):
        size = 4 * fd.PARTIAL_BLOCK
        data = bytearray(os.urandom(size))
        a = self.make("a.bin", bytes(data))
        data[-1] ^= 0xFF
        b = self.make("b.bin", bytes(data))
        self.assertNotEqual(fd.partial_hash(a, size), fd.partial_hash(b, size))

    def test_progress_not_printed_when_not_a_terminal(self):
        path = self.make("f.bin", b"x" * 10)
        stderr = io.StringIO()
        with mock.patch.object(fd, "PROGRESS_THRESHOLD", 0), mock.patch.object(sys, "stderr", stderr):
            fd.full_hash(path, 10)
        self.assertEqual(stderr.getvalue(), "")

    def test_progress_printed_in_terminal(self):
        class FakeTTY(io.StringIO):
            def isatty(self):
                return True

        path = self.make("f.bin", b"x" * 10)
        stderr = FakeTTY()
        with mock.patch.object(fd, "PROGRESS_THRESHOLD", 0), mock.patch.object(sys, "stderr", stderr):
            fd.full_hash(path, 10)
        self.assertIn("hashing f.bin: 100%", stderr.getvalue())


class FindContentDuplicatesTest(TempDirTestCase):
    def test_same_content_different_names(self):
        a = self.make("one.txt", b"hello")
        b = self.make("sub/two.md", b"hello")
        self.make("three.txt", b"world")
        groups = fd.find_content_duplicates(self.root)
        self.assertEqual(list(groups.values()), [[a, b]])
        (digest, size), = groups.keys()
        self.assertEqual(size, 5)
        self.assertEqual(digest, fd.partial_hash(a, 5))

    def test_large_identical_files(self):
        data = os.urandom(5 * fd.PARTIAL_BLOCK)
        a = self.make("big.bin", data)
        b = self.make("sub/renamed.bin", data)
        groups = fd.find_content_duplicates(self.root)
        self.assertEqual(groups, {(hashlib.blake2b(data).hexdigest(), len(data)): [a, b]})

    def test_large_files_differing_in_middle_not_reported(self):
        data = bytearray(os.urandom(5 * fd.PARTIAL_BLOCK))
        self.make("a.bin", bytes(data))
        data[len(data) // 2] ^= 0xFF
        self.make("b.bin", bytes(data))
        self.assertEqual(fd.find_content_duplicates(self.root), {})

    def test_full_hash_only_for_partial_matches(self):
        size = 5 * fd.PARTIAL_BLOCK
        self.make("a.bin", os.urandom(size))
        self.make("b.bin", os.urandom(size))
        with mock.patch.object(fd, "full_hash", wraps=fd.full_hash) as spy:
            fd.find_content_duplicates(self.root)
        spy.assert_not_called()

    def test_empty_files_grouped_unless_skipped(self):
        a = self.make("a.txt")
        b = self.make("b.txt")
        self.assertEqual(list(fd.find_content_duplicates(self.root).values()), [[a, b]])
        self.assertEqual(fd.find_content_duplicates(self.root, skip_empty=True), {})

    def test_groups_sorted_by_size_descending(self):
        self.make("s1.txt", b"ab")
        self.make("s2.txt", b"ab")
        self.make("l1.txt", b"abcdef")
        self.make("l2.txt", b"abcdef")
        sizes = [size for _, size in fd.find_content_duplicates(self.root)]
        self.assertEqual(sizes, [6, 2])

    def test_exclude_path_is_ignored(self):
        self.make("copy.txt", b"report")
        report = self.make("result_log.txt", b"report")
        self.assertEqual(fd.find_content_duplicates(self.root, exclude=report), {})

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "needs POSIX permissions as non-root")
    def test_unreadable_file_is_skipped(self):
        a = self.make("a.txt", b"data")
        b = self.make("b.txt", b"data")
        locked = self.make("c.txt", b"data")
        locked.chmod(0)
        try:
            groups = fd.find_content_duplicates(self.root)
        finally:
            locked.chmod(0o644)
        self.assertEqual(list(groups.values()), [[a, b]])


class WriteReportTest(TempDirTestCase):
    def test_no_duplicates(self):
        out = self.root / "r.txt"
        fd.write_report(self.root, {}, out)
        self.assertEqual(
            out.read_text(encoding="utf-8"),
            f"Duplicate files (same name and size) found in: {self.root}\nNo duplicate files found.\n",
        )

    def test_name_mode_format(self):
        out = self.root / "r.txt"
        groups = {("file.txt", 3): [Path("/x/file.txt"), Path("/y/file.txt")]}
        fd.write_report(self.root, groups, out)
        self.assertEqual(
            out.read_text(encoding="utf-8"),
            f"Duplicate files (same name and size) found in: {self.root}\n"
            "Total duplicate groups: 1\n"
            "\n"
            "file.txt (3 bytes) - 2 locations\n"
            f"  {Path('/x/file.txt')}\n"
            f"  {Path('/y/file.txt')}\n",
        )

    def test_content_mode_shows_short_hash(self):
        out = self.root / "r.txt"
        digest = "0123456789abcdef" + "f" * 112
        fd.write_report(self.root, {(digest, 7): [Path("/a"), Path("/b")]}, out, content=True)
        text = out.read_text(encoding="utf-8")
        self.assertIn("Duplicate files (same content)", text)
        self.assertIn("hash 0123456789abcdef (7 bytes) - 2 locations", text)
        self.assertNotIn(digest, text)


class CommandLineTest(TempDirTestCase):
    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.root,
            capture_output=True,
            text=True,
        )

    def test_default_scans_test_folder_and_writes_result_log(self):
        self.make("test/a.txt", b"1")
        self.make("test/sub/a.txt", b"2")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Found 1 duplicate group(s)", result.stdout)
        report = (self.root / "result_log.txt").read_text(encoding="utf-8")
        self.assertIn("a.txt (1 bytes) - 2 locations", report)

    def test_content_mode_with_custom_output(self):
        self.make("data/one.txt", b"same")
        self.make("data/two.txt", b"same")
        result = self.run_script("data", "--content", "-o", "out.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = (self.root / "out.txt").read_text(encoding="utf-8")
        self.assertIn("(same content)", report)
        self.assertIn("one.txt", report)
        self.assertIn("two.txt", report)

    def test_report_inside_scanned_folder_is_not_counted(self):
        self.make("data/result_log.txt", b"old")
        self.make("data/sub/result_log.txt", b"old")
        result = self.run_script("data", "-o", "data/result_log.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Found 0 duplicate group(s)", result.stdout)

    def test_missing_folder_is_an_error(self):
        result = self.run_script("does_not_exist")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a directory", result.stderr)


if __name__ == "__main__":
    unittest.main()
