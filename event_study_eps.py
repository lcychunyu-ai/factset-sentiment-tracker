"""
Event Study - EPS修正事件版本
用跟target_price事件完全相同的方法(市場+產業雙因子, [-250,-30]估計窗, date-cluster標準誤)
測試：EPS修正(要求分析師重跑財模型) 的事後CAR，是否比target price修正(較機械化)更強
"""
import json
import numpy as np
import pandas as pd
import yfinance as yf

events = json.load(open("factset_data/eps_events.json"))
prices_raw = json.load(open("factset_data/full_prices.json"))

df_taiex = yf.download("^TWII", start="2021-01-01", end="2026-07-22", progress=False, auto_adjust=True)
s = df_taiex["Close"]["^TWII"] if hasattr(df_taiex["Close"], "columns") else df_taiex["Close"]
taiex = s.dropna()
taiex.index = pd.to_datetime(taiex.index)
taiex_ret = taiex.pct_change().dropna()

stock_ret = {}
for t, d in prices_raw.items():
    ser = pd.Series(d["prices"]).sort_index()
    ser.index = pd.to_datetime(ser.index)
    stock_ret[t] = ser.pct_change().dropna().reindex(taiex_ret.index)

ticker_industry = {}
for e in events:
    if e.get("industry_canonical"):
        ticker_industry[e["ticker"]] = e["industry_canonical"]
industry_tickers = {}
for t, ind in ticker_industry.items():
    industry_tickers.setdefault(ind, []).append(t)

def industry_benchmark_loo(industry, exclude_ticker):
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
        issues.append(f"{ticker} {ev['date']}: 無股價"); continue

    sret = stock_ret[ticker]
    mret = taiex_ret
    iret = industry_benchmark_loo(industry, ticker) if industry else None
    two_factor = iret is not None
    if iret is None:
        iret = pd.Series(0.0, index=common_all)
    else:
        iret = iret.reindex(common_all)

    if ev_date not in common_all:
        after = common_all[common_all >= ev_date]
        if len(after) == 0:
            issues.append(f"{ticker} {ev['date']}: 超出資料範圍"); continue
        ev_date_aligned = after[0]
    else:
        ev_date_aligned = ev_date
    pos = common_all.get_loc(ev_date_aligned)

    est_start, est_end = pos-250, pos-30
    if est_start < 0:
        issues.append(f"{ticker} {ev['date']}: 估計窗口不足"); continue

    y = sret.iloc[est_start:est_end]; xm = mret.iloc[est_start:est_end]; xi = iret.iloc[est_start:est_end]
    valid = y.notna() & xm.notna() & xi.notna()
    if valid.sum() < 100:
        issues.append(f"{ticker} {ev['date']}: 有效樣本不足"); continue

    X = np.column_stack([np.ones(valid.sum()), xm[valid].values, xi[valid].values]) if two_factor \
        else np.column_stack([np.ones(valid.sum()), xm[valid].values])
    coef, *_ = np.linalg.lstsq(X, y[valid].values, rcond=None)
    alpha, beta_mkt = coef[0], coef[1]
    beta_ind = coef[2] if two_factor else 0.0

    win_start, win_end = max(0,pos-10), min(len(common_all),pos+21)
    wy, wm_, wi_ = sret.iloc[win_start:win_end], mret.iloc[win_start:win_end], iret.iloc[win_start:win_end]
    ar = wy - (alpha + beta_mkt*wm_ + beta_ind*wi_)
    ar.index = range(win_start-pos, win_end-pos)

    def car(lo,hi):
        keys=[k for k in range(lo,hi+1) if k in ar.index and not pd.isna(ar.loc[k])]
        need=hi-lo+1
        return (ar.loc[keys].sum(), len(keys)) if len(keys)>=need else (np.nan,len(keys))

    car_pre5,_ = car(-5,-1)
    car_post5,_ = car(1,5)
    car_post10,_ = car(1,10)
    car_post20,n20 = car(1,20)
    if n20 < 20:
        issues.append(f"{ticker} {ev['date']}: +20天資料不足")

    rows.append({"ticker":ticker,"date":ev["date"],"direction":ev["direction"],"two_factor":two_factor,
                 "car_pre_5":car_pre5,"car_post_5":car_post5,"car_post_10":car_post10,"car_post_20":car_post20})

result = pd.DataFrame(rows)
print(f"EPS事件總數:{len(events)} 成功計算:{len(result)} 問題:{len(issues)} 雙因子涵蓋:{result['two_factor'].sum()}/{len(result)}")
result.to_csv("factset_data/eps_event_study_results.csv", index=False)

def clustered_ttest(sub, col):
    valid = sub.dropna(subset=[col])
    if len(valid) < 3: return None
    cm = valid.groupby("date")[col].mean()
    n = len(cm); mean = cm.mean()*100
    se = cm.std()/np.sqrt(n)*100 if n>1 else np.nan
    return {"n":len(valid),"clusters":n,"mean":mean,"median":valid[col].median()*100,
            "t": mean/se if se and se>0 else np.nan}

print("\n=== EPS修正事件 CAR（依方向）===")
for d in ["UP","DOWN"]:
    sub = result[result["direction"]==d]
    print(f"\n--- {d} (n={len(sub)}) ---")
    for col in ["car_pre_5","car_post_5","car_post_10","car_post_20"]:
        r = clustered_ttest(sub, col)
        if r:
            print(f"  {col}: n={r['n']}, clusters={r['clusters']}, 平均={r['mean']:.2f}%, 中位數={r['median']:.2f}%, t={r['t']:.2f}")

print("\n=== 對照：target price修正事件（重跑前次結果） ===")
tp = pd.read_csv("factset_data/full_event_study_results.csv")
for d in ["UP","DOWN"]:
    sub = tp[tp["direction"]==d]
    print(f"\n--- TP {d} (n={len(sub)}) ---")
    for col in ["car_pre_5","car_post_5","car_post_10","car_post_20"]:
        r = clustered_ttest(sub, col)
        if r:
            print(f"  {col}: 平均={r['mean']:.2f}%, 中位數={r['median']:.2f}%, t={r['t']:.2f}")
