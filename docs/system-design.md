# Target system design

This is proposed work. The [baseline](architecture.md) remains one local vault with whole-file objects and a SQLite catalog.

## Deployment and failure boundaries

Five Python processes will run on one supported desktop, each with a separate node directory. Transport will use loopback or deterministic queued messages. No internet, external account, cloud service, GPU, Docker, sound, or Windows-only dependency is required.

The fault model covers process stop/restart, partitions, delayed/lost/duplicated/reordered messages, partial writes, injected I/O errors, exhausted node quotas, and detectable stored-data corruption. Processes are cooperative, not Byzantine. A synchronized write is assumed to survive a process crash under the documented filesystem contract. False durability reports from hardware, undetected hash collisions, malicious modification, authentication, and encryption are outside scope.

Separate directories are logical failure domains, not independent physical disks. These tests cannot establish survival of whole-desktop loss or sudden power failure. Filesystem and device durability must be qualified separately.

## Data and consensus

Immutable chunks will be identified by SHA-256 and length. Versioned file recipes will record chunk order, total length, and a whole-file hash. Independently readable snapshot manifests will preserve paths, directories, supported metadata, exclusions, parents, and capture identity. Existing whole-file snapshots must remain readable through explicit migration.

A replicated metadata log will order publication, branch heads, placement policies, receipts, pins, tombstones, membership changes, and maintenance jobs. Local SQLite indexes may serve applied state; they do not replace consensus.

The normal configuration has five voters and a majority of three. The protocol will use durable election state, correct commit rules, quorum-confirmed fresh reads, and safe membership transitions. The reference is Ongaro and Ousterhout's [Raft extended paper](https://raft.github.io/raft.pdf). Term, configuration epoch, and node incarnation remain separate identities. Old leaders and stale maintenance workers cannot authorize publication or destructive work.

A corrupt metadata log or election record stops that node from voting. Recovery requires healthy committed history or controlled replacement, not a fresh empty voter under an old identity. Without a healthy metadata majority, matching loose manifests cannot establish an authoritative new history.

## Publication and durability acknowledgement

Default placement requires three replicas on distinct logical nodes. Replica count may be configured from one to five, but each operation records its required policy; shortages never silently lower it.

1. Capture into journaled chunks and a complete manifest. Resumed capture revalidates source content. This still does not create an application-consistent filesystem snapshot.
2. Transfer each referenced chunk and the manifest to the required nodes. Receivers verify length/hash and synchronize both stored bytes and receipt records. Partial transfers receive no durable receipt.
3. Commit publication, parents, policy, and receipts through the metadata majority.
4. Acknowledge only after the required data receipts and metadata commit exist. Return snapshot ID, stable operation ID, configuration epoch, and committed log position.

The default acknowledgement therefore means three verified durable data copies plus a majority metadata commit at that time. Later corruption can invalidate copies and must change the reported health state.

The client persists `(client_id, job_id)` and a canonical capture-request digest before any mutation. Retries reuse that identity; a different request under the same ID is rejected. A response lost after commit produces an uncertain client outcome, not another snapshot. A quorum-confirmed lookup resolves it. An interrupted operation may already be committed; staging alone never proves success.

Log compaction preserves operation outcomes and deduplication markers. If retention has tombstoned the snapshot, a retry returns its retired outcome rather than publishing it again. Capturing a new version requires a new job ID.

## Branches and availability

Concurrent captures from one parent create separate children and visible branch heads. Neither timestamp nor commit order silently overwrites another client's version. A merge is an explicit new snapshot naming its parents and chosen contents. Tombstoned IDs cannot be resurrected by stale retries. Historical parent IDs are distinct from content-retention roots.

