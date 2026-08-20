"""解析 .html（含 reveal.js 式投影片与一般长页）为 IR 单元清单。只用 stdlib。"""

import re
from html.parser import HTMLParser
from pathlib import Path

from textutil import clean, finalize, make_unit

HEADINGS = {"h1", "h2", "h3"}
FLUSH_TAGS = {
    "p", "li", "td", "th", "div", "blockquote", "figcaption", "summary",
    "h4", "h5", "h6", "dt", "dd", "tr", "ul", "ol", "section", "article", "pre",
}
SKIP_TEXT = {"script", "style", "noscript"}
MEDIA_TAGS = {"img", "svg", "canvas", "video", "picture", "iframe"}
ANIM_ATTR_HINT = re.compile(r"animate|fade|slide-in|reveal|aos|parallax|marquee", re.I)
CSS_ANIM = re.compile(r"@keyframes|animation\s*:|transition\s*:", re.I)


class _Scanner(HTMLParser):
    """第一遍：数 section 与 heading，决定切分单位。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = 0
        self.headings = 0

    def handle_starttag(self, tag, attrs):
        if tag == "section":
            self.sections += 1
        elif tag in HEADINGS:
            self.headings += 1


class _Extractor(HTMLParser):
    def __init__(self, boundary: set):
        super().__init__(convert_charrefs=True)
        self.boundary = boundary
        self.units = []
        self.unit = make_unit(1)
        self._started = False
        self._skip_depth = 0
        self._svg_depth = 0
        self._heading_depth = 0
        self._buffer = []
        self.css_anim_hits = 0
        self._in_style = False

    # --- unit 生命周期 ---

    def _flush_text(self):
        text = clean("".join(self._buffer))
        self._buffer = []
        if text:
            self.unit["text_blocks"].append(text)

    def _new_unit(self):
        self._flush_text()
        # 尚未有任何内容的首个 unit 不需要切割
        if not self._started:
            self._started = True
            return
        self.units.append(finalize(self.unit))
        self.unit = make_unit(len(self.units) + 1)

    def close_all(self):
        self._flush_text()
        if self.unit["text_blocks"] or self.unit["images"] or self.unit["title"]:
            self.units.append(finalize(self.unit))
        return self.units

    # --- 事件 ---

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "style":
            self._in_style = True
        if tag in SKIP_TEXT:
            self._skip_depth += 1
            return

        if self._svg_depth:
            if tag == "svg":
                self._svg_depth += 1
            return

        if tag in self.boundary:
            self._new_unit()
            self._started = True

        if tag in HEADINGS:
            self._flush_text()
            self._heading_depth += 1

        if tag == "svg":
            self._svg_depth = 1
            self.unit["images"].append({
                "name": clean(attrs.get("aria-label", "")) or "inline-svg",
                "alt": clean(attrs.get("aria-label", "")),
                "file": "", "bytes": 0, "kind": "svg",
                "w_pt": _int(attrs.get("width")), "h_pt": _int(attrs.get("height")),
            })
        elif tag in MEDIA_TAGS:
            self.unit["images"].append({
                "name": clean(attrs.get("src", "")) or tag,
                "alt": clean(attrs.get("alt", "")),
                "file": clean(attrs.get("src", "")),
                "bytes": 0, "kind": tag,
                "w_pt": _int(attrs.get("width")), "h_pt": _int(attrs.get("height")),
            })
        elif tag == "table":
            self.unit["tables"] += 1
        elif tag == "details":
            self.unit["flags"].append("progressive_disclosure")

        blob = " ".join([attrs.get("class", ""), attrs.get("id", "")] + [
            v for k, v in attrs.items() if k.startswith("data-")
        ])
        if ANIM_ATTR_HINT.search(blob):
            self.unit["animations"] += 1

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False
        if tag in SKIP_TEXT:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._svg_depth:
            if tag == "svg":
                self._svg_depth -= 1
            return
        if tag in HEADINGS and self._heading_depth:
            self._heading_depth -= 1
            text = clean("".join(self._buffer))
            self._buffer = []
            if text and not self.unit["title"]:
                self.unit["title"] = text
            elif text:
                self.unit["text_blocks"].append(text)
        elif tag in FLUSH_TAGS:
            self._flush_text()

    def handle_data(self, data):
        if self._in_style:
            self.css_anim_hits += len(CSS_ANIM.findall(data))
        if self._skip_depth or self._svg_depth:
            return
        self._buffer.append(data)


def _int(value) -> int:
    try:
        return int(re.sub(r"[^0-9]", "", str(value or "")) or 0)
    except ValueError:
        return 0


def parse(path: Path) -> list:
    raw = path.read_text(encoding="utf-8", errors="replace")

    scanner = _Scanner()
    scanner.feed(raw)
    # 投影片式 HTML 用 <section> 切；一般长页用标题切
    boundary = {"section"} if scanner.sections >= 3 else HEADINGS

    extractor = _Extractor(boundary)
    extractor.feed(raw)
    units = extractor.close_all()

    if units and extractor.css_anim_hits:
        # CSS 动画属于整份文件，无法归属到单页；标记后交由人工确认是否承载信息
        units[0]["flags"].append(f"css_animation_rules:{extractor.css_anim_hits}")

    return units or [finalize(make_unit(1))]
