"""Find repeated files recursively inside a folder.

Default mode groups files by name and size. With --content, files are grouped by
their actual content (same bytes, any name): size -> partial hash -> full hash.
With --compare FOLDER_B, only files found in both folders are reported.
With --progress, a progress bar is shown on stderr for each step.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from collections import defaultdict
from pathlib import Path

CHUNK_SIZE = 1024 * 1024           # 1 MB read chunks for full hashing
PARTIAL_BLOCK = 64 * 1024          # bytes read from start and end for the quick check
PROGRESS_THRESHOLD = 100 * 1024 * 1024  # show progress when hashing files >= 100 MB


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class ProgressBar:
    """One-line progress bar on stderr. Without a total it shows a running count.

    In a terminal the line is redrawn in place; otherwise a line is printed every 10%
    (or once at the end for counters) so logs stay readable.
    """

    WIDTH = 30
    REDRAW_INTERVAL = 0.1  # seconds between counter redraws

    def __init__(self, label: str, total: int | None = None, unit: str = "files", enabled: bool = True):
        self.label = label
        self.total = total
        self.unit = unit
        self.enabled = enabled
        self.done = 0
        self.stream = sys.stderr
        self.tty = self.stream.isatty()
        self._last_key = None
        self._last_time = 0.0
        self._line_len = 0
        if enabled:
            self._draw(force=True)

    def update(self, n: int = 1) -> None:
        if not self.enabled:
            return
        self.done += n
        self._draw()

    def close(self) -> None:
        if not self.enabled:
            return
        if self.total is not None:
            self.done = self.total
        # Outside a terminal a bar that already logged 100% shouldn't log it again.
        self._draw(force=self.tty or self.total is None)
        if self.tty:
            self.stream.write("\n")
            self.stream.flush()
        self.enabled = False

    def _fmt(self, value: int) -> str:
        return human_size(value) if self.unit == "bytes" else f"{value:,} {self.unit}"

    def _draw(self, force: bool = False) -> None:
        if self.total is None:
            now = time.monotonic()
            if not force and (not self.tty or now - self._last_time < self.REDRAW_INTERVAL):
                return
            self._last_time = now
            if not self.tty and self.done == 0:
                return  # nothing useful to log yet
            text = f"{self.label}: {self._fmt(self.done)}"
        else:
            pct = 100 if self.total == 0 else min(100, self.done * 100 // self.total)
            key = pct if self.tty else pct // 10
            if not force and key == self._last_key:
                return
            self._last_key = key
            filled = self.WIDTH * pct // 100
            bar = "#" * filled + "." * (self.WIDTH - filled)
            text = f"{self.label} [{bar}] {pct:3d}% ({self._fmt(self.done)} / {self._fmt(self.total)})"
        if self.tty:
            self.stream.write("\r" + text.ljust(self._line_len))
            self._line_len = len(text)
        else:
            self.stream.write(text + "\n")
        self.stream.flush()


def file_info(path: Path, exclude: Path | None = None) -> tuple[Path, int] | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        resolved = path.resolve()
        if exclude is not None and resolved == exclude:
            return None
        return resolved, path.stat().st_size
    except OSError:
        return None


def iter_files(root: Path, exclude: Path | None = None):
    for path in root.rglob("*"):
        info = file_info(path, exclude)
        if info is not None:
            yield info


def iter_roots(root: Path, compare: Path | None, exclude: Path | None, progress: bool = False):
    roots = [root] if compare is None else [root, compare]
    if not progress:
        for r in roots:
            yield from iter_files(r, exclude)
        return

    # List everything first so the second step can show a real percentage.
    bar = ProgressBar("Listing", unit="entries")
    paths = []
    for r in roots:
        for path in r.rglob("*"):
            paths.append(path)
            bar.update()
    bar.close()

    bar = ProgressBar("Checking", total=len(paths), unit="entries")
    for path in paths:
        info = file_info(path, exclude)
        bar.update()
        if info is not None:
            yield info
    bar.close()


def group_filter(root: Path, compare: Path | None):
    """Return a predicate telling whether a group of paths should be kept."""
    if compare is None:
        return lambda paths: len(paths) > 1
    a, b = root.resolve(), compare.resolve()
    return lambda paths: any(a in p.parents for p in paths) and any(b in p.parents for p in paths)


def find_duplicates(
    root: Path, exclude: Path | None = None, compare: Path | None = None, progress: bool = False
) -> dict[tuple[str, int], list[Path]]:
    keep = group_filter(root, compare)
    groups: dict[tuple[str, int], list[Path]] = defaultdict(list)
    for path, size in iter_roots(root, compare, exclude, progress):
        groups[(path.name, size)].append(path)
    return {key: sorted(paths) for key, paths in sorted(groups.items()) if keep(paths)}


def partial_hash(path: Path, size: int, block: int = PARTIAL_BLOCK) -> str:
    h = hashlib.blake2b()
    with path.open("rb") as f:
        h.update(f.read(block))
        if size > 2 * block:
            f.seek(-block, 2)
            h.update(f.read(block))
    return h.hexdigest()


def full_hash(path: Path, size: int, on_chunk=None) -> str:
    """Hash the whole file. on_chunk(n) is called after each chunk of n bytes is read.

    Without on_chunk, files >= PROGRESS_THRESHOLD show their own progress line in a terminal.
    """
    h = hashlib.blake2b()
    show_progress = on_chunk is None and size >= PROGRESS_THRESHOLD and sys.stderr.isatty()
    done = 0
    last_pct = -1
    with path.open("rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
            if on_chunk is not None:
                on_chunk(len(chunk))
            if show_progress:
                done += len(chunk)
                pct = done * 100 // size
                if pct != last_pct:
                    last_pct = pct
                    print(f"\r  hashing {path.name}: {pct}%", end="", file=sys.stderr, flush=True)
    if show_progress:
        print(file=sys.stderr)
    return h.hexdigest()


def find_content_duplicates(
    root: Path,
    exclude: Path | None = None,
    skip_empty: bool = False,
    compare: Path | None = None,
    progress: bool = False,
) -> dict[tuple[str, int], list[Path]]:
    keep = group_filter(root, compare)
    by_size: dict[int, list[Path]] = defaultdict(list)
    for path, size in iter_roots(root, compare, exclude, progress):
        if skip_empty and size == 0:
            continue
        by_size[size].append(path)
    same_size = [(size, paths) for size, paths in by_size.items() if keep(paths)]

    # Quick check: hash the first and last PARTIAL_BLOCK bytes of each candidate.
    total = sum(len(paths) for _, paths in same_size)
    bar = ProgressBar("Quick check", total=total, enabled=progress and total > 0)
    by_partial: dict[tuple[int, str], list[Path]] = defaultdict(list)
    for size, paths in same_size:
        for p in paths:
            try:
                by_partial[(size, partial_hash(p, size))].append(p)
            except OSError:
                pass
            bar.update()
    bar.close()

    groups: dict[tuple[str, int], list[Path]] = {}
    needs_full: list[tuple[int, list[Path]]] = []
    for (size, digest), paths in by_partial.items():
        if not keep(paths):
            continue
        # Small files are fully covered by the partial hash already.
        if size <= 2 * PARTIAL_BLOCK:
            groups[(digest, size)] = sorted(paths)
        else:
            needs_full.append((size, paths))

    # Full compare: hash whole files that still match, tracking progress by bytes.
    total = sum(size * len(paths) for size, paths in needs_full)
    bar = ProgressBar("Full compare", total=total, unit="bytes", enabled=progress and total > 0)
    for size, paths in needs_full:
        by_full: dict[str, list[Path]] = defaultdict(list)
        for p in paths:
            try:
                by_full[full_hash(p, size, on_chunk=bar.update if progress else None)].append(p)
            except OSError:
                continue
        for digest, same in by_full.items():
            if keep(same):
                groups[(digest, size)] = sorted(same)
    bar.close()

    return dict(sorted(groups.items(), key=lambda item: (-item[0][1], item[1][0])))


def write_report(
    root: Path,
    groups: dict[tuple[str, int], list[Path]],
    output: Path,
    content: bool = False,
    compare: Path | None = None,
) -> None:
    mode = "same content" if content else "same name and size"
    if compare is None:
        lines = [f"Duplicate files ({mode}) found in: {root.resolve()}"]
    else:
        lines = [f"Files found in both folders ({mode}):", f"  A: {root.resolve()}", f"  B: {compare.resolve()}"]
    if not groups:
        lines.append("No duplicate files found." if compare is None else "No files found in both folders.")
    else:
        lines.append(f"Total duplicate groups: {len(groups)}")
        for (key, size), paths in groups.items():
            lines.append("")
            label = f"hash {key[:16]}" if content else key
            lines.append(f"{label} ({size} bytes) - {len(paths)} locations")
            if compare is None:
                lines.extend(f"  {p}" for p in paths)
                continue
            for tag, folder in (("A", root.resolve()), ("B", compare.resolve())):
                lines.append(f"  {tag}:")
                lines.extend(f"    {p}" for p in paths if folder in p.parents)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


HELP_EPILOG = """\
matching modes:
  name and size (default)
      Files are repeated when they have the same file name and the same size.
      Fast: no file is opened. Files with different names are never matched.
  --content
      Files are repeated when their bytes are identical, whatever their names.
      Checked in steps, each only on files that passed the previous one:
        1. same size
        2. same first and last 64 KB (quick check)
        3. same BLAKE2b hash of the whole file (read in 1 MB chunks)

