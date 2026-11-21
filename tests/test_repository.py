from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from keepsake.errors import KeepsakeError
from keepsake import repository as repository_module
from keepsake.repository import Repository


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.vault = self.root / "vault"
        self.repo = Repository.initialize(self.vault)

    def write(self, relative, content=b"hello\n"):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def object_for(self, snapshot_id, relative):
        entry = next(item for item in self.repo.entries(snapshot_id)
                     if item.path == relative)
        return self.vault / "objects" / entry.digest[:2] / entry.digest[2:]

    def symlink(self, target, link, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlinks unavailable on this system: {exc}")

    def test_open_requires_an_initialized_repository(self):
        with self.assertRaises(KeepsakeError):
            Repository(self.root / "missing")

    def test_empty_snapshot_restores_an_empty_directory(self):
        snapshot = self.repo.snapshot(self.source, label="empty")
        self.assertEqual(snapshot.file_count, 0)
        self.assertEqual(snapshot.total_bytes, 0)
        self.assertEqual(snapshot.label, "empty")
        destination = self.root / "restored"
        result = self.repo.restore(snapshot.id, destination)
        self.assertTrue(destination.is_dir())
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual(result.files, 0)

    def test_restore_preserves_bytes_names_and_empty_directories(self):
        contents = {
            "notes.txt": b"one\ntwo\n",
            "nested/data.bin": bytes(range(256)) * 100,
            "nested/café.txt": "résumé\n".encode("utf-8"),
            "zero.bin": b"",
        }
        for name, content in contents.items():
            self.write(name, content)
        (self.source / "empty" / "child").mkdir(parents=True)
        snapshot = self.repo.snapshot(self.source)
        destination = self.root / "restored"
        result = self.repo.restore(snapshot.id, destination)
        self.assertEqual(result.files, len(contents))
        self.assertEqual(result.total_bytes, sum(map(len, contents.values())))
        for name, content in contents.items():
            self.assertEqual((destination / name).read_bytes(), content)
        self.assertTrue((destination / "empty" / "child").is_dir())

    def test_duplicate_contents_share_storage_across_snapshots(self):
        content = b"shared contents" * 4096
        self.write("one.bin", content)
        self.write("nested/two.bin", content)
        self.repo.snapshot(self.source)
        self.repo.snapshot(self.source)
        stats = self.repo.stats()
        self.assertEqual(stats.snapshots, 2)
        self.assertEqual(stats.files, 4)
        self.assertEqual(stats.logical_bytes, len(content) * 4)
        self.assertEqual(stats.unique_objects, 1)
        self.assertEqual(stats.stored_bytes, len(content))
        self.assertEqual(stats.orphan_objects, 0)
        report = self.repo.verify()
        self.assertEqual(report.checked_objects, 1)
        self.assertEqual(report.checked_bytes, len(content))
        self.assertEqual(report.issues, [])

    def test_diff_reports_added_deleted_and_modified_files(self):
        self.write("keep.txt", b"same")
        self.write("edit.txt", b"before")
        self.write("delete.txt", b"remove me")
        before = self.repo.snapshot(self.source)
        self.write("edit.txt", b"after!")
        (self.source / "delete.txt").unlink()
        self.write("add.txt", b"new")
        after = self.repo.snapshot(self.source)
        changes = {change.path: change.status
                   for change in self.repo.diff(before.id, after.id)}
        self.assertEqual(changes, {
            "add.txt": "added", "delete.txt": "deleted", "edit.txt": "modified"
        })
        self.assertEqual(self.repo.diff(after.id, after.id), [])

    def test_timestamp_only_change_is_metadata(self):
        path = self.write("notes.txt", b"unchanged")
        before = self.repo.snapshot(self.source)
        original = path.stat()
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns + 2_000_000_000))
        after = self.repo.snapshot(self.source)
        self.assertEqual(
            [(change.path, change.status) for change in self.repo.diff(before.id, after.id)],
            [("notes.txt", "metadata")],
        )
        self.assertEqual(self.repo.stats().unique_objects, 1)

    def test_earlier_snapshot_survives_later_source_edits_and_deletion(self):
        path = self.write("notes.txt", b"version one")
        first = self.repo.snapshot(self.source)
        path.write_bytes(b"version two")
        second = self.repo.snapshot(self.source)
        path.unlink()
        self.repo.restore(first.id, self.root / "first")
        self.repo.restore(second.id, self.root / "second")
        self.assertEqual((self.root / "first/notes.txt").read_bytes(), b"version one")
        self.assertEqual((self.root / "second/notes.txt").read_bytes(), b"version two")

    def test_restore_preserves_file_modification_time(self):
        path = self.write("notes.txt")
        timestamp = 1_700_000_000_000_000_000
        os.utime(path, ns=(timestamp, timestamp))
        original_mtime = path.stat().st_mtime_ns
        snapshot = self.repo.snapshot(self.source)
        self.repo.restore(snapshot.id, self.root / "restored")
        self.assertEqual((self.root / "restored/notes.txt").stat().st_mtime_ns,
                         original_mtime)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not supported on Windows")
    def test_restore_preserves_posix_executable_permission(self):
        path = self.write("run.sh", b"#!/bin/sh\nexit 0\n")
        path.chmod(0o750)
        snapshot = self.repo.snapshot(self.source)
        self.repo.restore(snapshot.id, self.root / "restored")
        self.assertEqual((self.root / "restored/run.sh").stat().st_mode & 0o777, 0o750)

    def test_excludes_prune_matching_directories_and_files(self):
        self.write("keep.txt")
        self.write(".git/config", b"private config")
        self.write("nested/.git/config", b"another config")
        self.write("nested/cache.tmp", b"temporary")
        self.write("cache/output.bin", b"generated")
        snapshot = self.repo.snapshot(self.source, excludes=[".git", "*.tmp", "cache"])
        files = {entry.path for entry in self.repo.entries(snapshot.id)
                 if entry.kind == "file"}
        self.assertEqual(files, {"keep.txt"})
        self.assertFalse(any(".git" in entry.path for entry in self.repo.entries(snapshot.id)))

    def test_source_and_vault_must_not_contain_each_other(self):
        with self.assertRaises(KeepsakeError):
            self.repo.snapshot(self.root)
        with self.assertRaises(KeepsakeError):
            self.repo.snapshot(self.vault)
        inside_vault = self.vault / "source"
        inside_vault.mkdir()
        with self.assertRaises(KeepsakeError):
            self.repo.snapshot(inside_vault)
        self.assertEqual(self.repo.list_snapshots(), [])

    def test_source_must_be_an_existing_directory(self):
        file_path = self.write("notes.txt")
        for source in (self.root / "missing", file_path):
            with self.subTest(source=source):
                with self.assertRaises(KeepsakeError):
                    self.repo.snapshot(source)

    def test_restore_never_overwrites_an_existing_destination(self):
        self.write("notes.txt", b"snapshot")
        snapshot = self.repo.snapshot(self.source)
        destination = self.root / "restored"
        destination.mkdir()
        marker = destination / "notes.txt"
        marker.write_bytes(b"keep this")
        with self.assertRaises(KeepsakeError):
            self.repo.restore(snapshot.id, destination)
        self.assertEqual(marker.read_bytes(), b"keep this")

    def test_restore_rejects_missing_parent_and_vault_destination(self):
        self.write("notes.txt")
        snapshot = self.repo.snapshot(self.source)
        for destination in (self.root / "missing/restored", self.vault / "restored"):
            with self.subTest(destination=destination):
                with self.assertRaises(KeepsakeError):
                    self.repo.restore(snapshot.id, destination)
                self.assertFalse(destination.exists())

    def test_corrupt_object_is_reported_and_restore_is_not_published(self):
        self.write("notes.txt", b"original contents")
        snapshot = self.repo.snapshot(self.source)
        self.object_for(snapshot.id, "notes.txt").write_bytes(b"changed contents!")
        self.assertTrue(self.repo.verify(snapshot.id).issues)
        destination = self.root / "restored"
        before = set(self.root.iterdir())
        with self.assertRaises(KeepsakeError):
            self.repo.restore(snapshot.id, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(set(self.root.iterdir()), before)

    def test_missing_object_is_reported_and_restore_is_not_published(self):
        self.write("notes.txt")
        snapshot = self.repo.snapshot(self.source)
        self.object_for(snapshot.id, "notes.txt").unlink()
        self.assertTrue(self.repo.verify(snapshot.id).issues)
        destination = self.root / "restored"
        with self.assertRaises(KeepsakeError):
            self.repo.restore(snapshot.id, destination)
        self.assertFalse(destination.exists())

    def test_verification_can_target_an_unaffected_snapshot(self):
        path = self.write("notes.txt", b"first")
        first = self.repo.snapshot(self.source)
        path.write_bytes(b"second")
        second = self.repo.snapshot(self.source)
        self.object_for(second.id, "notes.txt").write_bytes(b"broken")
        self.assertEqual(self.repo.verify(first.id).issues, [])
        self.assertTrue(self.repo.verify(second.id).issues)
        self.assertTrue(self.repo.verify().issues)

    def test_unknown_snapshot_fails_without_creating_restore_output(self):
        unknown = "f" * 32
        with self.assertRaises(KeepsakeError):
            self.repo.entries(unknown)
        with self.assertRaises(KeepsakeError):
            self.repo.verify(unknown)
        with self.assertRaises(KeepsakeError):
            self.repo.restore(unknown, self.root / "restored")
        self.assertFalse((self.root / "restored").exists())

    def test_file_symlink_is_skipped_instead_of_followed(self):
        outside = self.root / "outside.txt"
        outside.write_bytes(b"outside contents")
        self.symlink(outside, self.source / "link.txt")
        self.write("keep.txt")
        snapshot = self.repo.snapshot(self.source)
        self.assertEqual(snapshot.file_count, 1)
        self.assertTrue(any("link.txt" in item for item in snapshot.skipped))
        self.assertFalse(any(entry.path == "link.txt" for entry in self.repo.entries(snapshot.id)))

    def test_directory_symlink_is_skipped_instead_of_traversed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_bytes(b"outside contents")
        self.symlink(outside, self.source / "linked", directory=True)
        snapshot = self.repo.snapshot(self.source)
        self.assertEqual(snapshot.file_count, 0)
        self.assertTrue(any("linked" in item for item in snapshot.skipped))

    def test_restore_rejects_symlink_ancestor(self):
        self.write("notes.txt")
        snapshot = self.repo.snapshot(self.source)
        actual = self.root / "actual"
        actual.mkdir()
        alias = self.root / "alias"
        self.symlink(actual, alias, directory=True)
        with self.assertRaises(KeepsakeError):
            self.repo.restore(snapshot.id, alias / "restored")
        self.assertEqual(list(actual.iterdir()), [])

    def test_retention_plan_keeps_newest_and_does_not_delete(self):
        self.write("notes.txt")
        snapshots = [self.repo.snapshot(self.source) for _ in range(3)]
        before_stats = self.repo.stats()
        plan = self.repo.retention_plan(keep_last=1)
        self.assertEqual(plan["keep"], [snapshots[-1].id])
        self.assertEqual(set(plan["remove"]), {snapshot.id for snapshot in snapshots[:-1]})
        self.assertEqual(self.repo.stats(), before_stats)
        self.assertEqual(len(self.repo.list_snapshots()), 3)
        self.assertEqual(self.repo.verify().issues, [])

    def test_invalid_retention_count_is_rejected(self):
        with self.assertRaises(KeepsakeError):
            self.repo.retention_plan(keep_last=-1)

    def test_reopened_repository_retains_snapshots(self):
        self.write("notes.txt", b"persistent")
        snapshot = self.repo.snapshot(self.source, label="saved")
        reopened = Repository(self.vault)
        self.assertEqual(reopened.list_snapshots()[0].id, snapshot.id)
        self.assertEqual(reopened.list_snapshots()[0].label, "saved")
        self.assertEqual(reopened.verify().issues, [])

    def test_newer_repository_format_is_rejected(self):
        with closing(sqlite3.connect(self.vault / "manifest.sqlite3")) as connection, connection:
            connection.execute("PRAGMA user_version = 999")
        with self.assertRaises(KeepsakeError):
            Repository(self.vault)

    def test_manifest_paths_cannot_escape_the_restore_tree(self):
        self.write("notes.txt", b"snapshot contents")
        snapshot = self.repo.snapshot(self.source)
        outside = self.root / "outside.txt"
        outside.write_bytes(b"leave this alone")
        invalid_paths = [
            "../outside.txt", "/outside.txt", "C:/outside.txt", "nested/../outside.txt",
            "nested\\outside.txt", "./outside.txt", "nested//outside.txt",
        ]
        for invalid_path in invalid_paths:
            with self.subTest(path=invalid_path):
                with closing(sqlite3.connect(self.vault / "manifest.sqlite3")) as connection, connection:
                    connection.execute(
                        "UPDATE entries SET path = ? WHERE snapshot_id = ?",
                        (invalid_path, snapshot.id),
                    )
                with self.assertRaises(KeepsakeError):
                    self.repo.restore(snapshot.id, self.root / "restored")
                self.assertFalse((self.root / "restored").exists())
                self.assertEqual(outside.read_bytes(), b"leave this alone")

    def test_manifest_digest_cannot_point_outside_object_storage(self):
        self.write("notes.txt")
        snapshot = self.repo.snapshot(self.source)
        with closing(sqlite3.connect(self.vault / "manifest.sqlite3")) as connection, connection:
            connection.execute(
                "UPDATE entries SET digest = ? WHERE snapshot_id = ?",
                ("../../outside.txt", snapshot.id),
            )
        with self.assertRaises(KeepsakeError):
            self.repo.restore(snapshot.id, self.root / "restored")
        self.assertFalse((self.root / "restored").exists())

    def test_manifest_file_cannot_also_be_a_directory_parent(self):
        self.write("parent", b"file contents")
        self.write("other", b"other contents")
        snapshot = self.repo.snapshot(self.source)
        with closing(sqlite3.connect(self.vault / "manifest.sqlite3")) as connection, connection:
            connection.execute(
                "UPDATE entries SET path = ? WHERE snapshot_id = ? AND path = ?",
                ("parent/child", snapshot.id, "other"),
            )
        with self.assertRaises(KeepsakeError):
            self.repo.restore(snapshot.id, self.root / "restored")
        self.assertFalse((self.root / "restored").exists())

    def test_a_link_in_object_storage_is_not_trusted(self):
        self.write("notes.txt", b"original contents")
        snapshot = self.repo.snapshot(self.source)
        object_path = self.object_for(snapshot.id, "notes.txt")
        outside = self.root / "outside.txt"
        outside.write_bytes(object_path.read_bytes())
        object_path.unlink()
        self.symlink(outside, object_path)
        self.assertTrue(self.repo.verify(snapshot.id).issues)
        with self.assertRaises(KeepsakeError):
            self.repo.restore(snapshot.id, self.root / "restored")
        self.assertFalse((self.root / "restored").exists())

    def test_existing_writer_lock_prevents_a_second_snapshot(self):
        self.write("notes.txt")
        lock = self.vault / "write.lock"
        lock.write_bytes(b"another writer owns this lock")
        with self.assertRaises(KeepsakeError):
            self.repo.snapshot(self.source)
        self.assertEqual(self.repo.list_snapshots(), [])
        self.assertEqual(lock.read_bytes(), b"another writer owns this lock")

    def test_file_changed_during_read_does_not_commit_a_partial_snapshot(self):
        self.write("original.txt", b"known good")
        committed = self.repo.snapshot(self.source)
        self.write("a-complete.txt", b"new object written before failure")
        changing = self.write("z-changing.txt", b"initial data")
        real_open = repository_module._open_regular

        class ChangingReader:
            def __init__(self, stream):
                self.stream = stream
                self.changed = False

            def __enter__(self):
                return self

            def __exit__(self, *arguments):
                return self.stream.__exit__(*arguments)

            def fileno(self):
                return self.stream.fileno()

            def read(self, size=-1):
                data = self.stream.read(size)
                if data and not self.changed:
                    changing.write_bytes(b"modified while being read")
                    self.changed = True
                return data

        def open_with_concurrent_edit(path):
            stream = real_open(path)
            return ChangingReader(stream) if path == changing else stream

        with patch.object(repository_module, "_open_regular", side_effect=open_with_concurrent_edit):
            with self.assertRaises(KeepsakeError):
                self.repo.snapshot(self.source)
        self.assertEqual([item.id for item in self.repo.list_snapshots()], [committed.id])
        self.assertEqual(self.repo.verify(committed.id).issues, [])
        self.assertFalse((self.vault / "write.lock").exists())
        self.assertEqual(list((self.vault / "tmp").iterdir()), [])

    def test_disappearing_source_file_does_not_commit_a_partial_snapshot(self):
        self.write("original.txt", b"known good")
        committed = self.repo.snapshot(self.source)
        self.write("a-complete.txt", b"new object written before failure")
        disappearing = self.write("z-disappearing.txt", b"gone before open")
        real_open = repository_module._open_regular

        def open_after_removal(path):
            if path == disappearing:
                path.unlink()
            return real_open(path)

        with patch.object(repository_module, "_open_regular", side_effect=open_after_removal):
            with self.assertRaises((KeepsakeError, OSError)):
                self.repo.snapshot(self.source)
        self.assertEqual([item.id for item in self.repo.list_snapshots()], [committed.id])
        self.assertEqual(self.repo.verify(committed.id).issues, [])
        self.assertFalse((self.vault / "write.lock").exists())
        self.assertEqual(list((self.vault / "tmp").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
