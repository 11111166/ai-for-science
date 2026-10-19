from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import Pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="AI for Science Virtual Cell v1")
    parser.add_argument("command", choices=("doctor", "build", "all"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--h5ad", type=Path, required=True)
    parser.add_argument("--target-snapshot", type=Path, required=True)
    parser.add_argument("--intervention-snapshot", type=Path, required=True)
    parser.add_argument("--model-config", type=Path)
    parser.add_argument("--disease-config", type=Path)
    args = parser.parse_args()
    pipeline = Pipeline(args.root, args.h5ad, args.target_snapshot, args.intervention_snapshot, args.model_config, args.disease_config)
    commands = ("doctor", "build") if args.command == "all" else (args.command,)
    for command in commands:
        print(json.dumps({"command": command, "result": getattr(pipeline, command)()}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
