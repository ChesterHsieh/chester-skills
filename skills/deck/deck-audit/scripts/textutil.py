"""解析器共用的文字工具。"""

import re

_CJK = re.compile(r"[㐀-䶿一-鿿぀-ヿ가-힯]")
_LATIN_WORD = re.compile(r"[A-Za-z0-9]+(?:[-'’][A-Za-z0-9]+)*")


def count_words(text: str) -> int:
    """中日韩字符逐字计，拉丁文逐词计。混排内容也能得到合理估算。"""
    if not text:
        return 0
    return len(_CJK.findall(text)) + len(_LATIN_WORD.findall(text))


def clean(text: str) -> str:
    """压缩空白，去除首尾空格。"""
    return re.sub(r"\s+", " ", text or "").strip()


def make_unit(index: int, title: str = "") -> dict:
    """建立一个空的 IR 单元（一页 / 一节）。"""
    return {
        "index": index,
        "title": clean(title),
        "text_blocks": [],
        "word_count": 0,
        "images": [],
        "tables": 0,
        "charts": 0,
        "animations": 0,
        "notes": "",
        "flags": [],
    }


def finalize(unit: dict) -> dict:
    """收尾：算字数、补 flags。标题不计入正文字数。"""
    unit["text_blocks"] = [b for b in (clean(b) for b in unit["text_blocks"]) if b]
    unit["word_count"] = sum(count_words(b) for b in unit["text_blocks"])

    if not unit["title"]:
        unit["title"] = "(无标题)"
        unit["flags"].append("no_title")
    if unit["word_count"] == 0 and not unit["images"]:
        unit["flags"].append("empty")
    if unit["word_count"] > 120:
        unit["flags"].append("text_heavy")
    if len(unit["images"]) > 3:
        unit["flags"].append("image_dense")
    return unit
