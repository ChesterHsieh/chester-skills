#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能树产生器 + 验证器。

用法:  python3 build.py
读取:  tree_data.py  (META, N, Q)
产生:  <slug>.html —— 单一自足档案，直接拿去 Artifact 发布
       skill-tree.json —— 副产物，除错用，不必交付

验证失败会 exit 1 —— 这就是 SKILL.md Step 7 的闸门，别绕过。
"""
import json, pathlib, sys, collections, re, statistics

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
try:
    from tree_data import META, N, Q  # noqa: E402
except ImportError as e:
    if "Q" in str(e):
        print("❌ tree_data.py 缺少 Q（题库）。这个 skill 的产出必须有两大区块：")
        print("   技能树 + 试炼题库。若真的不要题库，写 Q = [] 明示，")
        print("   并把 META['quiz_gate'] 设成 False。")
        sys.exit(1)
    raise

LET = "ABCD"
SELF_DISPARAGE = re.compile(r"(錯|错|混淆|反了|太|少了|只是|無關|无关|完全)")


def validate_tree(nodes):
    errs, warns = [], []
    ids = [n["id"] for n in nodes]
    for i, c in collections.Counter(ids).items():
        if c > 1:
            errs.append(f"重复 id: {i}")
    idset, acts = set(ids), {a["id"] for a in META["acts"]}
    by = {n["id"]: n for n in nodes}
    for n in nodes:
        for d in n["deps"]:
            if d not in idset:
                errs.append(f"{n['id']} 的前置 {d} 不存在")
            elif by[d]["act"] == n["act"] and by[d].get("y", 0) > n.get("y", 0):
                warns.append(f"{n['id']} 的前置 {d} 画在它下面，视觉上会逆流")
        if n["act"] not in acts:
            errs.append(f"{n['id']} 的 act {n['act']} 不存在")
        if not n.get("tasks"):
            errs.append(f"{n['id']} 没有任务清单")
        elif not 3 <= len(n["tasks"]) <= 8:
            warns.append(f"{n['id']} 任务 {len(n['tasks'])} 条（建议 3–8）")
        for f in ("desc", "why", "dod"):
            if not n.get(f):
                errs.append(f"{n['id']} 缺 {f}")
        if re.match(r"^(理解|熟悉|了解|認識|认识)", n.get("dod", "")):
            errs.append(f"{n['id']} 的 dod 不可判定：{n['dod'][:30]}")
        if not n.get("res"):
            warns.append(f"{n['id']} 没有资源连结")
    seen, stack = set(), set()

    def dfs(i):
        if i in stack:
            errs.append(f"依赖循环: {i}"); return
        if i in seen:
            return
        stack.add(i); seen.add(i)
        for d in by[i]["deps"]:
            if d in by:
                dfs(d)
        stack.discard(i)
    for i in ids:
        dfs(i)
    cross = [f"{d}->{n['id']}" for n in nodes for d in n["deps"]
             if d in by and by[d]["act"] != n["act"]]
    if len(cross) < 3:
        warns.append(f"跨章依赖只有 {len(cross)} 条——这是几根平行棍子，不是一棵树")
    for a in META["acts"]:
        ns = [n for n in nodes if n["act"] == a["id"]]
        if ns and not any(n["type"] == "keystone" for n in ns):
            warns.append(f"{a['id']} {a['name']} 没有 keystone（枢纽节点）")
    return errs, warns


def validate_quiz(nodes, quiz):
    """四项机械检查。定性原则挡不住这些反模式，必须量。"""
    errs, warns = [], []
    idset = {n["id"] for n in nodes}
    per = collections.Counter()
    for i, q in enumerate(quiz, 1):
        tag = f"q{i}"
        if q["n"] not in idset:
            errs.append(f"{tag} 挂在不存在的节点 {q['n']}")
        if len(q.get("o", [])) != 4:
            errs.append(f"{tag} 不是四选一")
            continue
        if not 0 <= q.get("a", -1) <= 3:
            errs.append(f"{tag} answer 索引越界")
            continue
        if not q.get("ex"):
            errs.append(f"{tag} 缺 explain")
        for k in ("s", "m", "t", "p"):
            if not q.get("dig", {}).get(k):
                errs.append(f"{tag} dig.{k} 缺失")
        if re.search(r"以上皆|都對|都对|都不對|都不对", "".join(q["o"])):
            errs.append(f"{tag} 出现偷懒选项")
        # 解释自打脸：正解字母不该出现在「X 错」句式里
        c = LET[q["a"]]
        m = re.search(r"(?<![A-Za-z0-9-])" + c + r" ?(.{0,4})", q.get("ex", ""))
        if m and SELF_DISPARAGE.search(m.group(1)):
            errs.append(f"{tag} 解释里说「{c} {m.group(1).strip()}」，但 {c} 就是正解")
        per[q["n"]] += 1

    for n in nodes:
        if per[n["id"]] < 3:
            errs.append(f"{n['id']} 只有 {per[n['id']]} 题（每节点至少 3 题）")
    if len(quiz) < 60:
        warns.append(f"全树只有 {len(quiz)} 题，低于 60 就退化成一张海报")

    # 答案位置分布
    dist = [sum(1 for q in quiz if q.get("a") == i) for i in range(4)]
    if quiz and max(dist) - min(dist) > max(2, len(quiz) * 0.05):
        errs.append(f"答案位置分布失衡 {dist}——选同一个字母就能拿分")

    # 长度泄漏
    def plain(s):
        return len(re.sub(r"<[^>]+>", "", s))
    margins = []
    for q in quiz:
        if len(q.get("o", [])) != 4 or not 0 <= q.get("a", -1) <= 3:
            continue
        L = [plain(o) for o in q["o"]]
        margins.append(L[q["a"]] / max(L[j] for j in range(4) if j != q["a"]))
    if margins:
        med, mx = statistics.median(margins), max(margins)
        bad = sum(1 for m in margins if m > 1.30)
        if med > 1.10 or bad:
            errs.append(f"长度泄漏答案：中位领先 {med:.2f}（要 ≤1.10）、"
                        f"最大 {mx:.2f}、{bad} 题超过 1.30。"
                        f"把正解压短，或把干扰项补到同等术语密度")

    # dig 重复
    digs = [tuple(q.get("dig", {}).get(k, "") for k in "smtp") for q in quiz]
    if len(set(digs)) != len(digs):
        errs.append(f"dig prompt 重复 {len(digs)-len(set(digs))} 组——同一段模板复制多次等于没写")
    return errs, warns


def main():
    errs, warns = validate_tree(N)
    qe, qw = validate_quiz(N, Q)
    errs += qe; warns += qw
    for w in warns:
        print("⚠️ ", w)
    if errs:
        print("\n❌ 验证失败：")
        for e in errs:
            print("  -", e)
        sys.exit(1)

    total = sum(n["hours"] for n in N)
    main_h = sum(n["hours"] for n in N if n["track"] == "main")
    meta = dict(META)
    meta.update(total_hours=total, main_hours=main_h, side_hours=total - main_h,
                node_count=len(N), quiz_count=len(Q))
    payload = {"meta": meta, "nodes": N, "quiz": Q}

    out = HERE / (meta.get("slug", "skill-tree") + ".html")
    tpl = (HERE / "index.template.html").read_text(encoding="utf-8")
    html = (tpl.replace("/*__TREE_JSON__*/", json.dumps(payload, ensure_ascii=False))
               .replace("{{TITLE}}", meta.get("title", "技能樹")))
    out.write_text(html, encoding="utf-8")
    (HERE / "skill-tree.json").write_text(   # 副产物，除错用；不必交付
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    wk = meta.get("weekly_hours", 10)
    dist = [sum(1 for q in Q if q.get("a") == i) for i in range(4)]
    print(f"✅ 产生完成 → {out.name}  ({out.stat().st_size//1024} KB)")
    print(f"   节点   : {len(N)}  (主线 {sum(1 for n in N if n['track']=='main')} / 支线 {sum(1 for n in N if n['track']=='side')})")
    print(f"   题目   : {len(Q)}   答案分布 A/B/C/D = {'/'.join(map(str,dist))}")
    print(f"   时数   : {total} h   主线 {main_h} h → {main_h/wk:.0f} 週 @ {wk}h/週")
    print()
    for a in META["acts"]:
        ns = [n for n in N if n["act"] == a["id"]]
        if not ns:
            continue
        qn = sum(1 for q in Q if q["n"] in {n["id"] for n in ns})
        ks = sum(1 for n in ns if n["type"] == "keystone")
        print(f"   {a['id']:<4} {a['name']:<20} {len(ns):>2} 节点 {sum(n['hours'] for n in ns):>3} h "
              f"{qn:>3} 题  ◆{ks}")


if __name__ == "__main__":
    main()
