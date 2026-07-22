"""
Event Study 全量版：22個產業、129檔股票、589筆目標價修正事件
股價回溯5年(2021-2026)，扣除大盤beta + 產業beta(leave-one-out)
標準誤採date-cluster聚合，避免同日多檔股票齊漲跌造成的顯著性高估
"""
import json
import numpy as np
import pandas as pd

events = json.load(open("factset_data/full_events.json"))
prices_raw = json.load(open("factset_data/full_prices.json"))
taiex_raw = json.load(open("factset_data/pilot_taiex.json"))  # 沿用pilot抓的大盤(2025起)，下面會補抓5年版

# 補抓完整5年大盤
import yfinance as yf
df = yf.download("^TWII", start="2021-01-01", end="2026-07-22", progress=False, auto_adjust=True)
s = df["Close"]["^TWII"] if hasattr(df["Close"], "columns") else df["Close"]
taiex = s.dropna()
taiex.index = pd.to_datetime(taiex.index)
taiex_ret = taiex.pct_change().dropna()

stock_ret = {}
for t, d in prices_raw.items():
    ser = pd.Series(d["prices"]).sort_index()
    ser.index = pd.to_datetime(ser.index)
    stock_ret[t] = ser.pct_change().dropna()

# ticker -> industry
ticker_industry = {}
for e in events:
    ticker_industry[e["ticker"]] = e["industry_canonical"]

industry_tickers = {}
for t, ind in ticker_industry.items():
    industry_tickers.setdefault(ind, []).append(t)

# 對齊到大盤的交易日index
common_all = taiex_ret.index
for t in stock_ret:
    stock_ret[t] = stock_ret[t].reindex(common_all)

# 產業日報酬矩陣（寬表：columns=tickers）
def industry_benchmark_leave_one_out(industry, exclude_ticker):
    members = [t for t in industry_tickers.get(industry, []) if t != exclude_ticker and t in stock_ret]
    if not members:
        return None
    mat = pd.concat([stock_ret[t] for t in members], axis=1)
    return mat.mean(axis=1, skipna=True)

issues = []
rows = []

for ev in events:
    ticker = ev["ticker"]
    industry = ev["industry_canonical"]
    ev_date = pd.Timestamp(ev["date"])
    if ticker not in stock_ret:
        issues.append(f"{ticker} {ev['date']}: 無股價資料")
        continue

    sret = stock_ret[ticker]
    mret = taiex_ret.reindex(common_all)
    iret = industry_benchmark_leave_one_out(industry, ticker)
    if iret is None:
        # 產業只有這一檔可用資料，退化成只用大盤模型
        iret = pd.Series(0.0, index=common_all)
        two_factor = False
    else:
        iret = iret.reindex(common_all)
        two_factor = True

    if ev_date not in common_all:
        after = common_all[common_all >= ev_date]
        if len(after) == 0:
            issues.append(f"{ticker} {ev['date']}: 事件日期在資料範圍外")
            continue
        ev_date_aligned = after[0]
    else:
        ev_date_aligned = ev_date

    pos = common_all.get_loc(ev_date_aligned)
    est_start, est_end = pos - 250, pos - 30
    if est_start < 0:
        issues.append(f"{ticker} {ev['date']}: 估計窗口不足250天(僅{pos}天可用)")
        continue

    y = sret.iloc[est_start:est_end]
    x_mkt = mret.iloc[est_start:est_end]
    x_ind = iret.iloc[est_start:est_end]
    valid = y.notna() & x_mkt.notna() & x_ind.notna()
    if valid.sum() < 100:
        issues.append(f"{ticker} {ev['date']}: 估計窗口有效樣本不足({valid.sum()})")
        continue

    X = np.column_stack([np.ones(valid.sum()), x_mkt[valid].values, x_ind[valid].values]) if two_factor \
        else np.column_stack([np.ones(valid.sum()), x_mkt[valid].values])
    Y = y[valid].values
    try:
        coef, *_ = np.linalg.lstsq(X, Y, rcond=None)
    except Exception:
        issues.append(f"{ticker} {ev['date']}: 迴歸失敗")
        continue
    alpha = coef[0]; beta_mkt = coef[1]; beta_ind = coef[2] if two_factor else 0.0

    win_start, win_end = max(0, pos-10), min(len(common_all), pos+21)
    wy = sret.iloc[win_start:win_end]
    wm = mret.iloc[win_start:win_end]
    wi = iret.iloc[win_start:win_end]
    expected = alpha + beta_mkt*wm + beta_ind*wi
    ar = (wy - expected)
    ar.index = range(win_start-pos, win_end-pos)

    def car(lo, hi):
        keys = [k for k in range(lo, hi+1) if k in ar.index and not pd.isna(ar.loc[k])]
        need = hi - lo + 1
        if len(keys) < need:
            return np.nan, len(keys)
        return ar.loc[keys].sum(), len(keys)

    car_pre5, _ = car(-5, -1)
    car_post5, n5 = car(1, 5)
    car_post10, n10 = car(1, 10)
    car_post20, n20 = car(1, 20)
    if n20 < 20:
        issues.append(f"{ticker} {ev['date']}: 事件後+20天資料不足({n20}天) — 太接近資料終點")

    rows.append({
        "ticker": ticker, "industry": industry, "date": ev["date"], "direction": ev["direction"],
        "analyst_count": ev["analyst_count"], "beta_mkt": round(beta_mkt,3), "beta_ind": round(beta_ind,3),
        "two_factor": two_factor,
        "car_pre_5": car_pre5, "car_post_5": car_post5, "car_post_10": car_post10, "car_post_20": car_post20,
    })

