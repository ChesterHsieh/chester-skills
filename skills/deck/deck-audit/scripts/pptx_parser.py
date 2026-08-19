"""解析 .pptx / .potx（OOXML）为 IR 单元清单。只用 stdlib。"""

import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from textutil import clean, finalize, make_unit

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
}

EMU_PER_PT = 12700
TITLE_PLACEHOLDERS = {"title", "ctrTitle"}
GRAPHIC_URI = {
    "table": "http://schemas.openxmlformats.org/drawingml/2006/table",
    "chart": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "diagram": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
}
ANIM_TAGS = {"anim", "animEffect", "animMotion", "animRot", "animScale", "animClr"}


def q(prefix: str, tag: str) -> str:
    return f"{{{NS[prefix]}}}{tag}"


def _resolve(base_part: str, target: str) -> str:
    """把 rels 里的相对 Target 正规化成 zip 内的完整路径。"""
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_part), target))


def _read_rels(zf: zipfile.ZipFile, part: str) -> dict:
    """回传 {rId: {"type": ..., "path": ...}}。"""
    rels_path = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    if rels_path not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read(rels_path))
    out = {}
    for rel in root.findall(q("pkg", "Relationship")):
        rid = rel.get("Id")
        target = rel.get("Target", "")
        if not rid or target.startswith("http"):
            continue
        out[rid] = {
            "type": rel.get("Type", "").rsplit("/", 1)[-1],
            "path": _resolve(part, target),
        }
    return out


def _slide_parts(zf: zipfile.ZipFile) -> list:
    """依 presentation.xml 的 sldIdLst 顺序回传 slide part 路径。"""
    pres = "ppt/presentation.xml"
    if pres not in zf.namelist():
        # .potx 之类可能没有 presentation.xml，退回档名排序
        return sorted(
            n for n in zf.namelist()
            if n.startswith("ppt/slides/slide") and n.endswith(".xml")
        )
    rels = _read_rels(zf, pres)
    root = ET.fromstring(zf.read(pres))
    parts = []
    for sld_id in root.iter(q("p", "sldId")):
        rid = sld_id.get(q("r", "id"))
        if rid in rels:
            parts.append(rels[rid]["path"])
    return parts


def _para_texts(tx_body) -> list:
    """把 a:p 各段落抽成字串清单（保留段落切分，因为它对应 bullet）。"""
    out = []
    for para in tx_body.findall(q("a", "p")):
        text = "".join(t.text or "" for t in para.iter(q("a", "t")))
        text = clean(text)
        if text:
            out.append(text)
    return out


def _shape_top(shape) -> int:
    off = shape.find(f'{q("p", "spPr")}/{q("a", "xfrm")}/{q("a", "off")}')
    if off is None:
        return 10**9  # 无版面资讯者排到最后
    try:
        return int(off.get("y", 10**9))
    except ValueError:
        return 10**9


def _collect(node, unit: dict, rels: dict, media_sizes: dict, text_shapes: list) -> None:
    """递归走 spTree，把文字／图片／表格／图表收进 unit。"""
    for child in node:
        tag = child.tag

        if tag == q("p", "grpSp"):
            _collect(child, unit, rels, media_sizes, text_shapes)

        elif tag == q("p", "sp"):
            tx_body = child.find(q("p", "txBody"))
            if tx_body is None:
                continue
            paras = _para_texts(tx_body)
            if not paras:
                continue
            ph = child.find(f'{q("p", "nvSpPr")}/{q("p", "nvPr")}/{q("p", "ph")}')
            ph_type = ph.get("type", "body") if ph is not None else None
            text_shapes.append({
                "paras": paras,
                "ph_type": ph_type,
                "top": _shape_top(child),
            })

        elif tag == q("p", "pic"):
            unit["images"].append(_read_pic(child, rels, media_sizes))

        elif tag == q("p", "graphicFrame"):
            data = child.find(f'{q("a", "graphic")}/{q("a", "graphicData")}')
            uri = data.get("uri", "") if data is not None else ""
            if uri == GRAPHIC_URI["table"]:
                unit["tables"] += 1
                for cell_text in child.iter(q("a", "t")):
                    if cell_text.text:
                        unit["text_blocks"].append(cell_text.text)
            elif uri in (GRAPHIC_URI["chart"], GRAPHIC_URI["diagram"]):
                unit["charts"] += 1
                if uri == GRAPHIC_URI["diagram"]:
                    unit["flags"].append("smartart")


