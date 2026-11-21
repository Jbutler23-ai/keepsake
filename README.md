# Keepsake

Keepsake is an offline backup tool for project folders, notes, and other local files. It saves snapshots, reuses identical file contents, compares versions, checks stored data, and restores an earlier version into a new folder.

The current version has a Python engine and command-line tool. Resuming interrupted work, recovering a damaged catalog, deleting expired snapshots, and moving backups between machines are planned features.

## Run it

Use Python 3.11 or newer with SQLite support. No third-party runtime packages, accounts, or services are needed. Run these commands from the repository root:

```sh
python -m keepsake --help
python -m keepsake --vault ../backup-vault init
python -m keepsake --vault ../backup-vault snapshot ./examples/sample-project --label first
python -m keepsake --vault ../backup-vault list
python -m keepsake --vault ../backup-vault verify
python -m keepsake --vault ../backup-vault stats
python -m keepsake --vault ../backup-vault restore latest ../restored-copy
python -m keepsake --vault ../backup-vault retention-plan --keep-last 3
```

On systems where Python is named `python3`, use that instead. Keep the vault outside the source folder. Neither folder can contain the other.

Place global options before the command. For machine-readable output:

```sh
python -m keepsake --vault ../backup-vault --json list
```

The `list` command shows snapshot IDs. Use a full ID to select a specific version, or `latest` for the newest one. Run `python -m keepsake COMMAND --help` for a command's arguments.

| Command | Purpose |
| --- | --- |
| `init` | Create a vault in a new or empty directory |
| `snapshot SOURCE [--label TEXT] [--exclude PATTERN]` | Capture a folder; repeat `--exclude` as needed |
| `list` | List snapshots, newest first |
| `diff BEFORE AFTER` | Compare two snapshot IDs |
| `restore SNAPSHOT DESTINATION` | Restore into a new folder |
| `verify [SNAPSHOT]` | Check a snapshot, or all snapshots when omitted |
| `stats` | Show storage totals and orphan object counts |
| `retention-plan --keep-last COUNT` | Preview retention; `COUNT` must be positive |

Successful commands exit with status `0`. Verification issues return `1`; invalid requests and expected failures return `2`; an interrupted command returns `130`. With `--json`, results go to standard output and operational errors go to standard error as an `error` object. Invalid command arguments still produce plain-text usage errors.

## What works now

- Capture regular files and empty directories in a local folder.
- Store file contents under their SHA-256 digest. Identical bytes share one stored object, including across snapshots.
- Keep a SQLite catalog of snapshots and relative paths.
- Compare two snapshots for added, deleted, modified, and metadata-only changes.
- Read stored objects back and verify their hashes.
- Restore into a new destination, checking contents before publishing the restored folder.
- Show storage totals and preview which snapshots a keep-last retention rule would remove.

Retention is a preview only. There is no delete or garbage-collection command in this version.

## Capture and restore behavior

Nothing is excluded by default. Add `--exclude` for paths you don't want captured:

```sh
python -m keepsake --vault ../backup-vault snapshot ./examples/sample-project --exclude .git --exclude '*.tmp'
```

Exclusions use shell-style patterns matched against each relative path and basename. A matching directory is skipped with its descendants. These are not `.gitignore` rules: negation, precedence, and git-specific behavior are not supported.

Symbolic links, Windows reparse points, and special files are skipped and reported. File identity, size, and timestamps are checked around each read, but a folder capture is not an atomic filesystem snapshot. Pause programs that need several files to change together before capturing their data.

A restore destination must not already exist, and its parent must exist. The destination cannot be inside the vault or underneath a symbolic link or reparse point. Restores use a temporary sibling folder and publish it after content checks finish. There is no overwrite mode. Keep other processes from changing the destination or its parent during a restore; the [architecture](docs/architecture.md) explains the filesystem race limits.

The vault contains unencrypted file contents and source paths. Anyone who can read it can read the backup. Encryption is outside the project scope. Permissions beyond ordinary file modes, extended attributes, and preservation of hard-link relationships are outside the baseline.

## Tests

```sh
python -m unittest discover -s tests -v
python scripts/demo.py
python scripts/benchmark.py --json
```

Tests use temporary directories and synthetic files. The demo captures two versions, checks deduplication, verifies the vault, and checks a restore. The benchmark builds a repeatable corpus, checks storage reuse, and compares restored files with independent hashes. Timings depend on the machine running it. See [acceptance checks](docs/acceptance.md) for the baseline checks and future targets.

## Project layout

```text
keepsake/       Storage engine, models, and CLI
tests/          Behavior and failure-case tests
examples/       Small synthetic input files
scripts/        Local development helpers
docs/           Architecture, acceptance criteria, and roadmap
```

Read the [architecture](docs/architecture.md) for the storage format and failure boundaries, or the [roadmap](docs/roadmap.md) for the remaining work. [Data and licensing](docs/data-and-license.md) covers the sample files and the MIT license.
