"""Find repeated files recursively inside a folder.

Default mode groups files by name and size. With --content, files are grouped by
their actual content (same bytes, any name): size -> partial hash -> full hash.
"""

import argparse
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

CHUNK_SIZE = 1024 * 1024           # 1 MB read chunks for full hashing
PARTIAL_BLOCK = 64 * 1024          # bytes read from start and end for the quick check
PROGRESS_THRESHOLD = 100 * 1024 * 1024  # show progress when hashing files >= 100 MB


def iter_files(root: Path, exclude: Path | None = None):
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve()
            if exclude is not None and resolved == exclude:
                continue
            yield resolved, path.stat().st_size
        except OSError:
            continue


def find_duplicates(root: Path, exclude: Path | None = None) -> dict[tuple[str, int], list[Path]]:
    groups: dict[tuple[str, int], list[Path]] = defaultdict(list)
    for path, size in iter_files(root, exclude):
        groups[(path.name, size)].append(path)
    return {key: sorted(paths) for key, paths in sorted(groups.items()) if len(paths) > 1}


def partial_hash(path: Path, size: int, block: int = PARTIAL_BLOCK) -> str:
    h = hashlib.blake2b()
    with path.open("rb") as f:
        h.update(f.read(block))
        if size > 2 * block:
            f.seek(-block, 2)
            h.update(f.read(block))
    return h.hexdigest()


def full_hash(path: Path, size: int) -> str:
    h = hashlib.blake2b()
    show_progress = size >= PROGRESS_THRESHOLD and sys.stderr.isatty()
    done = 0
    last_pct = -1
    with path.open("rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
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
    root: Path, exclude: Path | None = None, skip_empty: bool = False
) -> dict[tuple[str, int], list[Path]]:
    by_size: dict[int, list[Path]] = defaultdict(list)
    for path, size in iter_files(root, exclude):
        if skip_empty and size == 0:
            continue
        by_size[size].append(path)

    groups: dict[tuple[str, int], list[Path]] = {}
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue

        by_partial: dict[str, list[Path]] = defaultdict(list)
        for p in paths:
            try:
                by_partial[partial_hash(p, size)].append(p)
            except OSError:
                continue

        for candidates in by_partial.values():
            if len(candidates) < 2:
                continue
            # Small files are fully covered by the partial hash already.
            if size <= 2 * PARTIAL_BLOCK:
                groups[(partial_hash(candidates[0], size), size)] = sorted(candidates)
                continue
            by_full: dict[str, list[Path]] = defaultdict(list)
            for p in candidates:
                try:
                    by_full[full_hash(p, size)].append(p)
                except OSError:
                    continue
            for digest, same in by_full.items():
                if len(same) > 1:
                    groups[(digest, size)] = sorted(same)

    return dict(sorted(groups.items(), key=lambda item: (-item[0][1], item[1][0])))


def write_report(
    root: Path, groups: dict[tuple[str, int], list[Path]], output: Path, content: bool = False
) -> None:
    mode = "same content" if content else "same name and size"
    lines = [f"Duplicate files ({mode}) found in: {root.resolve()}"]
    if not groups:
        lines.append("No duplicate files found.")
    else:
        lines.append(f"Total duplicate groups: {len(groups)}")
        for (key, size), paths in groups.items():
            lines.append("")
            label = f"hash {key[:16]}" if content else key
            lines.append(f"{label} ({size} bytes) - {len(paths)} locations")
            lines.extend(f"  {p}" for p in paths)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", nargs="?", default="test", help="folder to scan (default: test)")
    parser.add_argument("-o", "--output", default="result_log.txt", help="report file (default: result_log.txt)")
    parser.add_argument("--content", action="store_true", help="compare file contents instead of name and size")
    parser.add_argument("--skip-empty", action="store_true", help="ignore empty files (with --content)")
    args = parser.parse_args()

    root = Path(args.folder)
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    output = Path(args.output)

    if args.content:
        groups = find_content_duplicates(root, exclude=output.resolve(), skip_empty=args.skip_empty)
    else:
        groups = find_duplicates(root, exclude=output.resolve())
    write_report(root, groups, output, content=args.content)
    print(f"Found {len(groups)} duplicate group(s). Report written to {output.resolve()}")


if __name__ == "__main__":
    main()
