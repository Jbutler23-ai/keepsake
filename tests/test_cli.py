import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.vault = self.root / "vault"
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "notes.txt").write_bytes(b"first version")

    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, "-m", "keepsake", "--vault", str(self.vault),
             "--json", *map(str, arguments)],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )

    def success(self, *arguments):
        result = self.run_cli(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_json_workflow_init_snapshot_list_restore_and_verify(self):
        self.success("init")
        snapshot = self.success("snapshot", self.source, "--label", "first backup")
        self.assertEqual(snapshot["label"], "first backup")
        self.assertEqual(snapshot["file_count"], 1)
        self.assertEqual(self.success("list")[0]["id"], snapshot["id"])
        destination = self.root / "restored"
        restored = self.success("restore", snapshot["id"], destination)
        self.assertEqual(restored["files"], 1)
        self.assertEqual((destination / "notes.txt").read_bytes(), b"first version")
        report = self.success("verify")
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["checked_objects"], 1)

    def test_diff_and_stats_output_are_machine_readable(self):
        self.success("init")
        first = self.success("snapshot", self.source)
        (self.source / "notes.txt").write_bytes(b"second version")
        second = self.success("snapshot", self.source)
        changes = self.success("diff", first["id"], second["id"])
        self.assertEqual(changes, [{"path": "notes.txt", "status": "modified"}])
        stats = self.success("stats")
        self.assertEqual(stats["snapshots"], 2)
        self.assertEqual(stats["unique_objects"], 2)

    def test_unknown_repository_returns_json_error_without_traceback(self):
        result = self.run_cli("list")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", json.loads(result.stderr))
        self.assertNotIn("Traceback", result.stderr)

    def test_existing_restore_destination_returns_nonzero(self):
        self.success("init")
        snapshot = self.success("snapshot", self.source)
        destination = self.root / "restored"
        destination.mkdir()
        result = self.run_cli("restore", snapshot["id"], destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(list(destination.iterdir()), [])

    def test_corrupt_storage_causes_verify_to_return_failure(self):
        self.success("init")
        self.success("snapshot", self.source)
        objects = [path for path in (self.vault / "objects").rglob("*") if path.is_file()]
        self.assertEqual(len(objects), 1)
        objects[0].write_bytes(b"corrupt")
        result = self.run_cli("verify")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(json.loads(result.stdout)["issues"])

    def test_retention_plan_only_previews_removal(self):
        self.success("init")
        first = self.success("snapshot", self.source)
        second = self.success("snapshot", self.source)
        plan = self.success("retention-plan", "--keep-last", "1")
        self.assertEqual(plan["keep"], [second["id"]])
        self.assertEqual(plan["remove"], [first["id"]])
        self.assertEqual(len(self.success("list")), 2)

    def test_help_does_not_require_a_repository(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("snapshot", result.stdout)
        self.assertIn("restore", result.stdout)


if __name__ == "__main__":
    unittest.main()
