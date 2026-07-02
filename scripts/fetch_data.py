"""
台股期貨籌碼儀表板 - 每日資料抓取腳本
資料來源（皆為官方公開資料，免金鑰）：
  - 臺灣證券交易所 OpenAPI: https://openapi.twse.com.tw/v1/
  - 臺灣期貨交易所 政府開放資料: https://www.taifex.com.tw/data_gov/taifex_open_data.asp

設計原則：
  - 每個資料來源各自用 try/except 包起來，單一來源失敗不會讓整個腳本掛掉
  - 找不到預期欄位時，用「關鍵字模糊比對」找最接近的欄位名稱，並把所有欄位名稱寫進 log，
    方便之後比對、修正
  - 原始 CSV 也另存一份到 data/raw/，萬一解析錯誤，之後好除錯
"""

import io
import json
import os
import re
import traceback
from datetime import datetime, timezone

import pandas as pd
import requests

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
RAW_DIR = os.path.join(OUT_DIR, "raw")
HISTORY_PATH = os.path.join(OUT_DIR, "history.json")
LATEST_PATH = os.path.join(OUT_DIR, "latest.json")
LOG_PATH = os.path.join(OUT_DIR, "fetch_log.txt")

LOG_LINES = []


def log(msg):
    print(msg)
    LOG_LINES.append(str(msg))


def safe(fn, name):
    try:
        return fn()
    except Exception as e:
        log(f"[ERROR] {name}: {e}")
        log(traceback.format_exc())
        return None


# ---------------------------------------------------------------------------
# 共用小工具
# ---------------------------------------------------------------------------

def to_float(x, default=0.0):
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except Exception:
        return default


def find_col(df, *keywords):
    """在 DataFrame 的欄位名稱中，找出包含任一關鍵字的欄位"""
    for col in df.columns:
        col_str = str(col)
        for kw in keywords:
            if kw in col_str:
                return col
    return None


def twse_date_to_iso(s):
    """TWSE 日期可能是 8 位西元(YYYYMMDD) 或 7 位民國(YYYMMDD)"""
    s = re.sub(r"\D", "", str(s))
    if len(s) == 8:
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    if len(s) == 7:
        roc = int(s[0:3])
        return f"{roc + 1911}-{s[3:5]}-{s[5:7]}"
    return s


def roc_date_to_iso(s):
    """TAIFEX 開放資料常見 yyy/mm/dd 民國年格式，例如 115/06/25"""
    s = str(s).strip()
    m = re.match(r"(\d{2,3})[/-](\d{1,2})[/-](\d{1,2})", s)
    if m:
        roc, mo, da = m.groups()
        return f"{int(roc) + 1911}-{int(mo):02d}-{int(da):02d}"
    m = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if m:
        y, mo, da = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(da):02d}"
    return s


# ---------------------------------------------------------------------------
# 資料來源 1：TWSE OpenAPI
# ---------------------------------------------------------------------------

def fetch_twse_json(endpoint):
    url = f"https://openapi.twse.com.tw/v1/{endpoint}"
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.json()


def get_weighted_index():
    data = fetch_twse_json("exchangeReport/FMTQIK")
    df = pd.DataFrame(data)
    if df.empty:
        raise ValueError("FMTQIK 回傳空資料")
    date_col = find_col(df, "日期")
    close_col = find_col(df, "加權股價指數")
    chg_col = find_col(df, "漲跌點數", "漲跌指數")
    turnover_col = find_col(df, "成交金額")
    last = df.iloc[-1]
    close = to_float(last[close_col])
    change = to_float(last[chg_col])
    prev_close = close - change
    return {
        "date": twse_date_to_iso(last[date_col]),
        "close": round(close, 2),
        "change": round(change, 2),
        "change_pct": round(change / prev_close * 100, 2) if prev_close else None,
        "turnover_billion": round(to_float(last[turnover_col]) / 1e8, 2),
    }