df = pd.DataFrame(rows)
print(f"總事件數: {len(events)}  成功計算: {len(df)}  問題筆數: {len(issues)}  (雙因子模型涵蓋: {df['two_factor'].sum()}/{len(df)})")

with open("factset_data/full_issues.json", "w", encoding="utf-8") as f:
    json.dump(issues, f, ensure_ascii=False, indent=2)
df.to_csv("factset_data/full_event_study_results.csv", index=False)

def clustered_ttest(sub, col):
    valid = sub.dropna(subset=[col])
    if len(valid) == 0:
        return None
    # collapse to date-cluster means，降低同日事件相關造成的顯著性高估
    cluster_means = valid.groupby("date")[col].mean()
    n = len(cluster_means)
    mean = cluster_means.mean() * 100
    se = cluster_means.std() / np.sqrt(n) * 100 if n > 1 else np.nan
    tstat = mean/se if se and se > 0 else np.nan
    return {"n_events": len(valid), "n_date_clusters": n, "mean_pct": mean, "tstat_clustered": tstat}

print("\n=== 全樣本彙總（依方向，date-cluster標準誤）===")
for direction in ["UP", "DOWN"]:
    sub = df[df["direction"] == direction]
    print(f"\n--- {direction} (n={len(sub)}) ---")
    for col in ["car_pre_5", "car_post_5", "car_post_10", "car_post_20"]:
        r = clustered_ttest(sub, col)
        if r:
            print(f"  {col}: events={r['n_events']}, date群數={r['n_date_clusters']}, 平均={r['mean_pct']:.2f}%, t值(clustered)={r['tstat_clustered']:.2f}")

print("\n=== 依產業拆解（樣本數>=15的產業，UP方向 car_post_20）===")
ind_counts = df[df["direction"]=="UP"].groupby("industry").size().sort_values(ascending=False)
for ind, cnt in ind_counts.items():
    if cnt < 15:
        continue
    sub = df[(df["direction"]=="UP") & (df["industry"]==ind)]
    r = clustered_ttest(sub, "car_post_20")
    if r:
        print(f"  {ind} (n={cnt}): 平均car_post_20={r['mean_pct']:.2f}%, t值={r['tstat_clustered']:.2f}")
