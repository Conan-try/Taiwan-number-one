"""
台股期貨籌碼儀表板 - 每日資料抓取腳本
資料來源（皆為官方公開資料，免金鑰）：
  - 臺灣證券交易所 OpenAPI: https://openapi.twse.com.tw/v1/
  - 臺灣期貨交易所 政府開放資料: https://www.taifex.com.tw/data_gov/taifex_open_data.asp
"""

import io, json, os, re, traceback
from datetime import datetime, timezone

import pandas as pd
import requests

OUT_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
RAW_DIR  = os.path.join(OUT_DIR, "raw")
HISTORY_PATH = os.path.join(OUT_DIR, "history.json")
LATEST_PATH  = os.path.join(OUT_DIR, "latest.json")
LOG_PATH     = os.path.join(OUT_DIR, "fetch_log.txt")
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
# 共用工具
# ---------------------------------------------------------------------------
def to_float(x, default=0.0):
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except Exception:
        return default

def find_col(df, *keywords):
    for col in df.columns:
        col_str = str(col)
        for kw in keywords:
            if kw in col_str:
                return col
    return None

def twse_date_to_iso(s):
    s = re.sub(r"\D", "", str(s))
    if len(s) == 8:
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    if len(s) == 7:
        return f"{int(s[0:3])+1911}-{s[3:5]}-{s[5:7]}"
    return s

def roc_date_to_iso(s):
    s = str(s).strip()
    m = re.match(r"(\d{2,3})[/-](\d{1,2})[/-](\d{1,2})", s)
    if m:
        roc, mo, da = m.groups()
        return f"{int(roc)+1911}-{int(mo):02d}-{int(da):02d}"
    m = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if m:
        y, mo, da = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(da):02d}"
    return s

# ---------------------------------------------------------------------------
# TWSE
# ---------------------------------------------------------------------------
def fetch_twse_json(endpoint):
    url = f"https://openapi.twse.com.tw/v1/{endpoint}"
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.json()

def get_weighted_index():
    data = fetch_twse_json("exchangeReport/FMTQIK")
    df = pd.DataFrame(data)
    log(f"FMTQIK 欄位: {list(df.columns)}")
    if df.empty:
        raise ValueError("FMTQIK 回傳空資料")
    date_col    = find_col(df, "日期")
    close_col   = find_col(df, "發行量加權股價指數", "加權股價指數", "指數")
    chg_col     = find_col(df, "漲跌點數", "漲跌")
    turnover_col= find_col(df, "成交金額")
    log(f"  date_col={date_col}, close_col={close_col}, chg_col={chg_col}, turnover_col={turnover_col}")
    if not (date_col and close_col):
        raise ValueError(f"FMTQIK 找不到必要欄位，現有欄位: {list(df.columns)}")
    last = df.iloc[-1]
    close  = to_float(last[close_col])
    change = to_float(last[chg_col]) if chg_col else 0.0
    prev   = close - change
    return {
        "date": twse_date_to_iso(last[date_col]),
        "close": round(close, 2),
        "change": round(change, 2),
        "change_pct": round(change/prev*100, 2) if prev else None,
        "turnover_billion": round(to_float(last[turnover_col])/1e8, 2) if turnover_col else None,
    }

def get_institutional_spot():
    data = fetch_twse_json("exchangeReport/BFIAUU")
    df = pd.DataFrame(data)
    log(f"BFIAUU 欄位: {list(df.columns)}")
    if df.empty:
        raise ValueError("BFIAUU 回傳空資料")
    date_col = find_col(df, "日期")
    name_col = find_col(df, "單位名稱", "機構名稱", "名稱")
    buy_col  = find_col(df, "買進金額", "買進")
    sell_col = find_col(df, "賣出金額", "賣出")
    log(f"  date_col={date_col}, name_col={name_col}, buy_col={buy_col}, sell_col={sell_col}")
    if not (date_col and name_col and buy_col and sell_col):
        raise ValueError(f"BFIAUU 找不到必要欄位，現有欄位: {list(df.columns)}")
    last_date = df[date_col].iloc[-1]
    today = df[df[date_col] == last_date]
    def net(keyword):
        rows = today[today[name_col].astype(str).str.contains(keyword)]
        return round((rows[buy_col].apply(to_float).sum() - rows[sell_col].apply(to_float).sum()) / 1e8, 2)
    foreign = net("外資")
    trust   = net("投信")
    dealer  = net("自營")
    return {
        "date": twse_date_to_iso(last_date),
        "foreign": foreign, "trust": trust, "dealer": dealer,
        "total": round(foreign+trust+dealer, 2),
    }

