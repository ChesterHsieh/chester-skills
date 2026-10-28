import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import analyze as A  # noqa: E402

FX = os.path.join(HERE, "fixtures")


def bars_from(rows):
    """rows: (high, low, close, volume)；open 用前一根收盤。"""
    out, prev = [], rows[0][2]
    for i, (h, l, c, v) in enumerate(rows):
        out.append({"date": f"d{i:03d}", "open": prev, "high": h, "low": l, "close": c, "volume": v})
        prev = c
    return out


def flat(n, px, v=100):
    return [(px * 1.01, px * 0.99, px, v)] * n


# ------------------------------------------------------------ 真實案例：SK Hynix 2026-10

def test_skhy_krx_double_bottom_breakout_without_volume():
    sym, bars = A.load(os.path.join(FX, "skhy_krx.json"))
    r = A.analyze(bars, A.Params(), sym)
    assert r.mode == "bottoming"
    assert r.pattern.startswith("破底翻")
    assert (r.one.date, r.one.price) == ("2026-10-20", 1183000)
    assert (r.x.date, r.x.price) == ("2026-10-21", 1329000)
    assert (r.three.date, r.three.price) == ("2026-10-23", 1171000)
    assert r.undercut_pct == pytest.approx(0.0101, abs=0.001)
    assert r.breakout_raw.date == "2026-10-27"
    assert r.breakout_filtered is None          # 1,341,000 < 1,329,000 × 1.01
    assert r.two_day is False
    bo = next(k for k in r.volume["key_days"] if k["name"] == "④ 突破日")
    assert bo["label"] == "無量" and bo["rvol"] == pytest.approx(0.80, abs=0.02)
    assert any("底部突破需要量" in w for w in r.warnings)
    assert "2026-10-23" in [d["date"] for d in r.reversal_days]
    gap = next(g for g in r.gaps if g["date"] == "2026-10-27")
    assert (gap["from"], gap["to"], gap["status"]) == (1315000, 1336000, "未回補")
    assert r.levels["measured"] == pytest.approx(1329000 + (1329000 - 1171000))


def test_skhy_adr_single_low_with_hurdles():
    sym, bars = A.load(os.path.join(FX, "skhy_adr.json"))
    r = A.analyze(bars, A.Params(), sym)
    assert r.mode == "bottoming"                 # 從 $124.8 漲到 $199.87 又跌到 $130.79，回吐 > 2/3
    assert (r.one.date, r.one.price) == ("2026-10-22", 130.79)
    assert r.stage == 1 and r.x is None          # 10/23 之後連漲，還沒回測
    assert r.x_provisional.price == 146.89
    assert [h.price for h in r.hurdles[:2]] == [145.97, 148.00]   # 第一關、第二關
    assert r.peak.price == 199.87
    assert r.levels["retr_33"] == pytest.approx(130.79 + (199.87 - 130.79) * 0.33)   # ≈ 153.6，不是 152


def test_skhy_adr_on_1022_was_valid_break_of_prior_low():
    """10/22 收盤跌破 10/16 低點 $136.23 超過 3%：不能被當成破底翻。"""
    sym, bars = A.load(os.path.join(FX, "skhy_adr.json"))
    r = A.analyze(bars, A.Params(), sym)
    assert not r.pattern.startswith("破底翻")
    assert (136.23 - 130.79) / 136.23 > 0.03


# ------------------------------------------------------------ 合成資料

def test_uptrend_pullback_mode():
    rows = []
    rows += [(101 + i, 99 + i, 100 + i, 100) for i in range(60)]     # 100 → 159
    rows += [(160 - i * 2, 156 - i * 2, 158 - i * 2, 120) for i in range(10)]   # 跌到約 138
    rows += [(142, 139, 141, 90)] * 3
    r = A.analyze(bars_from(rows))
    assert r.mode == "pullback"
    assert r.retr_of_upleg < 0.66


def test_single_low_then_pullback_then_breakout_same_bar():
    rows = flat(30, 200)
    rows += [(200 - i * 5, 195 - i * 5, 196 - i * 5, 150) for i in range(10)]   # 跌到 ~150
    rows += [
        (160, 149, 158, 120),   # 反彈
        (170, 157, 168, 130),   # 反彈，高點 170
        (169, 160, 162, 90),    # 收黑 → X = 170
        (166, 158, 160, 80),    # ③ 158 守住 ① 150
        (178, 159, 176, 260),   # 帶量收過 X × 1.01
        (182, 172, 180, 200),   # 兩日規則
    ]
    r = A.analyze(bars_from(rows))
    assert r.x.price == 170
    assert r.three.price == 158
    assert r.stage == 4
    assert r.breakout_filtered is not None and r.two_day
    bo = next(k for k in r.volume["key_days"] if k["name"] == "④ 突破日")
    assert bo["label"] == "帶量"
    assert not r.warnings


def test_new_low_today_is_stage_0():
    rows = flat(30, 100) + [(100 - i, 98 - i, 99 - i, 100) for i in range(8)]
    r = A.analyze(bars_from(rows))
    assert r.stage == 0


def test_event_day_raises_volume_bar():
    rows = flat(40, 100, v=100) + [(104, 99, 103, 130)]
    p = A.Params()
    bars = bars_from(rows)
    assert A.vol_label(A.rvol(bars, len(bars) - 1, p), p, event=False) == "帶量"
    assert A.vol_label(A.rvol(bars, len(bars) - 1, p), p, event=True) == "中性"


def test_rr_targets_sorted_and_tight_stop_only_after_breakout():
    sym, bars = A.load(os.path.join(FX, "skhy_krx.json"))
    r = A.analyze(bars, A.Params(), sym)
    first = [x for x in r.rr if x["entry"] == "現價" and x["stop"].startswith("結構")]
    assert [x["target_px"] for x in first] == sorted(x["target_px"] for x in first)
    assert first[0]["target"] == "型態測量目標"
    assert any(x["stop"].startswith("緊停損") for x in r.rr)


def test_render_runs():
    sym, bars = A.load(os.path.join(FX, "skhy_krx.json"))
    txt = A.render(A.analyze(bars, A.Params(), sym), A.Params())
    assert "破底翻" in txt and "報酬風險比" in txt
