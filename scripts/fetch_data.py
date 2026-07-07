"""
台股期貨籌碼儀表板 - 每日資料抓取腳本
"""
import io, json, os, re, traceback
from datetime import datetime, timezone, timedelta
import pandas as pd
import requests

OUT_DIR      = os.path.join(os.path.dirname(__file__), "..", "data")
RAW_DIR      = os.path.join(OUT_DIR, "raw")
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

def to_float(x, default=0.0):
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except Exception:
        return default

def find_col(df, *keywords):
    for col in df.columns:
        for kw in keywords:
            if kw in str(col):
                return col
    return None

def roc_date_to_iso(s):
    s = str(s).strip()
    # 純8位數字 YYYYMMDD（TAIFEX常見格式）
    if re.match(r"^\d{8}$", s):
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    # 純7位數字 YYYMMDD（民國年）
    if re.match(r"^\d{7}$", s):
        return f"{int(s[0:3])+1911}-{s[3:5]}-{s[5:7]}"
    # 有分隔符的民國年 YYY/MM/DD 或 YYY-MM-DD
    m = re.match(r"(\d{2,3})[/-](\d{1,2})[/-](\d{1,2})", s)
    if m:
        roc, mo, da = m.groups()
        return f"{int(roc)+1911}-{int(mo):02d}-{int(da):02d}"
    # 有分隔符的西元年 YYYY/MM/DD
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
    r = requests.get(url, timeout=30, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    return r.json()

def get_weighted_index():
    data = fetch_twse_json("exchangeReport/FMTQIK")
    df = pd.DataFrame(data)
    log(f"FMTQIK 欄位: {list(df.columns)}")
    if df.empty:
        raise ValueError("空資料")
    # 欄位為英文：Date, TAIEX, Change, TradeValue
    date_col     = find_col(df, "Date", "日期")
    close_col    = find_col(df, "TAIEX", "發行量加權股價指數", "加權股價指數")
    chg_col      = find_col(df, "Change", "漲跌點數", "漲跌")
    turnover_col = find_col(df, "TradeValue", "成交金額")
    log(f"  date={date_col}, close={close_col}, chg={chg_col}, turnover={turnover_col}")
    if not (date_col and close_col):
        raise ValueError(f"找不到必要欄位，現有: {list(df.columns)}")
    last   = df.iloc[-1]
    close  = to_float(last[close_col])
    change = to_float(last[chg_col]) if chg_col else 0.0
    prev   = close - change
    # 日期可能是西元 8 碼或民國 7 碼
    raw_date = str(last[date_col])
    digits = re.sub(r"\D","", raw_date)
    if len(digits) == 8:
        iso_date = f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    elif len(digits) == 7:
        iso_date = f"{int(digits[0:3])+1911}-{digits[3:5]}-{digits[5:7]}"
    else:
        iso_date = raw_date
    turnover = to_float(last[turnover_col])/1e8 if turnover_col else None
    return {
        "date": iso_date,
        "close": round(close, 2),
        "change": round(change, 2),
        "change_pct": round(change/prev*100, 2) if prev else None,
        "turnover_billion": round(turnover, 2) if turnover else None,
    }

def get_institutional_spot():
    """
    TWSE 三大法人整體買賣超。
    嘗試多個可能的 endpoint，逐一 fallback。
    """
    endpoints = [
        "fund/TWT38U",          # 外資及陸資買賣超彙總
        "exchangeReport/BFI82U",
        "exchangeReport/BFIAUU",
    ]
    df = None
    used_ep = None
    for ep in endpoints:
        try:
            data = fetch_twse_json(ep)
            tmp = pd.DataFrame(data)
            log(f"嘗試 {ep}，欄位: {list(tmp.columns)}, 列數: {len(tmp)}")
            if not tmp.empty:
                df = tmp
                used_ep = ep
                break
        except Exception as e:
            log(f"  {ep} 失敗: {e}")

    if df is None:
        raise ValueError("所有 TWSE 三大法人 endpoint 均失敗")

    log(f"使用 endpoint: {used_ep}, 欄位: {list(df.columns)}")
    df.columns = [str(c).strip() for c in df.columns]

    # 找日期欄
    date_col = find_col(df, "Date", "日期")
    name_col = find_col(df, "Name", "單位名稱", "機構名稱", "名稱")
    buy_col  = find_col(df, "BuyValue", "Buy", "買進金額", "買進")
    sell_col = find_col(df, "SellValue", "Sell", "賣出金額", "賣出")
    diff_col = find_col(df, "Diff", "買賣差額", "差額", "買賣超")
    log(f"  date={date_col}, name={name_col}, buy={buy_col}, sell={sell_col}, diff={diff_col}")

    if not (name_col and (buy_col or diff_col)):
        log(f"[WARN] 三大法人現貨：找不到買賣欄位，略過。現有: {list(df.columns)}")
        return None

    # 取最新日期
    if date_col:
        last_date = df[date_col].iloc[-1]
        today = df[df[date_col]==last_date]
    else:
        today = df

    def net(keyword):
        rows = today[today[name_col].astype(str).str.contains(keyword, case=False)]
        if rows.empty:
            return None
        if diff_col:
            return round(rows[diff_col].apply(to_float).sum()/1e8, 2)
        if buy_col and sell_col:
            b = rows[buy_col].apply(to_float).sum()
            s = rows[sell_col].apply(to_float).sum()
            return round((b-s)/1e8, 2)
        return None

    foreign = net("外資|Foreign")
    trust   = net("投信|Trust|Invest")
    dealer  = net("自營|Dealer|Prop")
    iso_date = roc_date_to_iso(last_date) if date_col else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = sum(v for v in [foreign, trust, dealer] if v is not None)
    return {
        "date": iso_date,
        "foreign": foreign, "trust": trust, "dealer": dealer,
        "total": round(total, 2),
    }

# ---------------------------------------------------------------------------
# TAIFEX CSV
# ---------------------------------------------------------------------------
def fetch_taifex_csv(data_name, save_raw=True):
    url = f"https://www.taifex.com.tw/data_gov/taifex_open_data.asp?data_name={data_name}"
    r = requests.get(url, timeout=30, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    raw = r.content
    text = None
    for enc in ("utf-8-sig","utf-8","big5","cp950"):
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
    date_col  = find_col(df, "日期")
    prod_col  = find_col(df, "契約代號", "商品代號", "契約")
    month_col = find_col(df, "到期月份")
    close_col = find_col(df, "最後成交價", "收盤價", "結算價")
    chg_col   = find_col(df, "漲跌價", "漲跌(")
    chgpct_col= find_col(df, "漲跌%")
    if not (prod_col and month_col and date_col):
        raise ValueError(f"找不到必要欄位，現有: {list(df.columns)}")
    tx = df[df[prod_col].astype(str).str.strip().isin(["TX","臺股期貨"])].copy()
    if tx.empty:
        tx = df[df[prod_col].astype(str).str.contains("TX")].copy()
    tx = tx[~tx[month_col].astype(str).str.contains("/")]
    # 只取一般時段（日盤），排除盤後時段
    session_col = find_col(df, "交易時段")
    if session_col:
        tx_regular = tx[tx[session_col].astype(str).str.contains("一般")]
        if not tx_regular.empty:
            tx = tx_regular
    last_date = tx[date_col].iloc[-1]
    near = tx[tx[date_col]==last_date].sort_values(month_col).iloc[0]
    return {
        "date": roc_date_to_iso(last_date),
        "price":      to_float(near[close_col]) if close_col else None,
        "change":     to_float(near[chg_col])   if chg_col   else None,
        "change_pct": to_float(near[chgpct_col])if chgpct_col else None,
    }

def _load_inst_fut_df():
    df = fetch_taifex_csv("MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate")
    log(f"三大法人期貨 欄位: {list(df.columns)}")
    return df

def get_institutional_futures_for(df, product_keyword):
    date_col   = find_col(df, "日期")
    prod_col   = find_col(df, "商品名稱", "商品")
    role_col   = find_col(df, "身份別", "身份", "身分")
    oi_net_col = find_col(df, "多空未平倉口數淨額", "未平倉契約淨額", "未平倉淨額")
    oi_chg_col = find_col(df, "多空未平倉口數淨額增減", "增減")
    log(f"  [{product_keyword}] role={role_col}, oi_net={oi_net_col}")
    if not (date_col and prod_col and role_col and oi_net_col):
        raise ValueError(f"找不到必要欄位，現有: {list(df.columns)}")
    last_date = df[date_col].iloc[-1]
    today = df[(df[date_col]==last_date) & (df[prod_col].astype(str).str.contains(product_keyword))]
    def pick(kw):
        rows = today[today[role_col].astype(str).str.contains(kw)]
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
    return get_institutional_futures_for(_load_inst_fut_df(), "臺股期貨")

def get_institutional_futures_mtx():
    return get_institutional_futures_for(_load_inst_fut_df(), "小型臺指期貨")

def get_large_trader_futures():
    df = fetch_taifex_csv("OpenInterestOfLargeTradersFutures")
    log(f"大額交易人期貨 欄位: {list(df.columns)}")
    date_col  = find_col(df, "日期")
    prod_col  = find_col(df, "商品名稱", "契約名稱", "契約")
    month_col = find_col(df, "到期月份")
    cat_col   = find_col(df, "交易人類別", "類別")
    b5_col    = find_col(df, "前五大交易人買方數量", "前五大交易人買方")
    s5_col    = find_col(df, "前五大交易人賣方數量", "前五大交易人賣方")
    b10_col   = find_col(df, "前十大交易人買方數量", "前十大交易人買方")
    s10_col   = find_col(df, "前十大交易人賣方數量", "前十大交易人賣方")
    log(f"  b5={b5_col}, s5={s5_col}, b10={b10_col}, s10={s10_col}")
    log(f"  month={month_col}, cat={cat_col}")

    if not (date_col and prod_col):
        raise ValueError(f"找不到必要欄位，現有: {list(df.columns)}")

    last_date = df[date_col].iloc[-1]
    today = df[(df[date_col]==last_date) & (
        df[prod_col].astype(str).str.contains("臺股期貨|TX")
    )].copy()
    result = {"date": roc_date_to_iso(last_date), "rows_found": len(today)}
    if today.empty or not (b5_col and s5_col and b10_col and s10_col):
        return result

    # 記錄所有列的月份和類別，方便除錯
    if month_col:
        log(f"  到期月份值: {today[month_col].tolist()}")
    if cat_col:
        log(f"  交易人類別值: {today[cat_col].tolist()}")

    def extract(row):
        b5  = to_float(row[b5_col]);  s5  = to_float(row[s5_col])
        b10 = to_float(row[b10_col]); s10 = to_float(row[s10_col])
        return {
            "top5_buy":b5, "top5_sell":s5, "top5_net":round(b5-s5,0),
            "top10_buy":b10,"top10_sell":s10,"top10_net":round(b10-s10,0),
        }

    if month_col:
        # 月份邏輯：
        # 999912 = 所有月份合計（最大值）
        # 666666 = 近三月或其他中間合計
        # 202607 = 具體近月合約
        today["_m"] = pd.to_numeric(today[month_col], errors="coerce").fillna(0)

        # 交易人類別：0 = 一般交易人，1 = 特定法人（數字格式）
        # 也相容文字格式「特定」
        if cat_col:
            cat_numeric = pd.to_numeric(today[cat_col], errors="coerce")
            if cat_numeric.notna().any():
                general  = today[cat_numeric == 0]
                specific = today[cat_numeric == 1]
            else:
                general  = today[~today[cat_col].astype(str).str.contains("特定")]
                specific = today[today[cat_col].astype(str).str.contains("特定")]
        else:
            general = today
            specific= pd.DataFrame()

        log(f"  一般交易人列數={len(general)}, 特定法人列數={len(specific)}")
        if not general.empty:
            log(f"  一般月份值: {general['_m'].tolist()}")

        # 近月 = 一般交易人中，月份 < 666666（具體合約月份）
        near_rows = general[general["_m"] < 666666].sort_values("_m") if not general.empty else pd.DataFrame()
        # 所有月份 = 月份 = 999912（最大）
        all_rows  = general[general["_m"] == 999912] if not general.empty else pd.DataFrame()
        # 若沒有 999912，取月份最大的列
        if all_rows.empty and not general.empty:
            max_m = general["_m"].max()
            all_rows = general[general["_m"] == max_m]

        if not near_rows.empty:
            result["near_month"] = extract(near_rows.iloc[0])
        if not all_rows.empty:
            result["all_months"] = extract(all_rows.iloc[0])

        # 特定法人：分別注入近月與所有月份（前端從 near_month/all_months 內讀取）
        if not specific.empty:
            sp_near = specific[specific["_m"] < 666666].sort_values("_m")
            sp_all  = specific[specific["_m"] == 999912]
            if sp_all.empty:
                sp_all = specific[specific["_m"] == specific["_m"].max()]

            def inject_specific(target_key, rows):
                if target_key in result and not rows.empty:
                    row = rows.iloc[0]
                    b = to_float(row[b10_col]); s = to_float(row[s10_col])
                    result[target_key]["top10_specific_buy"]  = b
                    result[target_key]["top10_specific_sell"] = s
                    result[target_key]["top10_specific_net"]  = round(b - s, 0)

            inject_specific("near_month", sp_near)
            inject_specific("all_months", sp_all)

            # 頂層也保留一份（相容）
            if not sp_all.empty:
                row = sp_all.iloc[0]
                result["top10_specific_net"] = round(
                    to_float(row[b10_col]) - to_float(row[s10_col]), 0)
    else:
        result["near_month"] = extract(today.iloc[0])
        if len(today) >= 2:
            result["all_months"] = extract(today.iloc[1])

    return result

def get_pc_ratio():
    df = fetch_taifex_csv("PutCallRatio")
    log(f"PutCallRatio 欄位: {list(df.columns)}")
    date_col = find_col(df, "日期")
    # 優先抓「未平倉量比率」，不要抓成「成交量比率」
    pc_col   = find_col(df, "買賣權未平倉量比率", "賣買權未平倉比", "未平倉量比率%")
    log(f"  date={date_col}, pc={pc_col}")
    if not (date_col and pc_col):
        raise ValueError(f"找不到必要欄位，現有: {list(df.columns)}")
    # CSV 可能不是按日期排序，取日期最大的那列
    df["_d"] = pd.to_numeric(df[date_col].astype(str).str.replace(r"\D","",regex=True), errors="coerce")
    last = df.sort_values("_d").iloc[-1]
    log(f"  P/C 取用日期: {last[date_col]}, 值: {last[pc_col]}")
    return {
        "date": roc_date_to_iso(last[date_col]),
        "pc_ratio": round(to_float(last[pc_col]), 2),
    }

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    weighted_index           = safe(get_weighted_index,            "加權指數 (TWSE FMTQIK)")
    institutional_spot       = safe(get_institutional_spot,        "三大法人現貨 (TWSE)")
    tx_futures               = safe(get_tx_futures,                "台指期 (TAIFEX)")
    institutional_futures_tx = safe(get_institutional_futures_tx,  "三大法人期貨-臺股期貨")
    institutional_futures_mtx= safe(get_institutional_futures_mtx, "三大法人期貨-小型臺指期貨")
    large_trader_futures     = safe(get_large_trader_futures,      "大額交易人期貨")
    pc_ratio                 = safe(get_pc_ratio,                  "P/C Ratio (TAIFEX)")

    # 以 TAIFEX 的日期為主（TAIFEX 更新較快）
    taifex_dates = [
        d.get("date") for d in [tx_futures, institutional_futures_tx, large_trader_futures]
        if d and d.get("date")
    ]
    record_date = max(taifex_dates) if taifex_dates else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log(f"record_date（以TAIFEX為主）: {record_date}")

    # 加權指數 / P/C Ratio 顯示邏輯：
    # 台灣時間 9:00-13:30 為盤中，收盤價尚未產生 → 顯示 －
    # 其他時間 → 顯示 TWSE/TAIFEX 提供的最新收盤值
    now_tw = datetime.now(timezone.utc) + timedelta(hours=8)
    hm = now_tw.strftime("%H:%M")
    is_weekday = now_tw.weekday() < 5
    in_session = is_weekday and ("09:00" <= hm < "13:30")
    if in_session:
        log(f"目前台灣時間 {hm} 為盤中時段，加權指數與 P/C Ratio 顯示為 －")
        weighted_index = None
        pc_ratio = None
    else:
        # 收盤後照常顯示，但若日期與 TAIFEX 不符仍記錄警告方便追蹤
        for d, name in [(weighted_index, "加權指數"), (pc_ratio, "P/C Ratio")]:
            if d and d.get("date") != record_date:
                log(f"[WARN] {name} 日期 {d.get('date')} ≠ TAIFEX {record_date}（來源尚未更新，仍顯示最新可得值）")

    basis = None
    if weighted_index and tx_futures:
        p = tx_futures.get("price"); c = weighted_index.get("close")
        # 只在兩者日期一致時計算價差，避免用舊指數算出錯誤價差
        if p and c and weighted_index.get("date") == tx_futures.get("date"):
            basis = round(p - c, 2)
        elif p and c:
            log(f"[WARN] 加權指數日期({weighted_index.get('date')})與台指期({tx_futures.get('date')})不符，價差不計算")

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
