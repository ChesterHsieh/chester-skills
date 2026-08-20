"""解析 .md / .txt（含 Marp、reveal.js markdown）为 IR 单元清单。只用 stdlib。"""

import re
from pathlib import Path

from textutil import clean, finalize, make_unit

HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
SLIDE_BREAK = re.compile(r"^\s*---\s*$")
MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)")
MD_IMAGE_FULL = re.compile(r"!\[[^\]]*\]\([^)]*\)")
HTML_IMAGE = re.compile(r"<(img|svg|canvas|video)\b[^>]*>", re.I)
TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]*-{3,}[\s:|-]*\|")
FENCE = re.compile(r"^\s*(```|~~~)")


def _strip_frontmatter(lines: list) -> list:
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[i + 1:]
    return lines


def parse(path: Path) -> list:
    lines = _strip_frontmatter(path.read_text(encoding="utf-8", errors="replace").splitlines())

    # 独立的 `---` 分隔线达 3 条以上，视为 Marp/reveal 式分页
    breaks = sum(1 for ln in lines if SLIDE_BREAK.match(ln))
    page_mode = breaks >= 3

    units = []
    unit = make_unit(1)
    in_fence = False
    in_table = False

    def push():
        nonlocal unit
        if unit["text_blocks"] or unit["images"] or unit["title"]:
            units.append(finalize(unit))
        unit = make_unit(len(units) + 1)

    for line in lines:
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        if page_mode and SLIDE_BREAK.match(line):
            push()
            continue

        heading = HEADING.match(line)
        if heading:
            if not page_mode:
                push()
            if not unit["title"]:
                unit["title"] = heading.group(2)
            else:
                unit["text_blocks"].append(heading.group(2))
            continue

        for alt, src in MD_IMAGE.findall(line):
            unit["images"].append({
                "name": src, "alt": clean(alt), "file": src,
                "bytes": 0, "kind": "img", "w_pt": 0, "h_pt": 0,
            })
        for tag in HTML_IMAGE.findall(line):
            unit["images"].append({
                "name": tag.lower(), "alt": "", "file": "",
                "bytes": 0, "kind": tag.lower(), "w_pt": 0, "h_pt": 0,
            })

        if TABLE_SEP.match(line):
            if not in_table:
                unit["tables"] += 1
                in_table = True
            continue
        if not line.strip():
            in_table = False

        text = MD_IMAGE_FULL.sub("", line)
        text = re.sub(r"^\s*[-*+]\s+|^\s*\d+\.\s+|^\s*>\s?", "", text)
        text = re.sub(r"[*_`]{1,3}", "", text)
        if text.lstrip().startswith("|"):
            text = text.replace("|", " ")  # 表格列内容保留，去掉栏位分隔符
        if clean(text):
            unit["text_blocks"].append(text)

    push()
    return units or [finalize(make_unit(1))]
