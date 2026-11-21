from dataclasses import dataclass, field


@dataclass(frozen=True)
class Entry:
    path: str
    kind: str
    digest: str | None
    size: int
    mode: int
    mtime_ns: int


@dataclass(frozen=True)
class Snapshot:
    id: str
    created_at: str
    source: str
    label: str
    file_count: int
    total_bytes: int
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Change:
    path: str
    status: str


@dataclass(frozen=True)
class Verification:
    checked_objects: int
    checked_bytes: int
    issues: list[str]


@dataclass(frozen=True)
class RestoreResult:
    destination: str
    files: int
    total_bytes: int


@dataclass(frozen=True)
class Stats:
    snapshots: int
    files: int
    logical_bytes: int
    unique_objects: int
    stored_bytes: int
    orphan_objects: int
