"""
Event Study：EPS修正事件版本
資料來源：build_event_dataset.py / build_price_dataset.py 產出的完整、去重、
有分頁保證的資料集(2023-2026全樣本)，非任何截斷/取樣版本。
方法跟 event_study_full.py(目標價修正版) 完全相同，用來對照EPS修正訊號強弱。
"""
import json
import numpy as np
import pandas as pd

events = json.load(open("factset_data/events_eps_full.json"))
prices_raw = json.load(open("factset_data/prices_full.json"))
taiex_raw = json.load(open("factset_data/taiex_full.json"))

taiex = pd.Series(taiex_raw).sort_index()
taiex.index = pd.to_datetime(taiex.index)
taiex_ret = taiex.pct_change().dropna()

stock_ret = {}
for t, d in prices_raw.items():
    ser = pd.Series(d["prices"]).sort_index()
    ser.index = pd.to_datetime(ser.index)
    stock_ret[t] = ser.pct_change().dropna().reindex(taiex_ret.index)

ticker_industry = {e["ticker"]: e["industry_canonical"] for e in events if e.get("industry_canonical")}
industry_tickers = {}
for t, ind in ticker_industry.items():
    industry_tickers.setdefault(ind, []).append(t)


def industry_benchmark_leave_one_out(industry, exclude_ticker):
    members = [t for t in industry_tickers.get(industry, []) if t != exclude_ticker and t in stock_ret]
    if not members:
        return None
    return pd.concat([stock_ret[t] for t in members], axis=1).mean(axis=1, skipna=True)


common_all = taiex_ret.index
issues = []
rows = []

for ev in events:
    ticker = ev["ticker"]
    industry = ev.get("industry_canonical")
    ev_date = pd.Timestamp(ev["date"])
    if ticker not in stock_ret:
        issues.append(f"{ticker} {ev['date']}: 無股價資料(可能已下市/併購/代號變更)")
        continue

    sret = stock_ret[ticker]
    mret = taiex_ret
    iret = industry_benchmark_leave_one_out(industry, ticker) if industry else None
    two_factor = iret is not None
    iret = iret.reindex(common_all) if iret is not None else pd.Series(0.0, index=common_all)

    if ev_date not in common_all:
        after = common_all[common_all >= ev_date]
        if len(after) == 0:
            issues.append(f"{ticker} {ev['date']}: 事件日期在資料範圍外")
            continue
        ev_date = after[0]
    pos = common_all.get_loc(ev_date)

    est_start, est_end = pos - 250, pos - 30
    if est_start < 0:
        issues.append(f"{ticker} {ev['date']}: 估計窗口不足250天(僅{pos}天可用) — 近期新上市")
        continue

    y = sret.iloc[est_start:est_end]
    xm = mret.iloc[est_start:est_end]
    xi = iret.iloc[est_start:est_end]
    valid = y.notna() & xm.notna() & xi.notna()
    if valid.sum() < 100:
        issues.append(f"{ticker} {ev['date']}: 估計窗口有效樣本不足({valid.sum()})")
        continue

    X = np.column_stack([np.ones(valid.sum()), xm[valid].values, xi[valid].values]) if two_factor \
        else np.column_stack([np.ones(valid.sum()), xm[valid].values])
    coef, *_ = np.linalg.lstsq(X, y[valid].values, rcond=None)
    alpha, beta_mkt = coef[0], coef[1]
    beta_ind = coef[2] if two_factor else 0.0

    win_start, win_end = max(0, pos - 10), min(len(common_all), pos + 21)
    wy, wm_, wi_ = sret.iloc[win_start:win_end], mret.iloc[win_start:win_end], iret.iloc[win_start:win_end]
    ar = wy - (alpha + beta_mkt * wm_ + beta_ind * wi_)
    ar.index = range(win_start - pos, win_end - pos)

    def car(lo, hi):
        keys = [k for k in range(lo, hi + 1) if k in ar.index and not pd.isna(ar.loc[k])]
        need = hi - lo + 1
        return (ar.loc[keys].sum(), len(keys)) if len(keys) >= need else (np.nan, len(keys))

    car_pre5, _ = car(-5, -1)
    car_post5, _ = car(1, 5)
    car_post10, _ = car(1, 10)
    car_post20, n20 = car(1, 20)
    if n20 < 20:
        issues.append(f"{ticker} {ev['date']}: 事件後+20天資料不足({n20}天) — 太接近資料終點")

    rows.append({
        "ticker": ticker, "date": ev["date"], "direction": ev["direction"], "two_factor": two_factor,
        "car_pre_5": car_pre5, "car_post_5": car_post5, "car_post_10": car_post10, "car_post_20": car_post20,
    })

result = pd.DataFrame(rows)
print(f"EPS事件總數: {len(events)}  成功計算: {len(result)}  問題筆數: {len(issues)}  (雙因子模型涵蓋: {result['two_factor'].sum()}/{len(result)})")
print(f"日期範圍: {result['date'].min()} ~ {result['date'].max()}")

with open("factset_data/eps_issues.json", "w", encoding="utf-8") as f:
    json.dump(issues, f, ensure_ascii=False, indent=2)
result.to_csv("factset_data/eps_event_study_results.csv", index=False)


def clustered_ttest(sub, col):
    valid = sub.dropna(subset=[col])
    if len(valid) < 3:
        return None
    cm = valid.groupby("date")[col].mean()
    n = len(cm)
    mean = cm.mean() * 100
    se = cm.std() / np.sqrt(n) * 100 if n > 1 else np.nan
    return {"n": len(valid), "clusters": n, "mean": mean, "median": valid[col].median() * 100,
            "t": mean / se if se and se > 0 else np.nan}


print("\n=== EPS修正事件 CAR（依方向，完整2023-2026資料）===")
for d in ["UP", "DOWN"]:
    sub = result[result["direction"] == d]
    print(f"\n--- {d} (n={len(sub)}) ---")
    for col in ["car_pre_5", "car_post_5", "car_post_10", "car_post_20"]:
        r = clustered_ttest(sub, col)
        if r:
            print(f"  {col}: n={r['n']}, clusters={r['clusters']}, 平均={r['mean']:.2f}%, 中位數={r['median']:.2f}%, t={r['t']:.2f}")

print("\n=== 對照：目標價修正事件（同方法論，見 event_study_full.py 結果） ===")
tp = pd.read_csv("factset_data/full_event_study_results.csv")
for d in ["UP", "DOWN"]:
    sub = tp[tp["direction"] == d]
    print(f"\n--- TP {d} (n={len(sub)}) ---")
    for col in ["car_pre_5", "car_post_5", "car_post_10", "car_post_20"]:
        r = clustered_ttest(sub, col)
        if r:
            print(f"  {col}: 平均={r['mean']:.2f}%, 中位數={r['median']:.2f}%, t={r['t']:.2f}")
