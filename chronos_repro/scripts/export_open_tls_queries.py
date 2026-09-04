"""Export CHRONOS TARGET_KEYWORDS without importing modules that require API dependencies."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("news_keywords")
    parser.add_argument("output")
    args = parser.parse_args()
    tree = ast.parse(Path(args.news_keywords).read_text(encoding="utf-8-sig"))
    target = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(name, ast.Name) and name.id == "TARGET_KEYWORDS" for name in node.targets
        ):
            target = ast.literal_eval(node.value)
            break
    if target is None:
        raise RuntimeError("TARGET_KEYWORDS assignment not found")
    queries = {topic_id: query for topic_id, query, _ in target["open"]}
    Path(args.output).write_text(
        json.dumps(queries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
