"""Command-line access to a local snapshot vault."""

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

from . import __version__
from .errors import KeepsakeError
from .repository import Repository


def _common_options(parser: argparse.ArgumentParser, *, root: bool = False) -> None:
    parser.add_argument(
        "--vault",
        type=Path,
        default=Path("keepsake-vault") if root else argparse.SUPPRESS,
        metavar="PATH",
        help="vault directory (default: ./keepsake-vault)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False if root else argparse.SUPPRESS,
        help="write machine-readable JSON",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keepsake",
        description="Keep versioned, offline copies of local folders.",
        epilog="Use a full snapshot ID or 'latest'. Restore always creates a new directory.",
    )
    parser.add_argument("--version", action="version", version=f"Keepsake {__version__}")
    _common_options(parser, root=True)
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        subparser = commands.add_parser(name, help=help_text, description=help_text)
        _common_options(subparser)
        return subparser

    command("init", "Create a vault in an empty directory.")
    snapshot = command("snapshot", "Capture a folder and store new file contents.")
    snapshot.add_argument("source", type=Path, metavar="SOURCE")
    snapshot.add_argument("--label", default="", help="optional description for this snapshot")
    snapshot.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="skip matching names or relative paths; repeat for multiple patterns",
    )
    command("list", "List snapshots, newest first.")
    diff = command("diff", "Compare paths, content, and metadata in two snapshots.")
    diff.add_argument("before", metavar="BEFORE")
    diff.add_argument("after", metavar="AFTER")
    restore = command("restore", "Verify and restore into a new directory.")
    restore.add_argument("snapshot", metavar="SNAPSHOT")
    restore.add_argument("destination", type=Path, metavar="DESTINATION")
    verify = command("verify", "Check stored contents against their recorded hashes.")
    verify.add_argument("snapshot", nargs="?", metavar="SNAPSHOT", help="omit to check the whole vault")
    command("stats", "Show logical size, physical size, and object counts.")
    retention = command("retention-plan", "Preview which snapshots a keep-last policy would remove.")
    retention.add_argument("--keep-last", required=True, type=int, metavar="COUNT")
    return parser


def _resolve_snapshot(repo: Repository, identifier: str) -> str:
    if identifier != "latest":
        return identifier
    snapshots = repo.list_snapshots()
    if not snapshots:
        raise KeepsakeError("The vault has no snapshots.")
    return snapshots[0].id


def _jsonable(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _print_result(command: str, result) -> None:
    if command == "init":
        print(f"Created vault {_quoted(result['vault'])}")
    elif command == "snapshot":
        print(f"Snapshot {result.id}")
        print(f"Captured {result.file_count} files, {_size(result.total_bytes)}")
        for path in result.skipped:
            print(f"Skipped: {_quoted(path)}")
    elif command == "list":
        if not result:
            print("No snapshots yet.")
        for snapshot in result:
            print(
                f"{snapshot.id}  {snapshot.created_at}  "
                f"{snapshot.file_count} files  {_size(snapshot.total_bytes)}  "
                f"{_quoted(snapshot.label)}"
            )
    elif command == "diff":
        if not result:
            print("No changes.")
        for change in result:
            print(f"{change.status:<9} {_quoted(change.path)}")
    elif command == "restore":
        print(f"Restored {result.files} files, {_size(result.total_bytes)}")
        print(f"Destination: {_quoted(result.destination)}")
    elif command == "verify":
        print(f"Checked {result.checked_objects} objects, {_size(result.checked_bytes)}")
        if result.issues:
            for issue in result.issues:
                print(f"Issue: {_quoted(issue)}")
        else:
            print("Integrity check passed.")
    elif command == "stats":
        print(f"Snapshots:      {result.snapshots}")
        print(f"File versions:  {result.files}")
        print(f"Logical size:   {_size(result.logical_bytes)}")
        print(f"Stored size:    {_size(result.stored_bytes)}")
        print(f"Unique objects: {result.unique_objects}")
        print(f"Orphan objects: {result.orphan_objects}")
    elif command == "retention-plan":
        print("Preview only. No snapshots or objects were deleted.")
        for snapshot_id in result["keep"]:
            print(f"Keep:   {snapshot_id}")
        for snapshot_id in result["remove"]:
            print(f"Remove: {snapshot_id}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            Repository.initialize(args.vault)
            result = {"vault": str(args.vault.resolve())}
        else:
            repo = Repository(args.vault)
            if args.command == "snapshot":
                result = repo.snapshot(args.source, label=args.label, excludes=args.exclude)
            elif args.command == "list":
                result = repo.list_snapshots()
            elif args.command == "diff":
                # Resolve aliases once so both sides use the same snapshot list.
                latest = None
                if "latest" in (args.before, args.after):
                    latest = _resolve_snapshot(repo, "latest")
                before = latest if args.before == "latest" else args.before
                after = latest if args.after == "latest" else args.after
                result = repo.diff(before, after)
            elif args.command == "restore":
                result = repo.restore(_resolve_snapshot(repo, args.snapshot), args.destination)
            elif args.command == "verify":
                snapshot_id = _resolve_snapshot(repo, args.snapshot) if args.snapshot else None
                result = repo.verify(snapshot_id)
            elif args.command == "stats":
                result = repo.stats()
            elif args.command == "retention-plan":
                result = repo.retention_plan(args.keep_last)
            else:
                raise AssertionError(f"Unhandled command: {args.command}")
        if args.json:
            print(json.dumps(_jsonable(result), ensure_ascii=True, indent=2))
        else:
            _print_result(args.command, result)
        return 1 if args.command == "verify" and result.issues else 0
    except (KeepsakeError, OSError, sqlite3.Error) as error:
        message = str(error)
        if args.json:
            print(json.dumps({"error": message}, ensure_ascii=True), file=sys.stderr)
        else:
            print(f"keepsake: {message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        if args.json:
            print(json.dumps({"error": "Interrupted."}), file=sys.stderr)
        else:
            print("keepsake: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
