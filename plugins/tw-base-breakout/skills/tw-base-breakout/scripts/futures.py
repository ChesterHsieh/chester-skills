"""個股期貨資訊（期交所 OpenAPI）：哪些股票有個股期貨、一般／小型、原始保證金比例。

一般股票期貨 1 口 = 2,000 股，小型 = 100 股。清單每天快取一次。
"""
import json
from dataclasses import dataclass
from datetime import date

from fetch import CACHE_DIR, get_json

LIST_URL = "https://openapi.taifex.com.tw/v1/SSFLists"
MARGIN_URL = "https://openapi.taifex.com.tw/v1/SingleStockFuturesMargining"
SHARES = {"一般": 2000, "小型": 100}


@dataclass(frozen=True)
class Contract:
    code: str          # 期貨商品代碼，例如 CDF
    kind: str          # 一般 | 小型
    initial_margin_rate: float | None

    def margin(self, price: float) -> float | None:
        if self.initial_margin_rate is None:
            return None
        return price * SHARES[self.kind] * self.initial_margin_rate


def _cached(url: str, name: str, today: date):
    path = CACHE_DIR / f"{name}_{today:%Y%m%d}.json"
    if path.exists():
        return json.loads(path.read_text())
    data = get_json(url, headers={"accept": "application/json"})
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))
    return data


def _rate(s):
    try:
        return float(str(s).rstrip("%")) / 100
    except ValueError:
        return None


def load_contracts(today: date | None = None) -> dict:
    """回傳 {股票代碼: [Contract, ...]}，只含普通股標的（排除 ETF）。"""
    today = today or date.today()
    listing = _cached(LIST_URL, "ssf_list", today)
    margins = {m["Contract"]: m for m in _cached(MARGIN_URL, "ssf_margin", today)}
    out = {}
    for row in listing:
        if "普通股" not in row.get("Type", ""):
            continue
        m = margins.get(row["Contract"], {})
        kind = "小型" if m.get("ContractName", "").startswith("小型") else "一般"
        out.setdefault(row["StockCode"], []).append(
            Contract(row["Contract"], kind, _rate(m.get("InitialMarginRate"))))
    return out


def describe(contracts: list, price: float) -> str:
    if not contracts:
        return "無個股期貨"
    parts = []
    for c in sorted(contracts, key=lambda c: c.kind):
        m = c.margin(price)
        parts.append(f"{c.kind}（{c.code}）" + (f" 保證金≈{m:,.0f}" if m else ""))
    return "／".join(parts)