# ---------------------------------------------------------------------------
# TAIFEX CSV
# ---------------------------------------------------------------------------
def fetch_taifex_csv(data_name, save_raw=True):
    url = f"https://www.taifex.com.tw/data_gov/taifex_open_data.asp?data_name={data_name}"
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    raw = resp.content
    text = None
    for enc in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            text = raw.decode(enc); break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    if save_raw:
        os.makedirs(RAW_DIR, exist_ok=True)
        open(os.path.join(RAW_DIR, f"{data_name}.csv"), "w", encoding="utf-8").write(text)
    df = pd.read_csv(io.StringIO(text))
    df.columns = [str(c).strip() for c in df.columns]
    return df

def get_tx_futures():
    df = fetch_taifex_csv("DailyMarketReportFut")
    log(f"DailyMarketReportFut 欄位: {list(df.columns)}")
    prod_col  = find_col(df, "契約代號", "商品代號", "商品名稱", "契約")
    month_col = find_col(df, "到期月份")
    date_col  = find_col(df, "日期")
    close_col = find_col(df, "最後成交價", "收盤價", "結算價")
    chg_col   = find_col(df, "漲跌價", "漲跌(")
    chgpct_col= find_col(df, "漲跌%", "漲跌百分比")
    if not (prod_col and month_col and date_col):
        raise ValueError(f"找不到必要欄位，現有: {list(df.columns)}")
    tx = df[df[prod_col].astype(str).str.strip().isin(["TX","臺股期貨"])].copy()
    if tx.empty:
        tx = df[df[prod_col].astype(str).str.contains("臺股期貨|TX")].copy()
    tx = tx[~tx[month_col].astype(str).str.contains("/")]
    last_date = tx[date_col].iloc[-1]
    tx_today = tx[tx[date_col]==last_date].sort_values(month_col)
    near = tx_today.iloc[0]
    return {
        "date": roc_date_to_iso(last_date),
        "price": to_float(near[close_col]) if close_col else None,
        "change": to_float(near[chg_col]) if chg_col else None,
        "change_pct": to_float(near[chgpct_col]) if chgpct_col else None,
    }

def _load_institutional_futures_df():
    df = fetch_taifex_csv("MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate")
    log(f"三大法人期貨 欄位: {list(df.columns)}")
    return df

def get_institutional_futures_for(df, product_keyword):
    date_col   = find_col(df, "日期")
    prod_col   = find_col(df, "商品名稱", "商品")
    # 實際欄位是「身份別」
    role_col   = find_col(df, "身份別", "身份", "身分")
    # 實際欄位是「多空未平倉口數淨額」
    oi_net_col = find_col(df, "多空未平倉口數淨額", "未平倉契約淨額", "未平倉淨額", "未沖銷契約淨額")
    oi_chg_col = find_col(df, "多空未平倉口數淨額增減", "未平倉契約淨額增減", "增減")
    log(f"  [{product_keyword}] role={role_col}, oi_net={oi_net_col}, oi_chg={oi_chg_col}")
    if not (date_col and prod_col and role_col and oi_net_col):
        raise ValueError(f"找不到必要欄位，現有: {list(df.columns)}")
    last_date = df[date_col].iloc[-1]
    today = df[(df[date_col]==last_date) & (df[prod_col].astype(str).str.contains(product_keyword))]
    def pick(keyword):
        rows = today[today[role_col].astype(str).str.contains(keyword)]
        if rows.empty: return None, None
        return to_float(rows[oi_net_col].iloc[0]), (to_float(rows[oi_chg_col].iloc[0]) if oi_chg_col else None)
    dealer_net, dealer_chg = pick("自營")
    trust_net,  trust_chg  = pick("投信")
    foreign_net,foreign_chg= pick("外資")
    return {
        "date": roc_date_to_iso(last_date),
        "dealer_oi_net": dealer_net, "dealer_oi_chg": dealer_chg,
        "trust_oi_net":  trust_net,  "trust_oi_chg":  trust_chg,
        "foreign_oi_net":foreign_net,"foreign_oi_chg":foreign_chg,
    }

