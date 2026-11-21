"""Snapshot storage and recovery, with no services or third-party packages."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sqlite3
import stat
import tempfile
from typing import Iterator
import uuid

from .errors import KeepsakeError
from .models import Change, Entry, RestoreResult, Snapshot, Stats, Verification


SCHEMA_VERSION = 1
CHUNK_SIZE = 1024 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SCHEMA = """
CREATE TABLE snapshots (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    label TEXT NOT NULL,
    skipped_json TEXT NOT NULL
);
CREATE TABLE entries (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('file', 'dir')),
    digest TEXT,
    size INTEGER NOT NULL CHECK(size >= 0),
    mode INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    PRIMARY KEY(snapshot_id, path),
    CHECK((kind = 'file' AND digest IS NOT NULL) OR
          (kind = 'dir' AND digest IS NULL AND size = 0))
);
CREATE INDEX entries_digest ON entries(digest);
PRAGMA user_version = 1;
"""


def _absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _link_like(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _guard_path(path: Path) -> None:
    """Reject links in existing components before following a filesystem path."""
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if _link_like(info):
            raise KeepsakeError(f"Symlink or reparse point is not allowed here: {part}")


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _validate_relative(path: str) -> None:
    if not isinstance(path, str) or not path or "\\" in path or "\0" in path:
        raise KeepsakeError(f"Invalid manifest path: {path!r}")
    if PurePosixPath(path).is_absolute() or PureWindowsPath(path).drive:
        raise KeepsakeError(f"Absolute manifest path is not allowed: {path!r}")
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise KeepsakeError(f"Unsafe manifest path: {path!r}")


def _sync_directory(path: Path) -> None:
    # Windows does not expose directory fsync through the standard library.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _fingerprint(info: os.stat_result) -> tuple[int, ...]:
    fields = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    # Some Windows Python versions report creation time from stat and change
    # time from fstat under the same ctime field. They cannot be compared.
    return fields if os.name == "nt" else (*fields, info.st_ctime_ns)


def _open_regular(path: Path):
    before = path.lstat()
    if _link_like(before) or not stat.S_ISREG(before.st_mode):
        raise KeepsakeError(f"Expected a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    stream = os.fdopen(descriptor, "rb")
    opened = os.fstat(stream.fileno())
    if _fingerprint(before) != _fingerprint(opened):
        stream.close()
        raise KeepsakeError(f"File changed while opening: {path}")
    return stream


class Repository:
    """A single local vault. Snapshot objects are immutable after publication."""

    def __init__(self, path: Path | str):
        self.path = _absolute(path)
        _guard_path(self.path)
        self.database = self.path / "manifest.sqlite3"
        for child in (self.database, self.path / "objects", self.path / "tmp"):
            _guard_path(child)
        if not self.database.is_file():
            raise KeepsakeError(f"Not an initialized vault: {self.path}")
        if not (self.path / "objects").is_dir() or not (self.path / "tmp").is_dir():
            raise KeepsakeError("Vault is missing its objects or tmp directory")
        with self._connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise KeepsakeError(f"Unsupported vault version {version}; expected {SCHEMA_VERSION}")
            db.execute("SELECT id, created_at, source, label, skipped_json FROM snapshots LIMIT 0")
            db.execute("SELECT snapshot_id, path, kind, digest, size, mode, mtime_ns FROM entries LIMIT 0")

    @classmethod
    def initialize(cls, path: Path | str) -> Repository:
        root = _absolute(path)
        _guard_path(root)
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise KeepsakeError(f"Initialize needs a new or empty directory: {root}")
        root.mkdir(parents=True, exist_ok=True)
        (root / "objects").mkdir()
        (root / "tmp").mkdir()
        db = sqlite3.connect(root / "manifest.sqlite3")
        try:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA synchronous = FULL")
            db.executescript(_SCHEMA)
            db.commit()
        finally:
            db.close()
        _sync_directory(root)
        return cls(root)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        _guard_path(self.database)
        db = None
        try:
            db = sqlite3.connect(self.database.as_uri() + "?mode=rw", uri=True, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA synchronous = FULL")
            yield db
        except sqlite3.Error as exc:
            raise KeepsakeError(f"Cannot read or update the vault database: {exc}") from exc
        finally:
            if db is not None:
                db.close()

    @contextmanager
    def _writer(self) -> Iterator[None]:
        lock = self.path / "write.lock"
        _guard_path(lock)
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise KeepsakeError(
                f"Vault is locked: {lock}. If a previous writer crashed, confirm it has stopped before removing the lock."
            ) from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            yield
        finally:
            lock.unlink(missing_ok=True)

    def _object_path(self, digest: str) -> Path:
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise KeepsakeError(f"Invalid object digest: {digest!r}")
        path = self.path / "objects" / digest[:2] / digest[2:]
        _guard_path(path)
        return path

    def _hash_object(self, digest: str) -> int:
        path = self._object_path(digest)
        hasher = hashlib.sha256()
        length = 0
        with _open_regular(path) as stream:
            while block := stream.read(CHUNK_SIZE):
                hasher.update(block)
                length += len(block)
        if hasher.hexdigest() != digest:
            raise KeepsakeError(f"Corrupt object: {digest}")
        return length

    def _store_file(self, source: Path, relative: str) -> Entry:
        _guard_path(source)
        before = source.lstat()
        temporary = None
        try:
            with _open_regular(source) as incoming, tempfile.NamedTemporaryFile(
                dir=self.path / "tmp", prefix="object-", delete=False
            ) as outgoing:
                temporary = Path(outgoing.name)
                digest = hashlib.sha256()
                size = 0
                while block := incoming.read(CHUNK_SIZE):
                    outgoing.write(block)
                    digest.update(block)
                    size += len(block)
                outgoing.flush()
                os.fsync(outgoing.fileno())
                after_open = os.fstat(incoming.fileno())
            after = source.lstat()
            if _fingerprint(before) != _fingerprint(after_open) or _fingerprint(before) != _fingerprint(after):
                raise KeepsakeError(f"File changed during snapshot; retry after it stops changing: {relative}")
            if size != before.st_size:
                raise KeepsakeError(f"File size changed during snapshot: {relative}")
            hexdigest = digest.hexdigest()
            target = self._object_path(hexdigest)
            target.parent.mkdir(exist_ok=True)
            if target.exists():
                if self._hash_object(hexdigest) != size:
                    raise KeepsakeError(f"Object size mismatch: {hexdigest}")
            else:
                os.replace(temporary, target)
                temporary = None
                _sync_directory(target.parent)
                _sync_directory(target.parent.parent)
            return Entry(relative, "file", hexdigest, size, stat.S_IMODE(before.st_mode), before.st_mtime_ns)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def snapshot(self, source: Path | str, label: str = "", excludes: list[str] | None = None) -> Snapshot:
        source_path = _absolute(source)
        _guard_path(source_path)
        if not source_path.is_dir():
            raise KeepsakeError(f"Snapshot source must be a directory: {source_path}")
        if _overlap(source_path, self.path):
            raise KeepsakeError("Source and vault must be separate directories; neither may contain the other")
        patterns = excludes or []
        if not isinstance(label, str) or any(not isinstance(pattern, str) or not pattern for pattern in patterns):
            raise KeepsakeError("Label and exclude patterns must be strings; exclude patterns cannot be empty")
        entries: list[Entry] = []
        skipped: list[str] = []

        def walk(directory: Path) -> None:
            _guard_path(directory)
            with os.scandir(directory) as scan:
                children = sorted(scan, key=lambda child: child.name)
            for child in children:
                path = Path(child.path)
                relative = path.relative_to(source_path).as_posix()
                _validate_relative(relative)
                if any(fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(child.name, pattern) for pattern in patterns):
                    skipped.append(f"{relative} (excluded)")
                    continue
                info = child.stat(follow_symlinks=False)
                if _link_like(info):
                    skipped.append(f"{relative} (symlink or reparse point)")
                elif stat.S_ISDIR(info.st_mode):
                    entries.append(Entry(relative, "dir", None, 0, stat.S_IMODE(info.st_mode), info.st_mtime_ns))
                    walk(path)
                elif stat.S_ISREG(info.st_mode):
                    entries.append(self._store_file(path, relative))
                else:
                    skipped.append(f"{relative} (special file)")

        snapshot_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self._writer():
            walk(source_path)
            with self._connection() as db:
                with db:
                    db.execute(
                        "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
                        (snapshot_id, created_at, str(source_path), label, json.dumps(skipped)),
                    )
                    db.executemany(
                        "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(snapshot_id, e.path, e.kind, e.digest, e.size, e.mode, e.mtime_ns) for e in entries],
                    )
        return Snapshot(snapshot_id, created_at, str(source_path), label,
                        sum(e.kind == "file" for e in entries), sum(e.size for e in entries), skipped)

    def list_snapshots(self) -> list[Snapshot]:
        with self._connection() as db:
            rows = db.execute("""
                SELECT s.*, SUM(CASE WHEN e.kind = 'file' THEN 1 ELSE 0 END) AS file_count,
                       COALESCE(SUM(e.size), 0) AS total_bytes
                FROM snapshots s LEFT JOIN entries e ON s.id = e.snapshot_id
                GROUP BY s.id ORDER BY s.created_at DESC, s.id DESC
            """).fetchall()
        result = []
        for row in rows:
            try:
                skipped = json.loads(row["skipped_json"])
                if not isinstance(skipped, list) or not all(isinstance(item, str) for item in skipped):
                    raise ValueError("Invalid skipped paths")
            except (ValueError, TypeError) as exc:
                raise KeepsakeError(f"Invalid snapshot metadata: {row['id']}") from exc
            result.append(Snapshot(row["id"], row["created_at"], row["source"], row["label"],
                                   row["file_count"], row["total_bytes"], skipped))
        return result

    def entries(self, snapshot_id: str) -> list[Entry]:
        with self._connection() as db:
            if not db.execute("SELECT 1 FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone():
                raise KeepsakeError(f"Unknown snapshot: {snapshot_id}")
            rows = db.execute(
                "SELECT path, kind, digest, size, mode, mtime_ns FROM entries WHERE snapshot_id = ? ORDER BY path",
                (snapshot_id,),
            ).fetchall()
        entries = [Entry(**dict(row)) for row in rows]
        by_path = {}
        for entry in entries:
            _validate_relative(entry.path)
            if entry.kind not in ("file", "dir") or not isinstance(entry.size, int) or entry.size < 0:
                raise KeepsakeError(f"Invalid manifest entry: {entry.path}")
            if not isinstance(entry.mode, int) or not 0 <= entry.mode <= 0o7777 or not isinstance(entry.mtime_ns, int):
                raise KeepsakeError(f"Invalid file metadata: {entry.path}")
            if entry.kind == "file":
                self._object_path(entry.digest)
            elif entry.digest is not None or entry.size != 0:
                raise KeepsakeError(f"Invalid directory entry: {entry.path}")
            by_path[entry.path] = entry
        for entry in entries:
            for parent in PurePosixPath(entry.path).parents:
                if str(parent) == ".":
                    continue
                if str(parent) not in by_path or by_path[str(parent)].kind != "dir":
                    raise KeepsakeError(f"Missing directory or file used as a parent: {entry.path}")
        return entries

    def diff(self, before: str, after: str) -> list[Change]:
        old = {entry.path: entry for entry in self.entries(before)}
        new = {entry.path: entry for entry in self.entries(after)}
        changes = []
        for path in sorted(old.keys() | new.keys()):
            if path not in old:
                changes.append(Change(path, "added"))
            elif path not in new:
                changes.append(Change(path, "deleted"))
            elif (old[path].kind, old[path].digest, old[path].size) != (new[path].kind, new[path].digest, new[path].size):
                changes.append(Change(path, "modified"))
            elif (old[path].mode, old[path].mtime_ns) != (new[path].mode, new[path].mtime_ns):
                changes.append(Change(path, "metadata"))
        return changes

    def verify(self, snapshot_id: str | None = None) -> Verification:
        issues = []
        sizes: dict[str, set[int]] = {}
        checked = 0
        checked_bytes = 0
        with self._connection() as db:
            checks = db.execute("PRAGMA integrity_check").fetchall()
            issues.extend(f"Database: {row[0]}" for row in checks if row[0] != "ok")
            issues.extend(f"Foreign key: {tuple(row)}" for row in db.execute("PRAGMA foreign_key_check"))
        ids = [snapshot_id] if snapshot_id is not None else [item.id for item in self.list_snapshots()]
        for identifier in ids:
            try:
                entries = self.entries(identifier)
            except KeepsakeError as exc:
                if snapshot_id is not None and str(exc).startswith("Unknown snapshot:"):
                    raise
                issues.append(f"Snapshot {identifier}: {exc}")
                continue
            for entry in entries:
                if entry.kind == "file":
                    sizes.setdefault(entry.digest, set()).add(entry.size)
        for digest, expected_sizes in sorted(sizes.items()):
            checked += 1
            try:
                length = self._hash_object(digest)
                checked_bytes += length
                if expected_sizes != {length}:
                    issues.append(f"Size mismatch for {digest}: manifest {sorted(expected_sizes)}, actual {length}")
            except (OSError, KeepsakeError) as exc:
                issues.append(f"Object {digest}: {exc}")
        return Verification(checked, checked_bytes, issues)

    def restore(self, snapshot_id: str, destination: Path | str) -> RestoreResult:
        target = _absolute(destination)
        _guard_path(target)
        if target.exists():
            raise KeepsakeError(f"Restore destination already exists: {target}")
        if not target.parent.is_dir():
            raise KeepsakeError(f"Restore destination parent must exist: {target.parent}")
        if _overlap(target, self.path):
            raise KeepsakeError("Restore destination must be outside the vault")
        entries = self.entries(snapshot_id)
        native_paths: set[str] = set()
        for entry in entries:
            native = os.path.normcase(str(Path(*entry.path.split("/"))))
            if native in native_paths:
                raise KeepsakeError(f"Paths collide on this filesystem: {entry.path}")
            native_paths.add(native)
            if os.name == "nt":
                for part in entry.path.split("/"):
                    if (any(char in part for char in '<>:"|?*') or part.endswith((" ", "."))
                            or PureWindowsPath(part).is_reserved()):
                        raise KeepsakeError(f"Path cannot be restored on Windows: {entry.path}")
        verification = self.verify(snapshot_id)
        if verification.issues:
            raise KeepsakeError("Restore stopped: " + "; ".join(verification.issues))
        stage = Path(tempfile.mkdtemp(prefix=".keepsake-restore-", dir=target.parent))
        try:
            for entry in sorted(entries, key=lambda item: (item.path.count("/"), item.path)):
                output = stage.joinpath(*entry.path.split("/"))
                if entry.kind == "dir":
                    output.mkdir()
                    continue
                digest = hashlib.sha256()
                size = 0
                with _open_regular(self._object_path(entry.digest)) as incoming, output.open("xb") as outgoing:
                    while block := incoming.read(CHUNK_SIZE):
                        outgoing.write(block)
                        digest.update(block)
                        size += len(block)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                if digest.hexdigest() != entry.digest or size != entry.size:
                    raise KeepsakeError(f"Object changed during restore: {entry.digest}")
            # Apply directory metadata last so child creation cannot alter saved mtimes.
            for entry in sorted(entries, key=lambda item: item.path.count("/"), reverse=True):
                output = stage.joinpath(*entry.path.split("/"))
                os.utime(output, ns=(entry.mtime_ns, entry.mtime_ns))
                # Do not restore setuid, setgid or sticky bits from a stored manifest.
                os.chmod(output, entry.mode & 0o777)
            _guard_path(target)
            if target.exists():
                raise KeepsakeError(f"Restore destination appeared during restore: {target}")
            _sync_directory(stage)
            stage.rename(target)
            _sync_directory(target.parent)
        except BaseException:
            if stage.exists():
                def remove_readonly(function, filename, _error):
                    os.chmod(filename, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
                    function(filename)
                shutil.rmtree(stage, onerror=remove_readonly)
            raise
        return RestoreResult(str(target), sum(e.kind == "file" for e in entries), sum(e.size for e in entries))

    def _stored_objects(self) -> dict[str, int]:
        objects = {}
        root = self.path / "objects"
        _guard_path(root)
        for directory in sorted(root.iterdir()):
            _guard_path(directory)
            if not directory.is_dir() or not re.fullmatch(r"[0-9a-f]{2}", directory.name):
                raise KeepsakeError(f"Unexpected entry in object storage: {directory.name}")
            for path in directory.iterdir():
                digest = directory.name + path.name
                self._object_path(digest)
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise KeepsakeError(f"Object is not a regular file: {digest}")
                objects[digest] = info.st_size
        return objects

    def stats(self) -> Stats:
        snapshots = self.list_snapshots()
        with self._connection() as db:
            referenced = {row[0] for row in db.execute("SELECT DISTINCT digest FROM entries WHERE kind = 'file'")}
        stored = self._stored_objects()
        return Stats(len(snapshots), sum(s.file_count for s in snapshots), sum(s.total_bytes for s in snapshots),
                     len(stored), sum(stored.values()), len(stored.keys() - referenced))

    def retention_plan(self, keep_last: int) -> dict[str, list[str]]:
        if isinstance(keep_last, bool) or not isinstance(keep_last, int) or keep_last < 1:
            raise KeepsakeError("keep_last must be a positive integer")
        snapshots = self.list_snapshots()
        return {"keep": [s.id for s in snapshots[:keep_last]], "remove": [s.id for s in snapshots[keep_last:]]}
