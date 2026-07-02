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
    log(f"  b5={b5_col}, b10={b10_col}, month={month_col}, cat={cat_col}")

    if not (date_col and prod_col):
        raise ValueError(f"找不到必要欄位，現有: {list(df.columns)}")

    last_date = df[date_col].iloc[-1]
    today = df[(df[date_col]==last_date) & (
        df[prod_col].astype(str).str.contains("臺股期貨|TX")
    )].copy()
    result = {"date": roc_date_to_iso(str(last_date)), "rows_found": len(today)}
    if today.empty or not (b5_col and s5_col and b10_col and s10_col):
        return result

    if month_col:
        log(f"  到期月份值: {today[month_col].tolist()}")
    if cat_col:
        log(f"  交易人類別值: {today[cat_col].tolist()}")

    def extract(row):
        b5=to_float(row[b5_col]); s5=to_float(row[s5_col])
        b10=to_float(row[b10_col]); s10=to_float(row[s10_col])
        return {
            "top5_buy":b5,"top5_sell":s5,"top5_net":round(b5-s5,0),
            "top10_buy":b10,"top10_sell":s10,"top10_net":round(b10-s10,0),
        }

    today["_m"] = pd.to_numeric(today[month_col], errors="coerce").fillna(0)

    # 交易人類別：數字 0=一般交易人，1=特定法人
    # 也相容文字格式「一般」/「特定」
    if cat_col:
        try:
            cat_num = pd.to_numeric(today[cat_col], errors="coerce")
            general  = today[cat_num == 0]
            specific = today[cat_num == 1]
            if general.empty:  # 若無法用數字區分，改用文字
                raise ValueError("fallback to text")
        except Exception:
            general  = today[~today[cat_col].astype(str).str.contains("特定")]
            specific = today[today[cat_col].astype(str).str.contains("特定")]
    else:
        general = today
        specific = pd.DataFrame()

    log(f"  一般={len(general)}, 特定={len(specific)}")

    # 近月 = 一般中月份最小的（具體合約月份，如202607）
    near_rows = general[general["_m"] < 666666].sort_values("_m")
    # 所有月份 = 一般中月份=999912
    all_rows  = general[general["_m"] == 999912]
    if all_rows.empty:
        all_rows = general[general["_m"] == general["_m"].max()]

    if not near_rows.empty:
        result["near_month"] = extract(near_rows.iloc[0])
    if not all_rows.empty:
        result["all_months"] = extract(all_rows.iloc[0])

    # 特定法人：月份=999912的那列，top10_net即為十大特定法人淨額
    if not specific.empty:
        sp_all = specific[specific["_m"] == 999912]
        if sp_all.empty:
            sp_all = specific[specific["_m"] == specific["_m"].max()]
        if not sp_all.empty:
            row = sp_all.iloc[0]
            result["top10_specific_net"] = round(
                to_float(row[b10_col]) - to_float(row[s10_col]), 0)

    return result
