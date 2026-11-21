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

The [roadmap](roadmap.md) includes additional acceptance checks for resumability, catalog reconstruction, applied retention, garbage collection, chunking, portable archives, and scheduling. None of those features is part of the baseline contract.

A durable-backup release needs controlled interruption tests at every persistence boundary and recovery drills against an independent reference manifest. A successful clean shutdown and restore do not prove recovery after sudden power loss. Real filesystem behavior must be tested and recorded before claiming that guarantee.

Performance targets remain unmeasured. Results need to include the corpus, hardware, operating system, filesystem, command, and memory measurement method. The small default benchmark doesn't cover the 50,000-file target.
