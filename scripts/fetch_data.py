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
    # 移除 pandas 讀成浮點數的小數點（如 20260707.0 → 20260707）
    if "." in s:
        s = s.split(".")[0]
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

def latest_iso_date(series):
    """把日期欄逐值正規化成 ISO(YYYY-MM-DD) 後取最大值。
    直接比較原始字串或轉數值都會因補零/不補零混雜而出錯，
    例如 '2026/7/2' 去掉分隔符是 6 位數、'20260602' 是 8 位數。"""
    iso = series.astype(str).map(roc_date_to_iso)
    valid = iso[iso.str.match(r"^\d{4}-\d{2}-\d{2}$")]
    if valid.empty:
        raise ValueError(f"日期欄無法解析: {series.head().tolist()}")
    return iso, valid.max()

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
    TWSE 盤後統計 FMTQIK（每日市場成交資訊，收盤後更新）
    欄位: [日期(民國), 成交股數, 成交金額, 成交筆數, 發行量加權股價指數, 漲跌點數]
    從今天往前最多找5天，取最近一個有資料的交易日
    """
    for offset in range(0, 5):
        d = _tw_now() - timedelta(days=offset)
        ymd = d.strftime("%Y%m%d")
        iso_date = f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"
        url = f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={ymd}&response=json"
        try:
            js = _twse_rwd_json(url)
        except Exception as e:
            log(f"  FMTQIK {ymd} 請求失敗: {e}")
            continue
        if js.get("stat") != "OK":
            log(f"  FMTQIK {ymd} stat={js.get('stat')}")
            continue
        target = None
        for row in (js.get("data") or []):
            if roc_date_to_iso(row[0]) == iso_date:
                target = row; break
        if target is None:
            log(f"  FMTQIK {ymd} 該月資料中尚無 {iso_date} 這一天（未更新），往前一天")
            continue
        close    = to_float(target[4])
        change   = to_float(target[5])
        prev     = close - change
        turnover = round(to_float(target[2]) / 1e8, 2)
        log(f"  加權指數 {iso_date}: {close} ({change:+.2f}), 成交 {turnover} 億")
        return {
            "date": iso_date,
            "close": round(close, 2),
            "change": round(change, 2),
            "change_pct": round(change / prev * 100, 2) if prev else None,
            "turnover_billion": turnover,
        }
    raise ValueError("往前5天皆無 FMTQIK 資料")

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
    tx["_iso"], last_iso = latest_iso_date(tx[date_col])
    near = tx[tx["_iso"]==last_iso].sort_values(month_col).iloc[0]
    return {
        "date": last_iso,
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
    df["_iso"], last_iso = latest_iso_date(df[date_col])
    today = df[(df["_iso"]==last_iso) & (df[prod_col].astype(str).str.contains(product_keyword))]
    def pick(kw):
        rows = today[today[role_col].astype(str).str.contains(kw)]
        if rows.empty: return None, None
        return to_float(rows[oi_net_col].iloc[0]), (to_float(rows[oi_chg_col].iloc[0]) if oi_chg_col else None)
    dealer_net, dealer_chg = pick("自營")
    trust_net,  trust_chg  = pick("投信")
    foreign_net,foreign_chg= pick("外資")
    return {
        "date": last_iso,
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

    df["_iso"], last_iso = latest_iso_date(df[date_col])
    today = df[(df["_iso"]==last_iso) & (
        df[prod_col].astype(str).str.contains("臺股期貨|TX")
    )].copy()
    result = {"date": last_iso, "rows_found": len(today)}
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

def get_txo_positions():
    """
    三大法人-選擇權買賣權分計（臺指選擇權 TXO）
    來源: MarketDataOfMajorInstitutionalTradersDetailsOfCallsAndPutsBytheDate
    回傳自營商/外資的買權/賣權淨部位（口）與淨金額（千元）
    """
    df = fetch_taifex_csv("MarketDataOfMajorInstitutionalTradersDetailsOfCallsAndPutsBytheDate")
    log(f"選擇權買賣權分計 欄位: {list(df.columns)}")
    date_col = find_col(df, "日期")
    prod_col = find_col(df, "商品名稱", "商品")
    cp_col   = find_col(df, "買賣權", "權別")
    role_col = find_col(df, "身份別", "身份", "身分")
    oi_net_col  = find_col(df, "未平倉口數買賣淨額", "多空未平倉口數淨額")
    amt_net_col = find_col(df, "未平倉契約金額買賣淨額", "多空未平倉契約金額淨額")
    log(f"  cp={cp_col}, role={role_col}, oi_net={oi_net_col}, amt_net={amt_net_col}")
    if not (date_col and prod_col and cp_col and role_col and oi_net_col):
        raise ValueError(f"找不到必要欄位，現有: {list(df.columns)}")

    # 取最大日期（以正規化後 ISO 日期比較）、篩臺指選擇權
    df["_iso"], last_iso = latest_iso_date(df[date_col])
    today = df[(df["_iso"]==last_iso) & (df[prod_col].astype(str).str.contains("臺指選擇權"))]
    log(f"  臺指選擇權列數: {len(today)}, 買賣權值: {today[cp_col].unique().tolist() if not today.empty else []}")

    result = {"date": last_iso}
    if today.empty:
        return result

    def pick(role_kw, cp_kw):
        rows = today[
            today[role_col].astype(str).str.contains(role_kw) &
            today[cp_col].astype(str).str.upper().str.contains(cp_kw)
        ]
        if rows.empty:
            return None, None
        r = rows.iloc[0]
        oi  = to_float(r[oi_net_col])
        amt = to_float(r[amt_net_col]) if amt_net_col else None
        return oi, amt

    for role_kw, key in [("自營", "dealer"), ("外資", "foreign")]:
        call_oi, call_amt = pick(role_kw, "CALL|買權")
        put_oi,  put_amt  = pick(role_kw, "PUT|賣權")
        result[key] = {
            "call_oi_net": call_oi, "call_amt_net": call_amt,
            "put_oi_net":  put_oi,  "put_amt_net":  put_amt,
        }
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
    # CSV 可能不是按日期排序、且日期格式補零/不補零混雜
    # → 一律先正規化成 ISO 再取最新一天
    df["_iso"], last_iso = latest_iso_date(df[date_col])
    last = df[df["_iso"]==last_iso].iloc[-1]
    log(f"  P/C 取用日期: {last_iso}, 值: {last[pc_col]}")
    return {
        "date": last_iso,
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
    txo_positions            = safe(get_txo_positions,             "選擇權留倉 (TAIFEX)")

    sections = {
        "weighted_index":            weighted_index,
        "tx_futures":                tx_futures,
        "institutional_spot":        institutional_spot,
        "institutional_futures_tx":  institutional_futures_tx,
        "institutional_futures_mtx": institutional_futures_mtx,
        "large_trader_futures":      large_trader_futures,
        "pc_ratio":                  pc_ratio,
        "txo_positions":             txo_positions,
    }

    # 盤中（台灣時間 9:00-13:30）不寫入加權指數與 P/C，收盤後的執行再補
    now_tw = datetime.now(timezone.utc) + timedelta(hours=8)
    hm = now_tw.strftime("%H:%M")
    in_session = now_tw.weekday() < 5 and ("09:00" <= hm < "13:30")
    if in_session:
        log(f"目前台灣時間 {hm} 為盤中時段，本次不寫入加權指數與 P/C Ratio")
        sections["weighted_index"] = None
        sections["pc_ratio"] = None

    # 顯示日期以 TAIFEX 為主（TAIFEX 收盤後更新較快）
    taifex_dates = [
        sections[k].get("date")
        for k in ("tx_futures", "institutional_futures_tx", "large_trader_futures")
        if sections[k] and sections[k].get("date")
    ]
    record_date = max(taifex_dates) if taifex_dates else (
        datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")
    log(f"record_date（以TAIFEX為主）: {record_date}")

    # 讀舊 history
    history = []
    if os.path.exists(HISTORY_PATH):
        try:
            history = json.load(open(HISTORY_PATH, encoding="utf-8")).get("history", [])
        except Exception as e:
            log(f"[WARN] 讀取舊 history.json 失敗: {e}")

    fetched_at = datetime.now(timezone.utc).isoformat()
    history = merge_into_history(history, sections, fetched_at, log)
    history = history[-180:]

    # latest.json = history 中 record_date 那一天（合併後、同日一致的資料）
    latest = next((h for h in history if h["date"] == record_date),
                  history[-1] if history else None)
    if latest is None:
        log("[ERROR] 無任何可寫入的資料")
        return

    json.dump({"history": history}, open(HISTORY_PATH,"w",encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(latest, open(LATEST_PATH,"w",encoding="utf-8"), ensure_ascii=False, indent=2)
    open(LOG_PATH,"w",encoding="utf-8").write("\n".join(LOG_LINES))
    log(f"完成。本次資料日期: {record_date}（history 共 {len(history)} 天）")


SECTION_KEYS = [
    "weighted_index", "tx_futures", "institutional_spot",
    "institutional_futures_tx", "institutional_futures_mtx",
    "large_trader_futures", "pc_ratio", "txo_positions",
]

def merge_into_history(history, sections, fetched_at, log=print):
    """把本次抓到的各資料區塊，依各區塊「自己的日期」寫入所屬的那一天。

    解決的問題：TWSE 與 TAIFEX 更新時間不同步時，舊版會把
    不同天的資料硬拼進同一筆紀錄（例如頂層標 7/7、裡面卻是 7/8 的加權指數）。
    現在每個區塊都回到它真正的日期底下，價差也只用同一天的期、現價計算。
    """
    by_date = {}

    # 1) 舊資料：日期正規化 + 同日去重（後蓋前）
    for h in history:
        d = roc_date_to_iso(h.get("date"))
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(d)):
            log(f"[WARN] 略過無法解析日期的舊紀錄: {h.get('date')!r}")
            continue
        h = dict(h); h["date"] = d
        by_date[d] = h

    # 2) 本次各區塊 upsert 到各自的日期
    for key, sec in sections.items():
        if not sec:
            continue
        d = roc_date_to_iso(sec.get("date")) if sec.get("date") else None
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(d)):
            log(f"[WARN] {key} 日期無法解析（{sec.get('date')!r}），本次不寫入")
            continue
        sec = dict(sec); sec["date"] = d
        entry = by_date.setdefault(d, {"date": d})
        entry[key] = sec
        entry["fetched_at"] = fetched_at

    # 3) 每一天補齊缺欄位、重算價差（僅用同一天的期價與現貨收盤）
    result = []
    for d in sorted(by_date):
        e = by_date[d]
        wi, tx = e.get("weighted_index"), e.get("tx_futures")
        basis = None
        if wi and tx and wi.get("close") is not None and tx.get("price") is not None:
            basis = round(tx["price"] - wi["close"], 2)
        ordered = {"date": d, "fetched_at": e.get("fetched_at"),
                   "weighted_index": wi, "tx_futures": tx, "basis": basis}
        for k in SECTION_KEYS[2:]:
            ordered[k] = e.get(k)
        result.append(ordered)
    return result

if __name__ == "__main__":
    main()