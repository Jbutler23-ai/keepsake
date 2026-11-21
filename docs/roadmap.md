# Roadmap

The baseline provides local capture, deduplication, comparison, verification, staged restore, storage statistics, and a retention preview. The remaining work is split into eight planned sessions. Their length will depend on the implementation and testing needed.

The target is a backup tool that can recover a known directory tree after an interruption, survive damage to its catalog, and retire old data without deleting contents that retained snapshots need.

## 1. Specify the storage contract and failure model

Document a versioned vault format, supported filename rules, timestamp precision, and durability boundaries. Build fixtures for empty directories, binary files, large files, repeated contents, and path collisions. Add a controlled subprocess harness that can stop a writer at named persistence boundaries.

Acceptance: format fixtures round-trip; unsupported paths fail with useful messages; interruption tests distinguish committed snapshots from incomplete captures. Record the operating system and filesystem used for each durability result.

## 2. Resume interrupted captures

Add a durable job journal and a CLI for inspecting and resuming interrupted captures. A resumed job must revalidate source files before reusing work. Define what happens when a source file disappears or changes between attempts, and replace manual stale-lock recovery with an explicit recovery workflow.

Acceptance: interruption after each journal transition can be resumed or safely abandoned; changed source files are reread; no incomplete job appears in the completed snapshot list; repeated recovery does not duplicate snapshots.

## 3. Recover a damaged catalog

Write independently readable snapshot manifests alongside the catalog. Add a repair command that rebuilds a fresh catalog from validated manifests without overwriting the damaged one. Report missing objects separately from catalog damage and preserve evidence needed to understand failures.

Acceptance: deleting a test catalog and rebuilding it recovers every committed snapshot with identical entries; a damaged manifest is identified without hiding healthy snapshots; repaired snapshots pass full content verification.

## 4. Apply retention and collect unused objects

Add a command to apply the keep-last preview. Add recoverable garbage collection, with a quarantine stage before permanent deletion. Coordinate capture, restore, retention, and collection so objects in use cannot be deleted.

Acceptance: overlapping snapshots retain shared objects; a dry run changes nothing; interrupted collection can resume; no retained snapshot loses an object; concurrent capture and retention are serialized or rejected clearly.

## 5. Reduce the cost of large changing files

Add bounded-memory chunking with a versioned object format. Measure whether chunk-level reuse is worth its index and metadata overhead. Support migration from whole-file objects without immediately rewriting every existing backup.

Acceptance: migrated and unmigrated snapshots restore identically; changing a small region of a large fixture reuses unaffected chunks; repeated content and incompressible content both have recorded storage results; memory remains bounded as file size grows.

## 6. Move portable backups between machines

Define a portable archive containing versioned manifests and referenced objects. Add streaming export and import, format-version checks, and predictable handling of names that the destination filesystem cannot represent. Use standard-library archive and compression modules so the workflow stays offline. Archives contain unencrypted data; encryption and key management are outside the project scope.

Acceptance: archives import on a second supported machine and restore identical bytes and directory structure; interrupted exports, truncated archives, unsupported versions, invalid paths, duplicate entries, and filename collisions fail without publishing a partial import. Repeated imports must reuse existing objects, and imports must remain within documented size limits.

## 7. Schedule and inspect local backup jobs

Add a local scheduler that runs configured capture and verification jobs without a cloud service. Record outcomes in a queryable job history. Define missed-run behavior, overlapping-run handling, and cancellation. Keep the scheduling policy separate from any optional operating-system integration.

Acceptance: a controlled clock produces predictable schedules; restart does not repeat a completed job; missed runs follow the documented policy; overlapping runs cannot corrupt the vault; a failed verification remains visible in job history and the CLI exit status.

## 8. Run recovery drills and establish release limits

Combine capture, interruption, recovery, retention, export, and restore in end-to-end recovery drills. Publish the exact dataset generator, commands, machine details, and results. Check the documented workflow on Linux and Windows, then set supported limits from measurements.

Acceptance: every retained snapshot in the deterministic corpus restores to the reference file hashes and directory structure after the documented interruption scenarios. The full suite passes without network access. Release notes distinguish tested guarantees from known filesystem limitations.

## Performance targets to measure

These targets haven't been measured yet.

| Workload | Target | Measurement |
| --- | --- | --- |
| 50,000 mixed files totaling 5 GiB | Complete capture and restore on a CPU-only machine | Elapsed time, peak RSS, object count, logical and stored bytes |
| One 4 GiB file | Peak process RSS below 256 MiB during capture and restore | Platform-specific process memory sampling, with sampler documented |
| Second capture of an unchanged corpus | No duplicate stored content | Compare referenced digests and stored object bytes before and after |
| One-region edit after chunking is added | Reuse unaffected chunks | Stored-byte increase and retained chunk identities |
| Recovery drill corpus | Exact content recovery for every retained snapshot | Independent SHA-256 manifest and directory list |

Throughput limits will be set after collecting a repeatable baseline. Results need to include disk type, filesystem, cache state, and antivirus activity because each can affect timing.
