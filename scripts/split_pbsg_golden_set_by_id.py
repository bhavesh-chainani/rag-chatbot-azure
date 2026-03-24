"""Write one JSON file per Golden Set entry for ingestion.

Azure AI Search then gets one document per entry (`sourcefile` = `FAM-03.json`, etc.),
which improves vector focus and keyword overlap with the entry id. Canonical array
remains `data/pbsg_golden_set_complete_v2.json` for evaluation and regeneration.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "pbsg_golden_set_complete_v2.json"
DEFAULT_OUT_DIR = ROOT / "data" / "pbsg_golden_set_by_id"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Path to golden set array JSON (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUT_DIR})",
    )
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 1

    data = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        print("Expected a JSON array of entry objects.", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    for obj in data:
        if not isinstance(obj, dict):
            print(f"Expected objects in array, got {type(obj)!r}", file=sys.stderr)
            return 1
        oid = obj.get("id")
        if not isinstance(oid, str) or not oid.strip():
            print(f"Missing string id on object with keys: {list(obj.keys())}", file=sys.stderr)
            return 1
        oid = oid.strip()
        if oid in seen:
            print(f"Duplicate id: {oid}", file=sys.stderr)
            return 1
        seen.add(oid)
        out_path = args.out_dir / f"{oid}.json"
        out_path.write_text(json.dumps(obj, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    print(f"Wrote {len(seen)} files to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
