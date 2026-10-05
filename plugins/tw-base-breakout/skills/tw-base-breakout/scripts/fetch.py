"""抓台股日線：先試上市（TWSE），沒有再試上櫃（TPEx）。只用標準函式庫。

已收完的月份快取在 ~/.cache/tw-base-breakout/，當月每次重抓。
成交量一律換算成「張」（TWSE 回傳股數，TPEx 回傳張數）。
價格是原始收盤價，未做除權息還原。
"""
import json
import time
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "tw-base-breakout"
TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={y}{m:02d}01&stockNo={code}&response=json"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code={code}&date={y}/{m:02d}/01&response=json"
REQUEST_GAP_SEC = 0.35  # TWSE 對連續請求會擋，留點間隔
UA = {"User-Agent": "Mozilla/5.0 (tw-base-breakout skill)"}


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float  # 張


@dataclass(frozen=True)
class Series:
    code: str
    name: str
    market: str  # "TWSE" | "TPEx"
    bars: tuple


def _num(s):
    """數字字串轉 float；無成交的各種記號（--、----、X0.00、空字串）一律回傳 None。"""
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def _roc_to_date(s):
    y, m, d = (int(x) for x in s.strip().split("/"))
    return date(y + 1911, m, d)


RETRIES = 3


def get_json(url, headers=None, timeout=20):
    """GET JSON，逾時或連線錯誤重試 RETRIES 次（間隔遞增），最後一次的錯誤往外丟。"""
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except (OSError, json.JSONDecodeError):
            if attempt == RETRIES - 1:
                raise
            time.sleep(2 * (attempt + 1))


def _parse_twse(js):
    if js.get("stat") != "OK":
        return None, []
    name = js.get("title", "").split()[2] if len(js.get("title", "").split()) > 2 else ""
    rows = []
    for r in js.get("data", []):
        o, h, l, c, v = _num(r[3]), _num(r[4]), _num(r[5]), _num(r[6]), _num(r[1])
        if None in (o, h, l, c):
            continue  # 當日無成交
        rows.append(Bar(_roc_to_date(r[0]), o, h, l, c, (v or 0) / 1000))
    return name, rows


def _parse_tpex(js):
    tables = js.get("tables") or []
    if not tables or not tables[0].get("data"):
        return None, []
    sub = tables[0].get("subtitle", "").split()
    name = sub[1] if len(sub) > 2 else ""
    rows = []
    for r in tables[0]["data"]:
        o, h, l, c, v = _num(r[3]), _num(r[4]), _num(r[5]), _num(r[6]), _num(r[1])
        if None in (o, h, l, c):
            continue
        rows.append(Bar(_roc_to_date(r[0]), o, h, l, c, v or 0))
    return name, rows


def _month_list(months, today):
    y, m = today.year, today.month
    out = []
    for _ in range(months):
        out.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return list(reversed(out))


def _cache_path(market, code, y, m):
    return CACHE_DIR / f"{market}_{code}_{y}{m:02d}.json"


def _fetch_month(market, code, y, m, is_current):
    path = _cache_path(market, code, y, m)
    if path.exists() and not is_current:
        return json.loads(path.read_text())
    url = (TWSE_URL if market == "TWSE" else TPEX_URL).format(code=code, y=y, m=m)
    js = get_json(url)
    time.sleep(REQUEST_GAP_SEC)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(js, ensure_ascii=False))
    return js


def _detect_market(code, today):
    """用當月（月初沒資料就前一個月）判斷上市或上櫃。"""
    for y, m in reversed(_month_list(2, today)):
        is_cur = (y, m) == (today.year, today.month)
        name, rows = _parse_twse(_fetch_month("TWSE", code, y, m, is_cur))
        if rows:
            return "TWSE"
        name, rows = _parse_tpex(_fetch_month("TPEx", code, y, m, is_cur))
        if rows:
            return "TPEx"
    raise ValueError(f"找不到 {code} 的上市或上櫃日線資料，確認代碼是否正確（ETF、權證、興櫃不支援）")


def fetch_series(code, months=13, today=None):
    today = today or date.today()
    market = _detect_market(code, today)
    parse = _parse_twse if market == "TWSE" else _parse_tpex
    name, bars = "", []
    for y, m in _month_list(months, today):
        is_cur = (y, m) == (today.year, today.month)
        try:
            n, rows = parse(_fetch_month(market, code, y, m, is_cur))
        except Exception as e:  # 單月失敗不讓整體失敗，但要讓呼叫端知道
            raise RuntimeError(f"抓 {code} {y}-{m:02d} 失敗：{e}") from e
        name = n or name
        bars.extend(rows)
    if not bars:
        raise ValueError(f"{code} 沒有日線資料")
    return Series(code=code, name=name, market=market, bars=tuple(bars))
