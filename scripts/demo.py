"""Exercise snapshots and recovery using disposable example files."""

import json
import sys
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from keepsake.repository import Repository


def main() -> None:
    with TemporaryDirectory(prefix="keepsake-demo-") as temporary:
        root = Path(temporary)
        source = root / "project"
        source.mkdir()
        (source / "empty-folder").mkdir()
        original = b"Workshop notes\nMeasure the shelf before buying brackets.\n"
        (source / "notes.txt").write_bytes(original)
        (source / "notes-copy.txt").write_bytes(original)

        repo = Repository.initialize(root / "vault")
        first = repo.snapshot(source, label="Before editing")
        initial_stats = repo.stats()
        if initial_stats.unique_objects != 1:
            raise RuntimeError("Identical files did not share one object.")

        (source / "notes.txt").write_text("Workshop notes\nShelf width: 90 cm.\n", encoding="utf-8")
        (source / "supplies.txt").write_text("2 brackets\n6 screws\n", encoding="utf-8")
        second = repo.snapshot(source, label="Updated measurements")
        changes = repo.diff(first.id, second.id)
        verification = repo.verify()
        if verification.issues:
            raise RuntimeError(f"Verification failed: {verification.issues}")

        restored = repo.restore(first.id, root / "recovered")
        if (root / "recovered" / "notes.txt").read_bytes() != original:
            raise RuntimeError("Restored bytes do not match the first snapshot.")
        if not (root / "recovered" / "empty-folder").is_dir():
            raise RuntimeError("The empty directory was not restored.")

        print("Created two snapshots from temporary example files.")
        print("The first snapshot stores two identical files as one object.")
        print("Changes between snapshots:")
        print(json.dumps([asdict(change) for change in changes], indent=2))
        print("Vault statistics:")
        print(json.dumps(asdict(repo.stats()), indent=2))
        print(f"Verified {verification.checked_objects} stored objects.")
        print(f"Restored {restored.files} files and checked the original bytes.")
        print("Temporary files are removed when this demo exits.")


if __name__ == "__main__":
    main()
