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
# TWSE — 使用官方盤後統計端點（收盤後約13:40-15:00更新，遠比 openapi 即時）
# ---------------------------------------------------------------------------
def _twse_rwd_json(url):
    r = requests.get(url, timeout=30, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    return r.json()

def _tw_now():
    return datetime.now(timezone.utc) + timedelta(hours=8)

def get_weighted_index():
    """
    TWSE 盤後統計 MI_INDEX（大盤統計資訊）
    從今天往前最多找5天，取最近一個有資料的交易日
    """
    for offset in range(0, 5):
        d = _tw_now() - timedelta(days=offset)
        ymd = d.strftime("%Y%m%d")
        url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd}&type=IND&response=json"
        try:
            js = _twse_rwd_json(url)
        except Exception as e:
            log(f"  MI_INDEX {ymd} 請求失敗: {e}")
            continue
        if js.get("stat") != "OK":
            log(f"  MI_INDEX {ymd} stat={js.get('stat')}（可能為假日或尚未更新）")
            continue

        # 在回傳的各表中尋找「發行量加權股價指數」那一列
        target = None
        tables = js.get("tables") or []
        for t in tables:
            for row in (t.get("data") or []):
                if row and "發行量加權股價指數" in str(row[0]):
                    target = row; break
            if target: break
        if target is None:
            for i in range(1, 8):
                for row in (js.get(f"data{i}") or []):
                    if row and "發行量加權股價指數" in str(row[0]):
                        target = row; break
                if target: break
        if target is None:
            log(f"  MI_INDEX {ymd} 找不到發行量加權股價指數列，回傳keys: {list(js.keys())}")
            # 印出各表的前幾列名稱協助除錯
            for t in (js.get("tables") or [])[:5]:
                sample = [str(r[0]) for r in (t.get("data") or [])[:3]]
                log(f"    table title={t.get('title')}, 前幾列: {sample}")
            continue

        # 欄位順序: [指數, 收盤指數, 漲跌(+/-), 漲跌點數, 漲跌百分比(%), ...]
        close   = to_float(target[1])
        sign    = -1 if "-" in str(target[2]) else 1
        change  = sign * abs(to_float(target[3]))
        chg_pct = sign * abs(to_float(target[4]))

        # 成交金額從 FMTQIK 盤後統計取同一天
        iso_date = f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"
        turnover = None
        try:
            js2 = _twse_rwd_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={ymd}&response=json")
            if js2.get("stat") == "OK":
                for r2 in (js2.get("data") or []):
                    if roc_date_to_iso(r2[0]) == iso_date:
                        turnover = round(to_float(r2[2]) / 1e8, 2)
                        break
        except Exception as e:
            log(f"  FMTQIK 成交金額抓取失敗: {e}")

        log(f"  加權指數 {iso_date}: {close} ({change:+.2f}, {chg_pct:+.2f}%)")
        return {
            "date": iso_date,
            "close": round(close, 2),
            "change": round(change, 2),
            "change_pct": round(chg_pct, 2),
            "turnover_billion": turnover,
        }
    raise ValueError("往前5天皆無 MI_INDEX 資料")

def get_institutional_spot():
    """
    TWSE 盤後統計 BFI82U（三大法人買賣金額統計表，約15:00公布）
    回傳外資/投信/自營商買賣超（億元）
    """
    for offset in range(0, 5):
        d = _tw_now() - timedelta(days=offset)
        ymd = d.strftime("%Y%m%d")
        url = f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate={ymd}&response=json"
        try:
            js = _twse_rwd_json(url)
        except Exception as e:
            log(f"  BFI82U {ymd} 請求失敗: {e}")
            continue
        if js.get("stat") != "OK":
            log(f"  BFI82U {ymd} stat={js.get('stat')}（可能為假日或尚未更新）")
            continue
        data = js.get("data") or []
        if not data:
            continue
        # 完整記錄每一列（名稱與買賣差額），方便核對
        for row in data:
            log(f"  BFI82U列: {row}")

        # 欄位: [單位名稱, 買進金額, 賣出金額, 買賣差額]
        def net(include_kw, exclude_kw=None):
            total, found = 0.0, False
            for row in data:
                name = str(row[0])
                if include_kw in name and (exclude_kw is None or exclude_kw not in name):
                    total += to_float(row[3]); found = True
            return round(total / 1e8, 2) if found else None

        # 自營商 = 自行買賣 + 避險（排除「外資自營商」以免重複計算）
        dealer  = net("自營商", exclude_kw="外資")
        trust   = net("投信")
        # 外資 = 外資及陸資 + 外資自營商
        foreign = net("外資")
        iso_date = f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"
        total = sum(v for v in [foreign, trust, dealer] if v is not None)
        log(f"  三大法人現貨 {iso_date}: 外資{foreign} 投信{trust} 自營{dealer}")
        return {
            "date": iso_date,
            "foreign": foreign, "trust": trust, "dealer": dealer,
            "total": round(total, 2),
        }
    log("[WARN] 往前5天皆無 BFI82U 資料")
    return None

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