| Condition | Behavior |
| --- | --- |
| Healthy majority and enough verified data copies | Permit publication, fresh queries, protected restores, and coordinated maintenance. |
| Metadata majority lost | New commits, fresh head queries, new global pins, retention, and destructive maintenance return unavailable. Local staging remains uncommitted. |
| Exact snapshot completely present on one node | An explicitly requested local-only restore may use a previously committed manifest and verified local chunks after creating a durable local pin. Report the last applied metadata index and possible staleness. |
| Local-only restore lacks required data | Return unavailable; do not assemble unprotected remote reads or publish a partial destination. |
| Commit response cannot be resolved | Report outcome-unknown/unavailable with the stable operation ID. |

Normal restores obtain committed, non-expiring pins before reading. Pins require explicit completion, cancellation, or inspected abandoned-job recovery. A timer alone cannot remove protection. Completed restores must match independent expected bytes and directories.

## Transfer, repair, and rebalancing

Capture and transfer checkpoints record validated progress, not just offsets. Resume rechecks source content, receiver incarnation, and partial data. Changed or truncated work is reread. Promotion and operation retries are idempotent.

Anti-entropy compares expected inventories with node state and periodically reads content. A matching filename is not evidence of healthy bytes. Corrupt copies are quarantined. Repair verifies a healthy source and synchronizes its replacement before advertising a receipt. An unreachable copy means unavailable, not necessarily lost. Unrecoverable is reserved for established loss/corruption of every known copy, with affected chunks and snapshots identified.

Placement uses configured eligible nodes and quotas. Rebalancing creates verified replacements before retiring surplus copies, preserving the required healthy copies and read protections. Transfer slots, byte quotas, and rate limits produce backpressure rather than skipped checks.

An offline node is not automatically retired. Explicit replacement uses safe membership transitions where required and a new incarnation. Old receipts and worker commands are fenced. Returning retired directories must be inspected/reseeded; they cannot replay old branch state. The reference harness can use a stopped node's process slot for a replacement learner.

## Retention and deletion barriers

Retention commits tombstones for specific snapshots and branch references. Live roots include retained snapshots, global restore/export pins, durable local-only restore pins, and unfinished capture, transfer, repair, and rebalancing jobs. Tombstones and configuration epochs survive log compaction.

The first collector will use a conservative barrier:

1. Commit a maintenance epoch that closes reference mutations and staged-object promotion. Existing pinned reads may continue.
2. Every current, nonretired node applies it, drains relevant mutations, records local roots, and closes creation of new local pins. An offline member blocks completion. Combined committed and local roots define the mark set.
3. Quarantine only unmarked objects, with durable per-object progress. Every participant must confirm destructive work has stopped before reference creation reopens. Lost quorum freezes new destructive steps; stale fences are rejected.
4. Purge only under a later fresh barrier and root check. Newly reachable/protected objects leave the deletion set. A grace interval alone cannot establish safety.

An unreleased pin or offline node can intentionally block cleanup. Retirement follows the fenced replacement procedure, not a timeout shortcut. Global pins survive retirement of their creator. A retired node's local-only restore may use existing local files, but cannot claim remote access; local active pins also block deleting those files during reseeding.

Interrupted collection resumes or cancels from committed epochs and journals. Reference creation stays closed until safe release. No independent orphan scan may bypass this protocol. More concurrent collection is future work, conditional on preserving the same invariant: never delete reachable or protected content.

## Disaster recovery and diagnosis

Portable archives will contain versioned manifests, complete chunk closure, and the committed recovery checkpoint exported. Exports remain pinned. A standalone reader validates format, lengths, hashes, paths, and duplicate entries before publishing a restore or import.

Catalog rebuild writes a new catalog and preserves damaged evidence. Recovering file bytes is separate from proving the latest branch history. An older archive names its recovery point and cannot claim later commits. Rebuilding authoritative metadata into a new cluster identity prevents old messages from entering the replacement system.

The local diagnostic interface will expose freshness/commit state, branch history, replica deficits, corruption, unfinished jobs, pins, tombstones, and stalled barriers. Recovery drills will distinguish committed, staged, outcome-unknown, unavailable, degraded, and unrecoverable results. The [acceptance plan](acceptance.md) defines the checks; none is established by today's tests.