def get_institutional_spot():
    data = fetch_twse_json("exchangeReport/BFIAUU")
    df = pd.DataFrame(data)
    if df.empty:
        raise ValueError("BFIAUU 回傳空資料")
    date_col = find_col(df, "日期")
    name_col = find_col(df, "單位名稱")
    buy_col = find_col(df, "買進金額")
    sell_col = find_col(df, "賣出金額")
    last_date = df[date_col].iloc[-1]
    today = df[df[date_col] == last_date]

    def net(keyword):
        rows = today[today[name_col].astype(str).str.contains(keyword)]
        buy = rows[buy_col].apply(to_float).sum()
        sell = rows[sell_col].apply(to_float).sum()
        return round((buy - sell) / 1e8, 2)

    foreign = net("外資")
    trust = net("投信")
    dealer = net("自營")
    return {
        "date": twse_date_to_iso(last_date),
        "foreign": foreign,
        "trust": trust,
        "dealer": dealer,
        "total": round(foreign + trust + dealer, 2),
    }


# ---------------------------------------------------------------------------
# 資料來源 2：TAIFEX 政府開放資料 (CSV)
# ---------------------------------------------------------------------------

def fetch_taifex_csv(data_name, save_raw=True):
    url = f"https://www.taifex.com.tw/data_gov/taifex_open_data.asp?data_name={data_name}"
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    raw = resp.content
    text = None
    for enc in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    if save_raw:
        os.makedirs(RAW_DIR, exist_ok=True)
        with open(os.path.join(RAW_DIR, f"{data_name}.csv"), "w", encoding="utf-8") as f:
            f.write(text)
    df = pd.read_csv(io.StringIO(text))
    df.columns = [str(c).strip() for c in df.columns]
    return df


def latest_rows(df, date_col):
    last_date = df[date_col].iloc[-1]
    return df[df[date_col] == last_date], last_date


def get_tx_futures():
    df = fetch_taifex_csv("DailyMarketReportFut")
    log(f"DailyMarketReportFut 欄位: {list(df.columns)}")
    prod_col = find_col(df, "商品代號", "商品名稱", "契約")
    month_col = find_col(df, "到期月份")
    date_col = find_col(df, "日期", "交易日期")
    settle_col = find_col(df, "結算價")
    close_col = find_col(df, "收盤價", "最後成交價") or settle_col
    chg_col = find_col(df, "漲跌價", "漲跌(")
    chgpct_col = find_col(df, "漲跌%", "漲跌百分比")
    if not (prod_col and month_col and date_col):
        raise ValueError("找不到必要欄位 (商品/月份/日期)")

    tx = df[df[prod_col].astype(str).str.strip() == "TX"].copy()
    if tx.empty:
        # 有些版本商品代號欄位含中文「臺股期貨」
        tx = df[df[prod_col].astype(str).str.contains("臺股期貨")].copy()
    # 排除價差(月份欄含 "/" 的是跨月價差合約)
    tx = tx[~tx[month_col].astype(str).str.contains("/")]
    last_date = tx[date_col].iloc[-1]
    tx_today = tx[tx[date_col] == last_date].copy()
    tx_today = tx_today.sort_values(month_col)
    near = tx_today.iloc[0]

    price = to_float(near[close_col])
    change = to_float(near[chg_col]) if chg_col else None
    change_pct = to_float(near[chgpct_col]) if chgpct_col else None
    return {
        "date": roc_date_to_iso(last_date),
        "price": price,
        "change": change,
        "change_pct": change_pct,
    }


def _load_institutional_futures_df():
    df = fetch_taifex_csv("MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate")
    log(f"三大法人期貨 欄位: {list(df.columns)}")
    return df


def get_institutional_futures_for(df, product_keyword):
    """從三大法人期貨明細表中，抓出指定商品(例如 臺股期貨 / 小型臺指期貨) 的
    自營/投信/外資 當日留倉淨額與較前日增減"""
    date_col = find_col(df, "日期")
    prod_col = find_col(df, "商品名稱", "商品")
    role_col = find_col(df, "身份", "身分")
    oi_net_col = find_col(df, "未平倉契約淨額", "未平倉淨額", "未沖銷契約淨額")
    oi_chg_col = find_col(df, "未平倉契約淨額增減", "增減")
    if not (date_col and prod_col and role_col and oi_net_col):
        raise ValueError("找不到必要欄位")

    last_date = df[date_col].iloc[-1]
    today = df[(df[date_col] == last_date) & (df[prod_col].astype(str).str.contains(product_keyword))]

    def pick(keyword):
        rows = today[today[role_col].astype(str).str.contains(keyword)]
        if rows.empty:
            return None, None
        net = to_float(rows[oi_net_col].iloc[0])
        chg = to_float(rows[oi_chg_col].iloc[0]) if oi_chg_col else None
        return net, chg

    dealer_net, dealer_chg = pick("自營")
    trust_net, trust_chg = pick("投信")
    foreign_net, foreign_chg = pick("外資")

    return {
        "date": roc_date_to_iso(last_date),
        "dealer_oi_net": dealer_net,
        "dealer_oi_chg": dealer_chg,
        "trust_oi_net": trust_net,
        "trust_oi_chg": trust_chg,
        "foreign_oi_net": foreign_net,
        "foreign_oi_chg": foreign_chg,
    }


