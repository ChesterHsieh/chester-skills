"""全市場逐日行情：一天兩個請求（上市＋上櫃）拿到所有股票，給整批粗掃用。

逐檔抓（fetch.py）掃 300 檔要上千個請求，證交所會擋；逐日抓不管幾檔都是固定請求數，
第一次約 230 個交易日 × 2，之後每天只補當天。過去日期（含休市日的空結果）永久快取。
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from fetch import CACHE_DIR, Bar, Series, _num, get_json

TWSE_DAY = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=ALLBUT0999&response=json"
TPEX_DAY = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={d:%Y/%m/%d}&type=EW&response=json"
GAP_SEC = {"TWSE": 0.6, "TPEx": 0.4}
DAY_CACHE = CACHE_DIR / "daily"


def _parse_twse_day(js):
    for t in js.get("tables") or []:
        if "每日收盤行情" in (t.get("title") or ""):
            out = {}
            for r in t["data"]:
                o, h, l, c, v = _num(r[5]), _num(r[6]), _num(r[7]), _num(r[8]), _num(r[2])
                if None not in (o, h, l, c):
                    out[r[0].strip()] = [o, h, l, c, (v or 0) / 1000, r[1].strip()]
            return out
    return {}


def _parse_tpex_day(js):
    tables = js.get("tables") or []
    if not tables:
        return {}
    out = {}
    for r in tables[0].get("data") or []:
        c, o, h, l, v = _num(r[2]), _num(r[4]), _num(r[5]), _num(r[6]), _num(r[7])
        if None not in (o, h, l, c):
            out[r[0].strip()] = [o, h, l, c, (v or 0) / 1000, r[1].strip()]
    return out


def _load_day(market, d, today):
    path = DAY_CACHE / f"{market}_{d:%Y%m%d}.json"
    if path.exists() and d < today:
        return json.loads(path.read_text())
    url = (TWSE_DAY if market == "TWSE" else TPEX_DAY).format(d=d)
    js = get_json(url, timeout=30)
    time.sleep(GAP_SEC[market])
    rows = _parse_twse_day(js) if market == "TWSE" else _parse_tpex_day(js)
    if d < today or rows:  # 今天還沒收盤的空結果不要快取
        DAY_CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False))
    return rows


def _weekdays(today, calendar_days):
    d = today - timedelta(days=calendar_days)
    while d <= today:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def _load_market(market, days, today, progress):
    out = []
    for i, d in enumerate(days):
        out.append((d, _load_day(market, d, today)))
        if progress and (i + 1) % 40 == 0:
            progress(f"{market} {i + 1}/{len(days)}")
    return out


def fetch_many(codes, today=None, calendar_days=270, progress=None):
    """回傳 {code: Series}；找不到的代碼不會出現在結果裡。"""
    today = today or date.today()
    days = list(_weekdays(today, calendar_days))
    with ThreadPoolExecutor(max_workers=2) as ex:  # 上市、上櫃各一條線，彼此不互相限速
        futs = {m: ex.submit(_load_market, m, days, today, progress) for m in ("TWSE", "TPEx")}
        per_market = {m: f.result() for m, f in futs.items()}

    wanted = set(codes)
    acc = {}
    for market, day_rows in per_market.items():
        for d, rows in day_rows:
            for code in wanted.intersection(rows):
                o, h, l, c, v, name = rows[code]
                entry = acc.setdefault(code, {"market": market, "name": name, "bars": []})
                entry["bars"].append(Bar(d, o, h, l, c, v))
    return {code: Series(code, e["name"], e["market"], tuple(e["bars"])) for code, e in acc.items()}
