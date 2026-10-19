from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import Pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="AI for Science perturbation and knockout v1")
    parser.add_argument("command", choices=("doctor", "download", "build", "all"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--target-snapshot", type=Path, required=True)
    parser.add_argument("--snapshot-id")
    args = parser.parse_args()
    pipeline = Pipeline(args.root, args.target_snapshot, args.snapshot_id)
    commands = ("doctor", "download", "build") if args.command == "all" else (args.command,)
    for command in commands:
        result = getattr(pipeline, command)()
        print(json.dumps({"command": command, "result": result}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