def get_institutional_futures_tx():
    df = _load_institutional_futures_df()
    return get_institutional_futures_for(df, "臺股期貨")


def get_institutional_futures_mtx():
    df = _load_institutional_futures_df()
    return get_institutional_futures_for(df, "小型臺指期貨")


def get_large_trader_futures():
    """
    抓取前五大/前十大交易人期貨未沖銷部位，包含：
    - 近月份：買方、賣方、淨額、增減
    - 所有月份：買方、賣方、淨額、增減
    - 前十大特定法人：買方、賣方、淨額、增減
    TAIFEX CSV 通常有兩列：一列是近月份、一列是所有月份。
    用「月份別」或列順序來區分。
    """
    df = fetch_taifex_csv("OpenInterestOfLargeTradersFutures")
    log(f"大額交易人期貨 欄位: {list(df.columns)}")
    date_col = find_col(df, "日期")
    prod_col = find_col(df, "商品名稱", "商品")
    if not (date_col and prod_col):
        raise ValueError("找不到必要欄位")

    last_date = df[date_col].iloc[-1]
    today = df[(df[date_col] == last_date) & (df[prod_col].astype(str).str.contains("臺股期貨"))].copy()

    result = {"date": roc_date_to_iso(last_date), "rows_found": len(today)}
    if today.empty:
        return result

    # 欄位模糊比對（TAIFEX 欄位名常有空白或全半形差異）
    def fc(*kws): return find_col(df, *kws)

    b5n  = fc("前五大交易人買方", "前五大買方口數")
    s5n  = fc("前五大交易人賣方", "前五大賣方口數")
    b10n = fc("前十大交易人買方", "前十大買方口數")
    s10n = fc("前十大交易人賣方", "前十大賣方口數")
    sp_b = fc("特定法人買方", "前十大特定法人買方")
    sp_s = fc("特定法人賣方", "前十大特定法人賣方")

    # 嘗試找「增減」欄（可能叫「前五大交易人買方增減」等）
    b5c  = fc("前五大交易人買方增減", "前五大買方增減")
    s5c  = fc("前五大交易人賣方增減", "前五大賣方增減")
    b10c = fc("前十大交易人買方增減", "前十大買方增減")
    s10c = fc("前十大交易人賣方增減", "前十大賣方增減")
    sp_bc= fc("特定法人買方增減", "前十大特定法人買方增減")
    sp_sc= fc("特定法人賣方增減", "前十大特定法人賣方增減")

    # 找「月份別」欄以區分近月/所有月份
    month_type_col = fc("月份別", "近月", "所有月份")

    def extract(row):
        out = {}
        def g(col): return to_float(row[col]) if col and col in row.index else None
        if b5n and s5n:
            out["top5_buy"]  = g(b5n)
            out["top5_sell"] = g(s5n)
            out["top5_net"]  = round(g(b5n) - g(s5n), 0) if g(b5n) is not None and g(s5n) is not None else None
            out["top5_buy_chg"]  = g(b5c)
            out["top5_sell_chg"] = g(s5c)
        if b10n and s10n:
            out["top10_buy"]  = g(b10n)
            out["top10_sell"] = g(s10n)
            out["top10_net"]  = round(g(b10n) - g(s10n), 0) if g(b10n) is not None and g(s10n) is not None else None
            out["top10_buy_chg"]  = g(b10c)
            out["top10_sell_chg"] = g(s10c)
        if sp_b and sp_s:
            out["top10_specific_buy"]  = g(sp_b)
            out["top10_specific_sell"] = g(sp_s)
            out["top10_specific_net"]  = round(g(sp_b) - g(sp_s), 0) if g(sp_b) is not None and g(sp_s) is not None else None
            out["top10_specific_buy_chg"]  = g(sp_bc)
            out["top10_specific_sell_chg"] = g(sp_sc)
        return out

    if month_type_col:
        # 有月份別欄：分別取近月與所有月份
        near_rows = today[today[month_type_col].astype(str).str.contains("近月")]
        all_rows  = today[today[month_type_col].astype(str).str.contains("所有")]
        if not near_rows.empty:
            result["near_month"] = extract(near_rows.iloc[0])
        if not all_rows.empty:
            result["all_months"] = extract(all_rows.iloc[0])
        # 若只有一列，同時放進兩個 key
        if near_rows.empty and all_rows.empty and not today.empty:
            result["near_month"] = extract(today.iloc[0])
            result["all_months"] = extract(today.iloc[0])
    else:
        # 沒有月份別欄：第一列視為近月，第二列（若有）視為所有月份
        result["near_month"] = extract(today.iloc[0])
        if len(today) >= 2:
            result["all_months"] = extract(today.iloc[1])
        else:
            result["all_months"] = extract(today.iloc[0])

    return result


