"""pattern.py 的合成資料測試：不碰網路。"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from fetch import Bar  # noqa: E402
from pattern import PARAMS, analyze, band_limit, find_anchor  # noqa: E402

D0 = date(2026, 1, 1)


def bars_from(closes, vols, wiggle=0.01):
    out = []
    for i, (c, v) in enumerate(zip(closes, vols)):
        o = c * (1 - wiggle / 2) if i % 2 else c * (1 + wiggle / 2)
        out.append(Bar(D0 + timedelta(days=i), o, max(o, c) * (1 + wiggle / 2),
                       min(o, c) * (1 - wiggle / 2), c, v))
    return out


def textbook(breakout=True, final=None):
    """140 根緩漲 → 10 根急跌 25% → 30 根平台（量縮）→ 可選 1 根帶量突破。"""
    up = [80 + i * 0.25 for i in range(140)]                       # 80 → ~115
    crash = [up[-1] * (1 - 0.025 * (i + 1)) for i in range(10)]    # ~115 → ~86
    base = [92 + (2 if i % 4 < 2 else -2) for i in range(30)]       # 90~94 箱型
    closes = up + crash + base
    vols = [1000] * 140 + [2000] * 10 + [500] * 30
    if breakout:
        closes.append(99)
        vols.append(1500)
    if final is not None:
        closes.append(final)
        vols.append(600)
    return bars_from(closes, vols)


def test_textbook_breakout_matches():
    r = analyze(textbook())
    assert r["status"] == "breakout"
    assert r["base"]["bars"] >= 20
    assert r["crash"]["drop"] >= PARAMS.min_crash
    keys = {c["key"]: c["ok"] for c in r["checks"]}
    assert keys["vol_dry"] and keys["breakout_vol"] and keys["higher_low"]


def test_still_in_base_is_reported_as_base():
    r = analyze(textbook(breakout=False))
    assert r["status"].startswith("base_")
    assert r["breakout"] is None


def test_breaking_below_floor_fails():
    r = analyze(textbook(breakout=False, final=80))
    assert r["match"] == "no"


def test_steady_downtrend_has_no_pattern():
    closes = [200 * (0.995 ** i) for i in range(200)]
    r = analyze(bars_from(closes, [1000] * 200))
    assert r["match"] == "no"


def test_too_few_bars_is_insufficient():
    assert analyze(textbook()[:100])["match"] == "insufficient"


def test_anchor_is_the_crash_low_not_a_dip_inside_the_base():
    # 錨點該落在急跌段底部（index 149 附近），不是平台裡的小回檔
    bars = textbook()
    anchor = find_anchor(bars)
    assert 145 <= anchor <= 152


def test_band_limit_is_clamped():
    calm = bars_from([100.0] * 80, [1] * 80, wiggle=0.001)
    wild = bars_from([100.0 + (10 if i % 2 else -10) for i in range(80)], [1] * 80)
    assert band_limit(calm) == PARAMS.min_band
    assert band_limit(wild) == PARAMS.max_band


def test_crash_is_info_only():
    # 沒有急跌、只是長期緩漲後橫盤，只要支撐區穩定一樣可以符合
    up = [80 + i * 0.25 for i in range(150)]
    base = [118 + (1.5 if i % 4 < 2 else -1.5) for i in range(30)]
    r = analyze(bars_from(up + base, [1000] * 150 + [600] * 30))
    crash = next(c for c in r["checks"] if c["key"] == "crash")
    assert crash["kind"] == "info" and not crash["ok"]
    assert r["match"] in ("yes", "partial")


def test_entry_zone_when_close_sits_on_floor():
    r = analyze(textbook(breakout=False, final=90.5))
    assert r["status"] == "base_floor"
    assert r["entry_zone"] is (r["match"] != "no")


def test_sinking_floor_fails_support_check():
    # 平台內每根都比前一根低一點：振幅還在門檻內，但支撐越墊越低
    up = [80 + i * 0.25 for i in range(140)]
    crash = [up[-1] * (1 - 0.025 * (i + 1)) for i in range(10)]
    sink = [96 - i * 0.25 + (1 if i % 2 else -1) for i in range(30)]
    r = analyze(bars_from(up + crash + sink, [1000] * 140 + [2000] * 10 + [500] * 30))
    flat = next(c for c in r["checks"] if c["key"] == "support_flat")
    assert not flat["ok"]