def _read_pic(pic, rels: dict, media_sizes: dict) -> dict:
    c_nv = pic.find(f'{q("p", "nvPicPr")}/{q("p", "cNvPr")}')
    blip = pic.find(f'{q("p", "blipFill")}/{q("a", "blip")}')
    ext = pic.find(f'{q("p", "spPr")}/{q("a", "xfrm")}/{q("a", "ext")}')

    file_name, size_bytes = "", 0
    if blip is not None:
        rid = blip.get(q("r", "embed"))
        rel = rels.get(rid)
        if rel:
            file_name = posixpath.basename(rel["path"])
            size_bytes = media_sizes.get(rel["path"], 0)

    def _pt(attr):
        if ext is None:
            return 0
        try:
            return round(int(ext.get(attr, 0)) / EMU_PER_PT)
        except ValueError:
            return 0

    return {
        "name": (c_nv.get("name", "") if c_nv is not None else ""),
        "alt": clean(c_nv.get("descr", "") if c_nv is not None else ""),
        "file": file_name,
        "bytes": size_bytes,
        "w_pt": _pt("cx"),
        "h_pt": _pt("cy"),
    }


def _pick_title(text_shapes: list) -> tuple:
    """回传 (title, remaining_shapes)。优先 title placeholder，其次版面最上方的文字。"""
    for i, shape in enumerate(text_shapes):
        if shape["ph_type"] in TITLE_PLACEHOLDERS:
            return shape["paras"][0], text_shapes[:i] + text_shapes[i + 1:]

    if not text_shapes:
        return "", []
    topmost = min(range(len(text_shapes)), key=lambda i: text_shapes[i]["top"])
    shape = text_shapes[topmost]
    # 版面最上方但内容像正文（多段或过长）就不当标题
    if len(shape["paras"]) > 1 or len(shape["paras"][0]) > 60:
        return "", text_shapes
    return shape["paras"][0], text_shapes[:topmost] + text_shapes[topmost + 1:]


def _count_animations(root) -> int:
    timing = root.find(q("p", "timing"))
    if timing is None:
        return 0
    return sum(1 for el in timing.iter() if el.tag.split("}")[-1] in ANIM_TAGS)


def parse(path: Path) -> list:
    units = []
    with zipfile.ZipFile(path) as zf:
        media_sizes = {i.filename: i.file_size for i in zf.infolist()}

        for index, part in enumerate(_slide_parts(zf), start=1):
            if part not in zf.namelist():
                continue
            root = ET.fromstring(zf.read(part))
            rels = _read_rels(zf, part)
            unit = make_unit(index)

            tree = root.find(f'{q("p", "cSld")}/{q("p", "spTree")}')
            text_shapes = []
            if tree is not None:
                _collect(tree, unit, rels, media_sizes, text_shapes)

            title, body_shapes = _pick_title(text_shapes)
            unit["title"] = title
            for shape in body_shapes:
                unit["text_blocks"].extend(shape["paras"])

            unit["animations"] = _count_animations(root)
            unit["notes"] = _read_notes(zf, rels)
            units.append(finalize(unit))

    return units


def _read_notes(zf: zipfile.ZipFile, rels: dict) -> str:
    for rel in rels.values():
        if rel["type"] != "notesSlide" or rel["path"] not in zf.namelist():
            continue
        root = ET.fromstring(zf.read(rel["path"]))
        parts = []
        for sp in root.iter(q("p", "sp")):
            ph = sp.find(f'{q("p", "nvSpPr")}/{q("p", "nvPr")}/{q("p", "ph")}')
            # 跳过投影片编号等非讲者备注的 placeholder
            if ph is not None and ph.get("type") in {"sldNum", "dt", "ftr"}:
                continue
            tx_body = sp.find(q("p", "txBody"))
            if tx_body is not None:
                parts.extend(_para_texts(tx_body))
        return clean(" ".join(parts))
    return ""
