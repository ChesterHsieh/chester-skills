import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import global_volume as G  # noqa: E402

FX = os.path.join(HERE, "fixtures")


def load_pair():
    return G.load(os.path.join(FX, "skhy_adr.json")), G.load(os.path.join(FX, "skhy_krx.json"))


def test_skhy_global_rvol_with_official_ratio():
    adr, krx = load_pair()
    res = G.compute(adr, krx, 0.1)
    row = {r["date"]: r for r in res["rows"]}
    # 10/27：ADR 帶量、原股無量，合起來中性
    assert row["2026-10-27"]["adr_rvol"] == pytest.approx(1.32, abs=0.02)
    assert row["2026-10-27"]["home_rvol"] == pytest.approx(0.80, abs=0.02)
    assert row["2026-10-27"]["label"] == "中性"
    assert row["2026-10-26"]["label"] == "無量"
    assert 0.15 < res["adr_share"] < 0.35
    assert res["down_avg"] > res["up_avg"]      # 10/14–16 的下跌量還壓著


def test_estimate_k_uses_recent_days_only():
    adr, krx = load_pair()
    k, n = G.estimate_k(adr, krx, fx=925.0)
    assert n == 10
    # 用 925 這個匯率反推，最近 10 天的 k 會接近官方的 0.1
    assert k == pytest.approx(0.1, abs=0.005)


def test_event_threshold():
    adr = {f"2026-01-{d:02d}": {"close": 10.0, "volume": 100.0} for d in range(1, 22)}
    home = {f"2026-01-{d:02d}": {"close": 100.0, "volume": 100.0} for d in range(1, 22)}
    adr["2026-01-22"] = {"close": 11.0, "volume": 130.0}
    home["2026-01-22"] = {"close": 110.0, "volume": 130.0}
    assert G.compute(adr, home, 1.0)["rows"][-1]["label"] == "帶量"
    assert G.compute(adr, home, 1.0, event_last=True)["rows"][-1]["label"] == "中性"


def test_estimate_k_needs_overlap():
    with pytest.raises(ValueError):
        G.estimate_k({"2026-01-01": {"close": 1, "volume": 1}}, {"2026-01-02": {"close": 1, "volume": 1}}, 1.0)
