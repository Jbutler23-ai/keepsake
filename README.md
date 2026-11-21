# Keepsake

Keepsake is an offline backup and recovery project. The working prototype captures local folders, reuses identical file contents, compares snapshots, checks stored data, and restores an earlier version into a new folder.

The planned system will use five local node processes to coordinate replicated storage, resumable capture and transfers, concurrent snapshot branches, repair, safe retention, and disaster recovery. It will run on one supported desktop through loopback or controlled message queues. Separate node directories simulate storage faults; they are not independent physical disks.

Only the single-vault Python engine and CLI described below exist today. There is no cluster, consensus, chunking, resumable job, applied retention, or replication command in the current code. The [target system design](docs/system-design.md) defines those future protocols and failure boundaries.

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

## Target system

The target separates immutable chunks and independently readable manifests from a replicated metadata log. By default, publication will require three verified durable data replicas and a majority metadata commit. Lost responses will be resolved through stable operation IDs; an uncertain outcome will not be presented as success.

Concurrent captures will remain separate branches. Quorum loss will block fresh metadata queries and new commits. Retention and garbage collection will protect retained snapshots, active restores, unfinished jobs, and exports. Corruption repair will require a healthy copy; temporarily unreachable data will be reported as unavailable.

The [12 planned stages](docs/roadmap.md) are grouped into three phases. A stage may require several coding sessions. The current single-vault tests establish none of these target guarantees.

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

Read the [baseline architecture](docs/architecture.md) for the implemented storage format, the [target system design](docs/system-design.md) for the proposed protocols, and the [roadmap](docs/roadmap.md) for the remaining work. [Data and licensing](docs/data-and-license.md) covers the sample files and the MIT license.
