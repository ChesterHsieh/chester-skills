#!/usr/bin/env python3
"""把 .pptx / .html / .md 交付物抽成统一的中间表示（IR），输出 JSON 到 stdout。

零外部依赖，只用 stdlib。

用法:
    python3 extract.py <file> [--pretty]

输出结构:
    {
      "source": str, "kind": "pptx|html|md", "unit_count": int,
      "title_chain": [str, ...],          # S1.2 标题链测试直接读这个
      "units": [ {index, title, text_blocks, word_count,
                  images, tables, charts, notes, flags}, ... ],
      "totals": {words, images, tables, charts, animations}
    }
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import html_parser
import md_parser
import pptx_parser

PARSERS = {
    ".pptx": ("pptx", pptx_parser.parse),
    ".potx": ("pptx", pptx_parser.parse),
    ".ppsx": ("pptx", pptx_parser.parse),
    ".html": ("html", html_parser.parse),
    ".htm": ("html", html_parser.parse),
    ".md": ("md", md_parser.parse),
    ".markdown": ("md", md_parser.parse),
    ".txt": ("md", md_parser.parse),
}


def build_ir(path: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix not in PARSERS:
        raise SystemExit(
            f"不支援的档案格式: {suffix}\n"
            f"支援: {', '.join(sorted(PARSERS))}"
        )
    kind, parse = PARSERS[suffix]
    units = parse(path)

    totals = {
        "words": sum(u["word_count"] for u in units),
        "images": sum(len(u["images"]) for u in units),
        "tables": sum(u["tables"] for u in units),
        "charts": sum(u["charts"] for u in units),
        "animations": sum(u.get("animations", 0) for u in units),
    }
    return {
        "source": str(path),
        "kind": kind,
        "unit_count": len(units),
        "title_chain": [u["title"] for u in units],
        "units": units,
        "totals": totals,
    }


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)

    path = Path(args[0]).expanduser()
    if not path.is_file():
        raise SystemExit(f"找不到档案: {path}")

    ir = build_ir(path)
    indent = 2 if "--pretty" in sys.argv else None
    json.dump(ir, sys.stdout, ensure_ascii=False, indent=indent)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
