# Architecture

Keepsake separates a snapshot's manifest from its file contents. The manifest says which paths belong to a snapshot. Objects hold the bytes. A second snapshot can point to the same objects without storing another copy.

## Components and storage

The CLI calls `Repository` in `keepsake.repository`. The engine can also be called directly from Python. Result types live in `keepsake.models`, and expected failures use `KeepsakeError`. No web service or external application is required.

The vault layout is:

```text
vault/
  manifest.sqlite3
  objects/
    ab/
      <remaining 62 hexadecimal digest characters>
  tmp/
  write.lock
```

`write.lock` allows only one writer at a time. An interrupted process can leave a stale lock. Recovery is manual: confirm no writer is running before removing the lock. Deleting a live writer's lock can allow conflicting writes.

The catalog records snapshot identity, creation time, source path, label, and skipped paths. Entries store a relative path, file or directory kind, digest for file contents, size, mode, and modification time. File counts and byte totals are derived from those entries. Snapshot identifiers are full UUID hex strings at the engine API boundary.

## Capturing files

The engine walks the source tree, applies exclusions, and captures regular files and directories. Paths in manifests use forward slashes relative to the source. Links, reparse points, and special files are not followed.

Each file is hashed as it is copied into object storage. Equal contents share an object even when their filenames differ. Deduplication works on whole files: changing part of a large file creates a new whole-file object.

Every capture reads the source files again. It does not trust an unchanged timestamp as proof of unchanged contents. Existing objects are checked before being reused. File bytes are streamed in 1 MiB blocks, while the manifest entries are assembled in memory; memory use therefore also depends on the number of paths.

The source file's metadata is checked before and after capture to detect changes during the read. This doesn't freeze the directory tree. A database with related files changing at different times can still produce an inconsistent backup. This version doesn't take filesystem snapshots or ask applications to pause writes.

Objects are published before snapshot entries are committed together in a SQLite transaction. The database uses write-ahead logging and full synchronization. Objects and the SQLite manifest are still separate persistence boundaries. A failed operation may leave an object that no committed snapshot references. Such an object is an orphan, not a completed snapshot. Statistics report orphans; the baseline does not delete them. Power-loss durability across every boundary still needs fault-injection work, especially because Python does not expose directory synchronization on Windows.

## Comparing snapshots

Diffs compare entries by relative path. New and missing paths produce `added` and `deleted` changes. A content or kind change produces `modified`; an otherwise unchanged entry with different recorded metadata produces `metadata`.

Diffs don't infer renames or a shared history between matching files.

## Verifying and restoring

Verification checks the catalog and reads referenced stored contents, comparing hashes and lengths with the manifests. Missing or corrupt objects are reported as issues. The CLI treats verification issues as a failed command. Unreferenced objects are counted by `stats` but are not covered by snapshot verification. Hash checking detects inconsistent contents; it does not authenticate a vault against deliberate replacement of both contents and manifests.

Restore checks the destination and manifest paths, verifies referenced objects, and stages the reconstructed tree in a sibling directory. It hashes the bytes again while copying them and only then publishes the destination. The destination must be new. This avoids merging a partial restore into existing user files.

Restore checks native path collisions and rejects Windows-reserved filenames on Windows. It restores modification times and ordinary permission bits where supported, but not setuid, setgid, sticky bits, ACLs, or extended attributes. The enclosing source directory's own metadata is not captured. Hard-linked files become separate restored files, and sparse-file allocation is not preserved.

This is a local tool for a vault controlled by the same user. It is not a security boundary against another process actively replacing filesystem paths during an operation. Keep the restore parent under your control while restoring: on POSIX, the final rename can replace an empty directory created between the last check and the rename. Untrusted imported vaults, cross-platform filename conflicts, and archive extraction rules need a separate hardening pass before portable imports are supported.

## Storage accounting

`logical_bytes` counts file bytes across snapshots, including repeated references. `stored_bytes` counts stored object bytes, so shared contents count once. Object counts and orphan counts describe content objects, not the total vault footprint. SQLite files, directories, and filesystem overhead are additional storage.

## Planned format changes

Resumable capture needs a durable job journal that can be reconciled with committed manifests. Catalog recovery needs independently readable snapshot manifests. Safe retention needs a transaction that chooses expired snapshots and a recoverable object-deletion phase. Chunk-level deduplication needs a versioned object format and migration tests.

Format changes need versioned migrations and compatibility fixtures so completed snapshots remain distinguishable from incomplete work. The [roadmap](roadmap.md) gives the planned order and acceptance checks.
