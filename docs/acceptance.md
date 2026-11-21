# Acceptance checks

Run the baseline suite from the repository root:

```sh
python -m unittest discover -s tests -v
```

The table below defines the expected baseline behavior. Future release checks are listed separately.

## Baseline

| Area | Expected result |
| --- | --- |
| Initialization | A new vault can be opened again; an unrelated directory is not mistaken for a valid vault. |
| Capture | Regular files and empty directories appear in the manifest, with content hashes that match their bytes. |
| Deduplication | Identical contents under different names or snapshots reuse a stored object. |
| Exclusions | A matching file is omitted; a matching directory is pruned; no paths are excluded unless requested. |
| Unsupported files | Symbolic links, reparse points, and special files are skipped and reported instead of followed. |
| Changes | Added, deleted, content-modified, and metadata-only changes are distinguished. |
| Restore | A snapshot recreates its bytes and empty directories in a new destination. |
| Existing destination | Restore refuses to overwrite an existing file or directory. |
| Corruption | Altered or missing objects cause verification to fail and prevent a completed restore. |
| Path safety | Source/vault nesting and unsafe restore destinations are rejected. |
| Retention preview | The selected keep/remove sets match the rule and leave the vault unchanged. |
| CLI | Structured output is valid JSON; invalid requests and failed verification produce a nonzero exit status. |

Recovery tests compare restored bytes with independent expected contents. A successful exit status alone doesn't establish that a restore is correct.

## Manual smoke test

1. Initialize a vault outside the example source folder.
2. Capture the sample folder and note the resulting snapshot ID.
3. Run verification and inspect the reported object count.
4. Change a file in a temporary copy of the sample folder and capture that copy.
5. Compare the two IDs and check the changed path.
6. Restore the first snapshot into a new folder and compare its files with the original sample.
7. Preview retention and confirm that both snapshots still appear in `list`.

Use temporary copies for deliberate corruption tests. The ordinary test suite manages its own temporary directories.

## Repeatable recovery benchmark

```sh
python scripts/benchmark.py --files 256 --bytes-per-file 4096 --unique-files 64 --json
```

The script generates synthetic bytes, records independent hashes, captures the corpus twice, checks that the unchanged capture adds no object bytes, verifies the vault, and restores the first snapshot. It compares the restored file set, empty directories, and each file's hash with the generated reference. All files live in a temporary directory that is removed afterward.

Increase the corpus parameters to check larger workloads. Allow free disk space for both the source and restored files, plus the vault. An unchanged second capture still reads every source file. Elapsed times cover the work stated in each result field. The script doesn't measure peak resident memory or simulate power loss.

## Future release gates

Everything below is planned acceptance work for the [12-stage roadmap](roadmap.md), not an executed test result. The [target system design](system-design.md) defines the failure model and acknowledgement policy. Existing baseline tests do not establish consensus, replication, resumability, repair, or safe deletion.

### Independent correctness oracles

| Invariant | Required evidence |
| --- | --- |
| Exact recovery | A separate reader and independently generated reference manifest compare the complete file set, directory set, sizes, and file hashes. A successful exit alone is insufficient. |
| Committed metadata consistency | Record client requests/responses, stable operation IDs, terms, applied indexes, and committed entries. An offline checker must find one legal committed history consistent with completed operations and branch rules. |
| Honest acknowledgement | Every reported success maps to a committed publication and the required synchronized, verified chunk/manifest receipts. Missing quorum or copies cannot pass. Lost replies may leave an unknown outcome; retries must resolve the same operation without duplication. |
| Concurrent branches | Clients capture from the same parent during reordered/partitioned delivery. After reconciliation, both children remain explicit and independently restorable unless an explicit later retention action removes one. |
| Protected cleanup | Before every quarantine/purge transition, compare the candidate set with an independently computed closure of retained snapshots, pins, journals, exports, and repair work. The intersection must be empty. |
| Resume validation | Replace, edit, truncate, or remove source/partial data between attempts. Reused work must be revalidated, and incomplete bytes cannot receive durable receipts. |
| Corruption handling | Alter known bytes or lengths. A verified healthy copy permits repair; the replacement must match the independent reference. Unreachable copies remain unavailable. Established loss/corruption of every copy produces an explicit unrecoverable result. |
| Archive recovery | Rebuild in fresh directories from validated versioned archives. Match bytes and directories, preserve damaged evidence, and report the exact recovery checkpoint without inventing later history. |

