#!/usr/bin/env python3
"""Monotonic per-ring request queue; filenames never determine order."""
import argparse
import json
from pathlib import Path

from ringctl import RINGS, validate_payload


class QueueError(ValueError):
    pass


def load_index(root: Path, ring: str) -> dict:
    path = root / "request-index" / f"{ring}.json"
    if not path.exists():
        return {
            "schema": "openrappter-request-index/v1",
            "ring": ring,
            "next_sequence": 1,
            "entries": [],
        }
    value = json.loads(path.read_text())
    if set(value) != {"schema", "ring", "next_sequence", "entries"} or value["schema"] != "openrappter-request-index/v1" or value["ring"] != ring:
        raise QueueError("request index is not closed")
    sequences = [entry["sequence"] for entry in value["entries"]]
    if sequences != list(range(1, len(sequences) + 1)) or value["next_sequence"] != len(sequences) + 1:
        raise QueueError("request index has a gap or non-monotonic sequence")
    return value


def enqueue(root: Path, request: dict) -> tuple[dict, str, bool]:
    ring = request["to"]
    if ring not in RINGS:
        raise QueueError("unknown target ring")
    index = load_index(root, ring)
    existing = next((entry for entry in index["entries"] if entry["request_id"] == request["promotion_id"]), None)
    if existing:
        request["sequence"] = existing["sequence"]
        validate_payload(request)
        path = root / existing["path"]
        if not path.exists() or json.loads(path.read_text()) != request:
            raise QueueError("existing request id conflicts with immutable queued request")
        return index, existing["path"], False
    sequence = index["next_sequence"]
    request["sequence"] = sequence
    validate_payload(request)
    relative = f"requests/{ring}/{sequence:020d}-{request['promotion_id']}.json"
    index["entries"].append({
        "sequence": sequence,
        "request_id": request["promotion_id"],
        "path": relative,
    })
    index["next_sequence"] = sequence + 1
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n")
    index_path = root / "request-index" / f"{ring}.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    return index, relative, True


def select(index: dict, cursor: int, requested: int | None = None) -> dict | None:
    ring = index["ring"]
    # Revalidate gaps independently of enqueue.
    sequences = [entry["sequence"] for entry in index["entries"]]
    if sequences != list(range(1, len(sequences) + 1)):
        raise QueueError("request queue gap")
    wanted = requested if requested is not None else cursor + 1
    if wanted <= cursor:
        return None
    if wanted != cursor + 1:
        raise QueueError("requested sequence skips unapplied request")
    entry = next((entry for entry in index["entries"] if entry["sequence"] == wanted), None)
    if entry is None:
        return None
    return entry


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("enqueue")
    add.add_argument("--root", required=True); add.add_argument("--request", required=True)
    choose = sub.add_parser("select")
    choose.add_argument("--index", required=True); choose.add_argument("--cursor", type=int, required=True)
    choose.add_argument("--sequence", type=int)
    args = parser.parse_args()
    if args.command == "enqueue":
        request = json.loads(Path(args.request).read_text())
        _, path, changed = enqueue(Path(args.root), request)
        print(json.dumps({"path": path, "sequence": request["sequence"], "changed": changed}))
    else:
        entry = select(json.loads(Path(args.index).read_text()), args.cursor, args.sequence)
        print(json.dumps(entry))


if __name__ == "__main__":
    main()
