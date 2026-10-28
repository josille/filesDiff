# filesDiff

Find repeated files recursively inside a folder, or files that exist in two folders, and write a report to `result_log.txt`.

Two ways of finding repeated files:

- **Name + size** (default): files with the same file name and the same size.
- **Content** (`--content`): files with exactly the same bytes, whatever their names are.

Requires Python 3.8+. No extra packages needed.

## Usage

```bash
python3 find_duplicates.py [folder] [-o OUTPUT] [--content] [--skip-empty] [--compare FOLDER_B]
```

| Option | Description |
|---|---|
| `folder` | Folder to scan, including all subfolders. Default: `test` |
| `-o`, `--output` | Report file. Default: `result_log.txt` in the current directory |
| `--content` | Compare file contents instead of name and size |
| `--skip-empty` | Ignore empty (0-byte) files. Only applies with `--content` |
| `--compare FOLDER_B` | Only report files found in both `folder` and `FOLDER_B`. Works with either matching mode |

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

# Files in ~/Pictures that already exist in the backup, by content
python3 find_duplicates.py ~/Pictures --compare /Volumes/Backup/Pictures --content
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

## Comparing two folders

With `--compare FOLDER_B`, only groups that have at least one file in each folder are reported. Duplicates that exist inside just one of the folders are left out. When a file is in both, every copy is listed, grouped by folder:

```
Files found in both folders (same name and size):
  A: /path/to/Pictures
  B: /path/to/Backup/Pictures
Total duplicate groups: 1

x.jpg (2048 bytes) - 3 locations
  A:
    /path/to/Pictures/2024/x.jpg
    /path/to/Pictures/x.jpg
  B:
    /path/to/Backup/Pictures/x.jpg
```

Folder structure doesn't matter: a file can be in any subfolder of A and any subfolder of B. The two folders must not be the same, and one can't be inside the other. If nothing is in both, the report says `No files found in both folders.`

With `--content`, files whose size exists in only one of the folders are never read, so comparing large folders stays fast.

## How content mode works

Each check only runs on the files that passed the previous one, so large files are read in full only when they are very likely duplicates:

1. **Same size.** Instant, no file is read.
2. **Same first and last 64 KB.** Reads only 128 KB per file.
3. **Same full content (BLAKE2b hash).** Reads the whole file in 1 MB chunks, so memory use stays around 1 MB even for 1 GB files. Files of 100 MB or more show a progress percentage in the terminal.

## Running the tests

The tests use Python's built-in `unittest` module, so there's nothing to install. From the project folder:

```bash
python3 -m unittest discover -s tests -v
```

They create their own temporary files and don't touch the `test/` folder. `pytest` also works if you have it installed.

## Notes

- Shortcuts to other files or folders (symlinks) are skipped, so the scan can't loop.
- Files that can't be read (for example, no permission) are skipped instead of stopping the scan.
- The report file is never counted as a duplicate, even if it's inside the scanned folder.
- In name + size mode, two different files can match by chance (for example, two empty files with the same name). Use `--content` when you need to be sure the files are identical.
