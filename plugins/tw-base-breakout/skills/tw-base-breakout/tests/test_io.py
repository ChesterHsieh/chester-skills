"""不碰網路的 I/O 小工具測試。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from fetch import _num  # noqa: E402
from futures import Contract, describe  # noqa: E402
from scan import parse_codes  # noqa: E402


def test_num_handles_no_trade_marks():
    assert _num("1,234.5") == 1234.5
    for s in ("--", "----", "", "X0.00", "除權息"):
        assert _num(s) is None


def test_parse_codes_accepts_mixed_separators_and_comments():
    assert parse_codes(["2330,5483", "3673 2330", "2409、8299  # 面板 記憶體\n2317"]) == \
        ["2330", "5483", "3673", "2409", "8299", "2317"]


def test_futures_margin_uses_contract_size():
    normal, mini = Contract("CDF", "一般", 0.135), Contract("QFF", "小型", 0.135)
    assert normal.margin(2500) == 2500 * 2000 * 0.135
    assert mini.margin(2500) == 2500 * 100 * 0.135
    assert describe([], 100) == "無個股期貨"
    assert "小型（QFF）" in describe([normal, mini], 2500)