### Fault matrix

The harness must run five separate node processes and save the exact seed, event schedule, fault point, node identities, quota settings, and results. A queued-message mode supplies reproducible ordering; process-level loopback runs check serialization and persistence boundaries too.

| Fault family | Minimum planned cases | Required outcome |
| --- | --- | --- |
| Process stops | Each of five single-node stops and all ten two-node combinations, at named persistence boundaries | A healthy majority and available data preserve acknowledged history. Restart reconciles committed versus staged work without duplicate publication. |
| Partitions and message faults | Every 3/2 split; a 2/2/1 split; delayed, lost, duplicated, and reordered messages; healing after a new leader | Minority sides cannot publish or claim fresh metadata. Healed replicas converge on committed history while preserving explicit snapshot branches. |
| Lost responses | Drop replies before/after data receipt, metadata commit, and client acknowledgement; retry after log compaction and retention | The caller-known ID and request digest resolve to one result. Changed requests under the same ID fail; retired snapshots are not republished. Unknown outcomes remain unknown until quorum-confirmed lookup. |
| Per-node storage faults | Short/truncated writes, injected I/O failure, quota exhaustion, and restart at each journal/log/object boundary | No premature durable receipt, invented commit, or published partial restore. Evidence remains inspectable. |
| Stored corruption | One bad replica with healthy alternatives; all known copies corrupt; corrupt manifest; corrupt metadata log/checkpoint/election state | Detect the affected objects, repair only from verified data, fence unsafe voters, and distinguish unavailable data from established permanent loss. |
| Overlapping maintenance | Capture/transfer, restore/export pins, repair, rebalancing, tombstones, quarantine, and purge under reordered delivery | Reachability and pin checks never permit deletion of live/protected content. Lost quorum freezes new destructive work. |
| Offline return and replacement | Offline node during cleanup; fenced replacement; delayed commands from old incarnation; stale tombstoned references | Cleanup blocks until a safe barrier or explicit replacement. Old incarnations and references do not resurrect retired state. |
| Archive/catalog damage | Missing catalog, truncated archive, unsupported format, duplicate entries, invalid paths, and older recovery checkpoint | Rebuild only validated state in new directories; preserve damage and make recovery-point limits explicit. |

Each named fault point must be tested before and after the corresponding persistence transition. At least 100 seeded mixed schedules are a proposed regression target, not a claim of coverage today. Small deterministic cases must remain available when a larger randomized schedule finds a failure.

### Corpus, quotas, and measurements

Begin with a fast synthetic corpus of 200 files and 64 MiB of logical content, including empty directories, duplicate bytes, large changing files, and conflicting client captures. Run it at replica counts one, three, and five; acknowledgement checks must use the chosen policy rather than assuming every setting has the default durability.

Configure per-node stored-byte quotas, transfer slots, bytes per second, queued messages, and event/operation budgets. Deliberately exhaust each quota. Log both configured and observed use. Safety checks must still pass when work stops for backpressure; never skip a check because the budget is small.

The unmeasured larger target is 50,000 files totaling 5 GiB logical data with three replicas. Report stored/unique/replicated bytes, capture/restore time, repaired bytes, rebalance traffic, journal size, backlog, and peak process memory using a documented sampler. Include operating system, Python version, filesystem, disk/cache conditions, and enough free space for sources, copies, staging, quarantine, and restores. The default baseline benchmark covers none of these distributed targets.

Five directories on one desktop do not simulate independent hardware failure perfectly. Process kills and per-node disk faults are not power-loss tests. A durable-backup release must separately qualify actual synchronization behavior on supported filesystems before claiming stronger crash or power-loss guarantees.