def get_institutional_futures_tx():
    return get_institutional_futures_for(_load_institutional_futures_df(), "臺股期貨")

def get_institutional_futures_mtx():
    return get_institutional_futures_for(_load_institutional_futures_df(), "小型臺指期貨")

def get_large_trader_futures():
    """
    實際欄位：日期、契約、商品名稱(契約名稱)、到期月份(週別)、交易人類別、
              前五大交易人買方數量、前五大交易人賣方數量、
              前十大交易人買方數量、前十大交易人賣方數量、全市場未沖銷部位數
    到期月份(週別) 的值：近月合約如 "202607"，所有月份通常是最後一列且值最大或含特定字
    """
    df = fetch_taifex_csv("OpenInterestOfLargeTradersFutures")
    log(f"大額交易人期貨 欄位: {list(df.columns)}")
    date_col  = find_col(df, "日期")
    prod_col  = find_col(df, "商品名稱", "契約名稱", "契約")
    month_col = find_col(df, "到期月份")
    b5_col    = find_col(df, "前五大交易人買方數量", "前五大交易人買方", "前五大買方")
    s5_col    = find_col(df, "前五大交易人賣方數量", "前五大交易人賣方", "前五大賣方")
    b10_col   = find_col(df, "前十大交易人買方數量", "前十大交易人買方", "前十大買方")
    s10_col   = find_col(df, "前十大交易人賣方數量", "前十大交易人賣方", "前十大賣方")
    log(f"  b5={b5_col}, s5={s5_col}, b10={b10_col}, s10={s10_col}, month={month_col}")
    if not (date_col and prod_col):
        raise ValueError(f"找不到必要欄位，現有: {list(df.columns)}")
    last_date = df[date_col].iloc[-1]
    # 篩選臺股期貨 TX
    today = df[(df[date_col]==last_date) & (
        df[prod_col].astype(str).str.contains("臺股期貨|TX")
    )].copy()
    log(f"  今日臺股期貨列數: {len(today)}")
    if month_col:
        log(f"  到期月份值: {today[month_col].tolist()}")
    result = {"date": roc_date_to_iso(last_date), "rows_found": len(today)}
    if today.empty or not (b5_col and s5_col and b10_col and s10_col):
        return result

    def extract(row):
        b5  = to_float(row[b5_col])
        s5  = to_float(row[s5_col])
        b10 = to_float(row[b10_col])
        s10 = to_float(row[s10_col])
        return {
            "top5_buy":  b5,  "top5_sell":  s5,  "top5_net":  round(b5-s5,0),
            "top10_buy": b10, "top10_sell": s10, "top10_net": round(b10-s10,0),
        }

    if month_col:
        month_vals = today[month_col].astype(str).tolist()
        # 找「所有月份」列（通常含"所有"字樣或是數字最大的那列）
        all_rows  = today[today[month_col].astype(str).str.contains("所有|合計|all", case=False)]
        near_rows = today[~today[month_col].astype(str).str.contains("所有|合計|all", case=False)]
        # 近月＝月份數字最小的那列
        if not near_rows.empty:
            try:
                near_rows = near_rows.copy()
                near_rows["_sort"] = near_rows[month_col].astype(str).str.extract(r"(\d+)")[0].astype(float)
                near_rows = near_rows.sort_values("_sort")
            except Exception:
                pass
            result["near_month"] = extract(near_rows.iloc[0])
        if not all_rows.empty:
            result["all_months"] = extract(all_rows.iloc[0])
        elif len(today) >= 2:
            # 若沒有「所有月份」字樣，取月份數字最大的那列當作所有月份
            try:
                today2 = today.copy()
                today2["_sort"] = today2[month_col].astype(str).str.extract(r"(\d+)")[0].astype(float)
                result["all_months"] = extract(today2.sort_values("_sort").iloc[-1])
            except Exception:
                result["all_months"] = extract(today.iloc[-1])
    else:
        result["near_month"] = extract(today.iloc[0])
        if len(today) >= 2:
            result["all_months"] = extract(today.iloc[1])

    return result

