"""Measure capture and recovery against a deterministic temporary corpus."""

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from keepsake.errors import KeepsakeError
from keepsake.repository import Repository


BLOCK_SIZE = 64 * 1024


def positive_integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def content_blocks(seed: int, size: int):
    """Yield the same bytes for a seed without holding a whole file in memory."""
    remaining = size
    block_number = 0
    while remaining:
        length = min(BLOCK_SIZE, remaining)
        key = f"keepsake-corpus-v1:{seed}:{block_number}".encode("ascii")
        yield hashlib.shake_256(key).digest(length)
        remaining -= length
        block_number += 1


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(BLOCK_SIZE):
            digest.update(block)
    return digest.hexdigest()


def timed(operation):
    started = perf_counter()
    result = operation()
    return result, perf_counter() - started


def run_benchmark(files: int, bytes_per_file: int, unique_files: int) -> dict:
    with TemporaryDirectory(prefix="keepsake-benchmark-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        (source / "empty" / "nested").mkdir(parents=True)
        expected: dict[str, str] = {}

        generation_started = perf_counter()
        for index in range(files):
            relative = f"group-{index % 16:02d}/item-{index:08d}.bin"
            path = source / relative
            path.parent.mkdir(exist_ok=True)
            digest = hashlib.sha256()
            with path.open("wb") as output:
                for block in content_blocks(index % unique_files, bytes_per_file):
                    output.write(block)
                    digest.update(block)
            expected[relative] = digest.hexdigest()
        expected_directories = {
            path.relative_to(source).as_posix()
            for path in source.rglob("*")
            if path.is_dir()
        }
        generation_seconds = perf_counter() - generation_started
        distinct_contents = len(set(expected.values()))

        repo = Repository.initialize(root / "vault")
        first, capture_seconds = timed(lambda: repo.snapshot(source, label="Benchmark baseline"))
        first_stats = repo.stats()
        if first.file_count != files or first.total_bytes != files * bytes_per_file:
            raise RuntimeError("First snapshot does not match the generated corpus size.")
        if first_stats.unique_objects != distinct_contents:
            raise RuntimeError("Stored object count does not match independent content hashes.")

        _, unchanged_seconds = timed(lambda: repo.snapshot(source, label="Unchanged recapture"))
        final_stats = repo.stats()
        if final_stats.stored_bytes != first_stats.stored_bytes:
            raise RuntimeError("Unchanged recapture added stored object bytes.")
        if final_stats.unique_objects != first_stats.unique_objects or final_stats.orphan_objects:
            raise RuntimeError("Unchanged recapture changed the object set or left orphan objects.")

        verification, verification_seconds = timed(repo.verify)
        if verification.issues:
            raise RuntimeError("Verification failed: " + "; ".join(verification.issues))

        destination = root / "restored"
        _, restore_seconds = timed(lambda: repo.restore(first.id, destination))
        comparison_started = perf_counter()
        restored_paths = list(destination.rglob("*"))
        if any(path.is_symlink() for path in restored_paths):
            raise RuntimeError("Restore unexpectedly created a symbolic link.")
        restored_files = {
            path.relative_to(destination).as_posix(): path
            for path in restored_paths
            if path.is_file()
        }
        restored_directories = {
            path.relative_to(destination).as_posix()
            for path in restored_paths
            if path.is_dir()
        }
        if set(restored_files) != set(expected):
            raise RuntimeError("Restored file paths do not match the independent source manifest.")
        if restored_directories != expected_directories:
            raise RuntimeError("Restored directories do not match, including empty directories.")
        for relative, path in sorted(restored_files.items()):
            if path.stat().st_size != bytes_per_file or file_digest(path) != expected[relative]:
                raise RuntimeError(f"Restored bytes do not match the generated content: {relative}")
        comparison_seconds = perf_counter() - comparison_started

        return {
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
            "corpus": {
                "generator": "keepsake-corpus-v1",
                "files": files,
                "bytes_per_file": bytes_per_file,
                "total_bytes": files * bytes_per_file,
                "configured_unique_files": unique_files,
                "distinct_contents": distinct_contents,
                "directories": len(expected_directories),
            },
            "storage": {
                "snapshots": final_stats.snapshots,
                "logical_bytes": final_stats.logical_bytes,
                "stored_bytes": final_stats.stored_bytes,
                "unique_objects": final_stats.unique_objects,
                "orphan_objects": final_stats.orphan_objects,
                "unchanged_capture_added_bytes": final_stats.stored_bytes - first_stats.stored_bytes,
            },
            "checks": {
                "verified_objects": verification.checked_objects,
                "restored_files_checked": len(restored_files),
                "restored_directories_checked": len(restored_directories),
                "passed": True,
            },
            "seconds": {
                "generate_corpus": round(generation_seconds, 6),
                "first_capture": round(capture_seconds, 6),
                "unchanged_capture": round(unchanged_seconds, 6),
                "verify": round(verification_seconds, 6),
                "restore": round(restore_seconds, 6),
                "independent_restore_check": round(comparison_seconds, 6),
            },
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=positive_integer, default=256)
    parser.add_argument("--bytes-per-file", type=positive_integer, default=4096)
    parser.add_argument("--unique-files", type=positive_integer, default=64)
    parser.add_argument("--json", action="store_true", help="write a machine-readable result")
    args = parser.parse_args(argv)
    if args.unique_files > args.files:
        parser.error("--unique-files cannot exceed --files")
    try:
        result = run_benchmark(args.files, args.bytes_per_file, args.unique_files)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=True))
        else:
            print(f"Python {result['environment']['python']} on {result['environment']['platform']}")
            print(f"Corpus: {args.files} files x {args.bytes_per_file} bytes")
            print(f"Distinct contents: {result['corpus']['distinct_contents']}")
            print(
                f"Storage after two snapshots: {result['storage']['logical_bytes']} logical bytes, "
                f"{result['storage']['stored_bytes']} stored bytes"
            )
            print("Unchanged recapture added 0 stored bytes.")
            print(f"Verified {result['checks']['verified_objects']} objects and checked every restored file.")
            for operation, seconds in result["seconds"].items():
                print(f"{operation.replace('_', ' ')}: {seconds:.6f} s")
            print("Elapsed times describe this local run only. Temporary data has been removed.")
        return 0
    except (KeepsakeError, OSError, RuntimeError) as error:
        if args.json:
            print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        else:
            print(f"benchmark: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("benchmark: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