def get_pc_ratio():
    df = fetch_taifex_csv("PutCallRatio")
    log(f"PutCallRatio 欄位: {list(df.columns)}")
    date_col = find_col(df, "日期")
    pc_col = find_col(df, "賣買權未平倉比", "賣權買權未平倉比率", "比率")
    vix_col = find_col(df, "VIX")
    if not (date_col and pc_col):
        raise ValueError("找不到必要欄位")
    last = df.iloc[-1]
    out = {
        "date": roc_date_to_iso(last[date_col]),
        "pc_ratio": to_float(last[pc_col]),
    }
    if vix_col:
        out["vix"] = to_float(last[vix_col])
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    weighted_index = safe(get_weighted_index, "加權指數 (TWSE FMTQIK)")
    institutional_spot = safe(get_institutional_spot, "三大法人現貨買賣超 (TWSE BFIAUU)")
    tx_futures = safe(get_tx_futures, "台指期 (TAIFEX DailyMarketReportFut)")
    institutional_futures_tx = safe(get_institutional_futures_tx, "三大法人期貨-臺股期貨 (TAIFEX)")
    institutional_futures_mtx = safe(get_institutional_futures_mtx, "三大法人期貨-小型臺指期貨 (TAIFEX)")
    large_trader_futures = safe(get_large_trader_futures, "大額交易人期貨未平倉 (TAIFEX)")
    pc_ratio = safe(get_pc_ratio, "賣買權未平倉比 (TAIFEX)")

    # 決定這次資料代表的日期：以加權指數或台指期的日期為主
    record_date = None
    for d in [weighted_index, tx_futures, institutional_spot]:
        if d and d.get("date"):
            record_date = d["date"]
            break
    if not record_date:
        record_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    basis = None
    if weighted_index and tx_futures and tx_futures.get("price") is not None:
        basis = round(tx_futures["price"] - weighted_index["close"], 2)

    record = {
        "date": record_date,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "weighted_index": weighted_index,
        "tx_futures": tx_futures,
        "basis": basis,
        "institutional_spot": institutional_spot,
        "institutional_futures_tx": institutional_futures_tx,
        "institutional_futures_mtx": institutional_futures_mtx,
        "large_trader_futures": large_trader_futures,
        "pc_ratio": pc_ratio,
    }

    # 讀取既有歷史，依日期去重後更新
    history = []
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                history = json.load(f).get("history", [])
        except Exception as e:
            log(f"[WARN] 讀取舊 history.json 失敗: {e}")

    history = [h for h in history if h.get("date") != record_date]
    history.append(record)
    history.sort(key=lambda h: h.get("date", ""))
    history = history[-180:]  # 只保留最近約180個交易日，避免檔案無限長大

    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump({"history": history}, f, ensure_ascii=False, indent=2)

    with open(LATEST_PATH, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(LOG_LINES))

    log(f"完成。本次資料日期: {record_date}")


if __name__ == "__main__":
    main()