def get_pc_ratio():
    df = fetch_taifex_csv("PutCallRatio")
    log(f"PutCallRatio 欄位: {list(df.columns)}")
    date_col = find_col(df, "日期")
    # 實際欄位：買賣權未平倉量比率%
    pc_col   = find_col(df, "買賣權未平倉量比率", "賣買權未平倉比", "未平倉量比率", "比率")
    vix_col  = find_col(df, "VIX")
    log(f"  date={date_col}, pc={pc_col}, vix={vix_col}")
    if not (date_col and pc_col):
        raise ValueError(f"找不到必要欄位，現有: {list(df.columns)}")
    last = df.iloc[-1]
    out = {
        "date": roc_date_to_iso(last[date_col]),
        "pc_ratio": round(to_float(last[pc_col]), 2),
    }
    if vix_col:
        out["vix"] = round(to_float(last[vix_col]), 2)
    return out

# ---------------------------------------------------------------------------
# VIX — 另外從 TAIFEX 官網抓（PutCallRatio CSV 沒有 VIX 欄）
# ---------------------------------------------------------------------------
def get_vix():
    """嘗試從 VolatilityIndex 資料集抓 VIX；此資料集可能不在政府開放資料，失敗則回 None"""
    try:
        df = fetch_taifex_csv("VolatilityIndex", save_raw=True)
        log(f"VolatilityIndex 欄位: {list(df.columns)}")
        date_col = find_col(df, "日期")
        vix_col  = find_col(df, "VIX", "波動率指數", "指數")
        if date_col and vix_col:
            return {"date": roc_date_to_iso(df.iloc[-1][date_col]),
                    "vix": round(to_float(df.iloc[-1][vix_col]), 2)}
    except Exception as e:
        log(f"[WARN] VIX 資料抓取失敗: {e}")
    return None

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    weighted_index          = safe(get_weighted_index,           "加權指數 (TWSE FMTQIK)")
    institutional_spot      = safe(get_institutional_spot,       "三大法人現貨 (TWSE BFIAUU)")
    tx_futures              = safe(get_tx_futures,               "台指期 (TAIFEX DailyMarketReportFut)")
    institutional_futures_tx= safe(get_institutional_futures_tx, "三大法人期貨-臺股期貨")
    institutional_futures_mtx=safe(get_institutional_futures_mtx,"三大法人期貨-小型臺指期貨")
    large_trader_futures    = safe(get_large_trader_futures,     "大額交易人期貨")
    pc_ratio                = safe(get_pc_ratio,                 "P/C Ratio (TAIFEX)")
    vix_data                = safe(get_vix,                      "VIX (TAIFEX VolatilityIndex)")

    # 把 VIX 補進 pc_ratio
    if vix_data and pc_ratio:
        pc_ratio["vix"] = vix_data.get("vix")

    record_date = None
    for d in [weighted_index, tx_futures, institutional_spot]:
        if d and d.get("date"):
            record_date = d["date"]; break
    if not record_date:
        record_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    basis = None
    if weighted_index and tx_futures and tx_futures.get("price") and weighted_index.get("close"):
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

    history = []
    if os.path.exists(HISTORY_PATH):
        try:
            history = json.load(open(HISTORY_PATH, encoding="utf-8")).get("history", [])
        except Exception as e:
            log(f"[WARN] 讀取舊 history.json 失敗: {e}")
    history = [h for h in history if h.get("date") != record_date]
    history.append(record)
    history.sort(key=lambda h: h.get("date",""))
    history = history[-180:]

    json.dump({"history": history}, open(HISTORY_PATH,"w",encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(record, open(LATEST_PATH,"w",encoding="utf-8"), ensure_ascii=False, indent=2)
    open(LOG_PATH,"w",encoding="utf-8").write("\n".join(LOG_LINES))
    log(f"完成。本次資料日期: {record_date}")

if __name__ == "__main__":
    main()
