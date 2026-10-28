# filesDiff

Find repeated files recursively inside a folder and write a report to `result_log.txt`.

Two ways of finding repeated files:

- **Name + size** (default): files with the same file name and the same size.
- **Content** (`--content`): files with exactly the same bytes, whatever their names are.

Requires Python 3.12+. No extra packages needed.

## Usage

```bash
python3 find_duplicates.py [folder] [-o OUTPUT] [--content] [--skip-empty]
```

| Option | Description |
|---|---|
| `folder` | Folder to scan, including all subfolders. Default: `test` |
| `-o`, `--output` | Report file. Default: `result_log.txt` in the current directory |
| `--content` | Compare file contents instead of name and size |
| `--skip-empty` | Ignore empty (0-byte) files. Only applies with `--content` |

### Examples

```bash
# Same name and size, scanning ./test
python3 find_duplicates.py

# Same name and size in another folder, custom report file
python3 find_duplicates.py ~/Pictures -o pictures_log.txt

# Identical content, even when the names differ
python3 find_duplicates.py ~/Pictures --content

# Identical content, ignoring empty files
python3 find_duplicates.py ~/Pictures --content --skip-empty
```

## Report format

Name + size mode:

```
Duplicate files (same name and size) found in: /path/to/test
Total duplicate groups: 1

test1.txt (0 bytes) - 2 locations
  /path/to/test/organizar/test1.txt
  /path/to/test/test1.txt
```

Content mode (each group is labelled with the start of its content hash):

```
Duplicate files (same content) found in: /path/to/folder
Total duplicate groups: 1

hash 0529418c2ac9aeab (200000000 bytes) - 2 locations
  /path/to/folder/big.bin
  /path/to/folder/sub/renamed.bin
```

If nothing is found, the report says `No duplicate files found.`

## How content mode works

Each check only runs on the files that passed the previous one, so large files are read in full only when they are very likely duplicates:

1. **Same size.** Instant, no file is read.
2. **Same first and last 64 KB.** Reads only 128 KB per file.
3. **Same full content (BLAKE2b hash).** Reads the whole file in 1 MB chunks, so memory use stays around 1 MB even for 1 GB files. Files of 100 MB or more show a progress percentage in the terminal.

## Notes

- Shortcuts to other files or folders (symlinks) are skipped, so the scan can't loop.
- Files that can't be read (for example, no permission) are skipped instead of stopping the scan.
- The report file is never counted as a duplicate, even if it's inside the scanned folder.
- In name + size mode, two different files can match by chance (for example, two empty files with the same name). Use `--content` when you need to be sure the files are identical.
