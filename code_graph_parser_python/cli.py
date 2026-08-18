from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parser import PythonCodeGraphParser


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--project")
    parser.add_argument("--project-name")
    parser.add_argument("--request")
    parser.add_argument("--out")
    args = parser.parse_args()
    if args.request:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    elif args.stdio:
        request = json.load(sys.stdin)
    elif args.project:
        root = str(Path(args.project).resolve())
        request = {
            "projectName": args.project_name or Path(root).name,
            "language": "python",
            "projectRoot": root,
            "sourceFiles": [],
        }
    else:
        parser.error("use --stdio, --project, or --request")
    delta = PythonCodeGraphParser().parse(request)
    output = json.dumps(delta, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    sys.stderr.write(
        "Parsed %d Python files, %d units, %d functions, %d relationships.\n"
        % (
            len(delta["scope"]["sourceFiles"]),
            len(delta["units"]),
            len(delta["functions"]),
            len(delta["relationships"]),
        )
    )


if __name__ == "__main__":
    main()