examples:
  # scan ./test, report to result_log.txt
  %(prog)s
  # same name and size anywhere in ~/Pictures, report to dupes.txt
  %(prog)s ~/Pictures -o dupes.txt
  # identical content with any name, ignoring empty files, with progress bars
  %(prog)s ~/Pictures --content --skip-empty --progress
  # files in ~/Pictures that already exist in the backup, by content
  %(prog)s ~/Pictures --compare /Volumes/Backup/Pictures --content

The report file is never counted as a duplicate, even inside the scanned folder.
Symbolic links and unreadable files are skipped.
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, epilog=HELP_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "folder", nargs="?", default="test", help="folder to scan recursively (default: test)"
    )
    parser.add_argument(
        "-o", "--output", default="result_log.txt",
        help="file the report is written to, overwritten if it exists (default: result_log.txt)",
    )
    parser.add_argument(
        "--content", action="store_true",
        help="match files by identical content instead of name and size (see matching modes)",
    )
    parser.add_argument(
        "--skip-empty", action="store_true",
        help="ignore 0-byte files, which would otherwise all match each other (only with --content)",
    )
    parser.add_argument(
        "--compare", metavar="FOLDER_B",
        help="only report files found in both folder and FOLDER_B; duplicates inside just one "
        "of them are left out. The folders must not be the same or inside each other",
    )
    parser.add_argument(
        "--progress", action="store_true",
        help="show a progress bar on stderr while listing, checking and hashing files",
    )
    args = parser.parse_args()

    root = Path(args.folder)
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    compare = None
    if args.compare is not None:
        compare = Path(args.compare)
        if not compare.is_dir():
            parser.error(f"not a directory: {compare}")
        a, b = root.resolve(), compare.resolve()
        if a == b or a in b.parents or b in a.parents:
            parser.error("folders to compare must not be the same or inside each other")
    output = Path(args.output)

    if args.content:
        groups = find_content_duplicates(
            root, exclude=output.resolve(), skip_empty=args.skip_empty, compare=compare, progress=args.progress
        )
    else:
        groups = find_duplicates(root, exclude=output.resolve(), compare=compare, progress=args.progress)
    write_report(root, groups, output, content=args.content, compare=compare)
    print(f"Found {len(groups)} duplicate group(s). Report written to {output.resolve()}")


if __name__ == "__main__":
    main()
