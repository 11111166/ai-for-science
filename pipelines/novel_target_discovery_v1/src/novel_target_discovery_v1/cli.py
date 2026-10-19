from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import Pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="AI for Science Novel Target Discovery v1")
    parser.add_argument("command", choices=("doctor", "download", "build", "benchmark", "publish", "all"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--sources-config", type=Path)
    parser.add_argument("--disease-config", type=Path)
    parser.add_argument("--top-n", type=int, default=500)
    args = parser.parse_args()
    pipeline = Pipeline(args.root, args.sources_config, args.disease_config)
    commands = ("doctor", "download", "build", "benchmark", "publish") if args.command == "all" else (args.command,)
    for command in commands:
        result = pipeline.publish(args.top_n) if command == "publish" else getattr(pipeline, command)()
        print(json.dumps({"command": command, "result": result}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
