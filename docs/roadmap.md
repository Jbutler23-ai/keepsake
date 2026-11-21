# Roadmap

The working baseline is a single local vault with whole-file SHA-256 objects, SQLite, capture, diff, verify, restore, statistics, and retention preview. The target is an offline replicated backup and recovery system. These 12 stages are planned work, grouped into three phases. A stage may require several coding sessions.

The first milestone is an independently readable versioned manifest, followed by interrupted-capture recovery. A deterministic five-node harness precedes consensus qualification. The [system design](system-design.md) defines the proposed guarantees.

## Phase 1: storage, resumability, and coordination

| Stage | Work | Acceptance gate |
| --- | --- | --- |
| 1. Versioned manifests and chunk storage | Define chunk identities, ordered file recipes, whole-file hashes, directory entries, and migration from whole-file objects. | An independent reader restores exact bytes/directories without the original catalog. Old/new fixtures agree. Invalid manifests fail before publication. |
| 2. Resumable capture | Add durable job journals, bounded-memory chunking, source revalidation, and explicit resume/abandon states. | Kill capture at every persistence boundary. Resume or abandon repeatedly without duplicate commits or silent reuse of changed source content. |
| 3. Five-node fault harness | Run five Python processes with separate directories, loopback transport, deterministic message faults, process stops, and per-node disk quotas/errors. | A saved fault schedule reproduces protocol decisions. Node files remain isolated. No external service or special hardware is needed. |
| 4. Metadata consensus and fencing | Add durable election/log state, committed-prefix recovery, quorum-confirmed reads, and configuration/worker epochs. | Histories admit one committed order. A minority cannot commit or claim freshness. Stale leaders/workers cannot publish or delete. Corrupt metadata stops voting pending safe recovery. |

## Phase 2: publication, branches, and replica movement

| Stage | Work | Acceptance gate |
| --- | --- | --- |
| 5. Placement and acknowledgement | Implement configurable replica placement, verified durable receipts, majority metadata publication, and retryable operation IDs. Default to three data copies. | Every success has the required data receipts and metadata commit. Lost responses resolve to the original operation. Insufficient replicas or quorum never produces fake success. |
| 6. Branches and protected restore | Preserve concurrent capture branches, explicit merge parents, fresh queries, durable pins, and restricted local-only restore. | Concurrent children remain discoverable/restorable. No timestamp chooses a silent winner. Restore protects its manifest closure or returns unavailable. |
| 7. Transfer, anti-entropy, and repair | Add resumable chunk transfer, checkpoint validation, inventory comparison, content scrubbing, quarantine, and verified repair. | Interrupted transfers never earn premature receipts. A corrupt replica repairs from a healthy copy. Unreachable copies remain unavailable; established loss of all copies is reported unrecoverable. |
| 8. Rebalancing and replacement | Add placement changes, rate limits, byte/operation quotas, and fenced node replacement with safe membership transitions. | Verified replacements precede removal of old copies. Protected reads remain safe. Retired incarnations cannot replay stale receipts, branches, or tombstones. Quotas produce explicit backpressure/failure. |

## Phase 3: cleanup, disaster recovery, and qualification

| Stage | Work | Acceptance gate |
| --- | --- | --- |
| 9. Retention and garbage collection | Apply committed tombstones and deletion barriers covering all current nodes, pins, pending jobs, and reachable chunks. Quarantine before a fresh purge check. | No cleanup step deletes reachable/protected content. Offline members block the barrier. Interrupted passes resume/cancel safely. Stale messages cannot resurrect deleted references. |
| 10. Archives and disaster rebuild | Export pinned manifests, content, and recovery checkpoints. Rebuild a fresh catalog or new cluster identity without overwriting damaged evidence. | An independent reader restores exact contents. Truncated/incompatible archives fail. Recovered bytes and proven commit history remain distinct; older archives cannot fabricate later commits. |
| 11. Local diagnosis and recovery drills | Expose branch/commit history, freshness, node health, replica deficits, jobs, pins, and stalled barriers through a local diagnostic interface. | Each injected fault has a specific visible state and repeatable recovery action. Committed, staged, outcome-unknown, unavailable, degraded, and unrecoverable states remain distinct. |
| 12. Integrated qualification | Combine consensus, capture, transfer, repair, rebalance, retention, restore, and archive fault schedules. Run Windows/Linux format and workflow checks. | Independent manifests match recoverable acknowledged snapshots; metadata histories pass consistency checks; measurements identify corpus, fault schedule, hardware, filesystem, and quotas. |

## Scope and success

The hard part is coordination across persistence boundaries: publication must wait for durable data, conflict handling must preserve branches, repair must reject corrupt sources, and cleanup must not outrun offline nodes or active restores. Retries alone do not establish those guarantees.

The reference setup has five local node processes on one supported desktop. It tests software behavior under simulated process, message, and per-node storage faults. It does not provide independent physical disks or prove resistance to whole-machine failure.

The larger workload target is 50,000 files totaling 5 GiB of logical data with the default three-copy policy. That workload and its fault runs are unmeasured. Space is also needed for sources, restore destinations, temporary transfers, and quarantine. Throughput and memory limits will be set from measured runs, not inferred from the baseline microbenchmark.

Byzantine behavior, authentication, encryption, internet/cloud services, and real device control are outside scope. The [acceptance plan](acceptance.md) defines the future fault matrix and measurable gates.
