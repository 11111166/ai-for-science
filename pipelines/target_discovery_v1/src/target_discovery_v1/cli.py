from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import Pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="AI for Science Target Discovery v1 pipeline")
    parser.add_argument("command", choices=("doctor", "download", "build", "benchmark", "publish", "all"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--top-n", type=int, default=100)
    args = parser.parse_args()
    pipeline = Pipeline(args.root)
    commands = ("doctor", "download", "build", "benchmark", "publish") if args.command == "all" else (args.command,)
    for command in commands:
        result = pipeline.publish(args.top_n) if command == "publish" else getattr(pipeline, command)()
        print(json.dumps({"command": command, "result": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

