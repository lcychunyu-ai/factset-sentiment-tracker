"""
波動度分析：市值加權版 vs 等權重版 vs 產業樣本門檻>=3 對照測試
"""
import json
import numpy as np
import pandas as pd

events = [e for e in json.load(open("factset_data/events_unified_target_full.json")) if e.get("direction") in ("UP", "DOWN")]
prices_raw = json.load(open("factset_data/prices_full.json"))
taiex_raw = json.load(open("factset_data/taiex_full.json"))
shares = json.load(open("factset_data/shares_outstanding.json"))

taiex = pd.Series(taiex_raw).sort_index(); taiex.index = pd.to_datetime(taiex.index)
taiex_ret = taiex.pct_change().dropna()
common_all = taiex_ret.index

stock_ret = {}
stock_price = {}
for t, d in prices_raw.items():
    ser = pd.Series(d["prices"]).sort_index(); ser.index = pd.to_datetime(ser.index)
    stock_price[t] = ser.reindex(common_all)
    stock_ret[t] = ser.pct_change().dropna().reindex(common_all)

ticker_industry = {e["ticker"]: e["industry_canonical"] for e in events}
industry_tickers = {}
for t, ind in ticker_industry.items():
    industry_tickers.setdefault(ind, []).append(t)


def industry_benchmark(industry, exclude_ticker, weighting, min_members):
    members = [t for t in industry_tickers.get(industry, []) if t != exclude_ticker and t in stock_ret]
    if len(members) < min_members:
        return None
    if weighting == "equal":
        return pd.concat([stock_ret[t] for t in members], axis=1).mean(axis=1, skipna=True)
    else:  # market-cap weighted
        rets = pd.concat([stock_ret[t] for t in members], axis=1, keys=members)
        caps = pd.concat([stock_price[t] * shares.get(t, 0) for t in members], axis=1, keys=members)
        caps = caps.shift(1)  # 用前一天市值當權重，避免用當天報酬去加權當天報酬造成內生性
        w = caps.div(caps.sum(axis=1), axis=0)
        return (rets * w).sum(axis=1, min_count=1)


def run(weighting, min_members, label):
    rows = []
    for ev in events:
        ticker = ev["ticker"]; industry = ev["industry_canonical"]
        ev_date = pd.Timestamp(ev["date"])
        if ticker not in stock_ret:
            continue
        sret = stock_ret[ticker]; mret = taiex_ret
        iret = industry_benchmark(industry, ticker, weighting, min_members)
        two_factor = iret is not None
        iret = iret.reindex(common_all) if iret is not None else pd.Series(0.0, index=common_all)

        if ev_date not in common_all:
            after = common_all[common_all >= ev_date]
            if len(after) == 0: continue
            ev_date = after[0]
        pos = common_all.get_loc(ev_date)
        est_start, est_end = pos-250, pos-30
        if est_start < 0: continue

        y = sret.iloc[est_start:est_end]; xm = mret.iloc[est_start:est_end]; xi = iret.iloc[est_start:est_end]
        valid = y.notna() & xm.notna() & xi.notna()
        if valid.sum() < 100: continue
        X = np.column_stack([np.ones(valid.sum()), xm[valid].values, xi[valid].values]) if two_factor \
            else np.column_stack([np.ones(valid.sum()), xm[valid].values])
        coef,*_ = np.linalg.lstsq(X, y[valid].values, rcond=None)
        alpha, beta_mkt = coef[0], coef[1]; beta_ind = coef[2] if two_factor else 0.0
        est_expected = alpha + beta_mkt*xm[valid] + beta_ind*xi[valid]
        est_ar = y[valid] - est_expected
        est_vol = est_ar.std()
        if est_vol == 0: continue

        win_start, win_end = pos+1, min(len(common_all), pos+21)
        wy = sret.iloc[win_start:win_end]; wm = mret.iloc[win_start:win_end]; wi = iret.iloc[win_start:win_end]
        post_ar = (wy - (alpha+beta_mkt*wm+beta_ind*wi)).dropna()
        if len(post_ar) < 15: continue
        post_vol = post_ar.std()

        rows.append({"ticker": ticker, "date": ev["date"], "direction": ev["direction"],
                     "vol_ratio": post_vol/est_vol, "two_factor": two_factor})

    df = pd.DataFrame(rows)
    print(f"\n=== {label} (n={len(df)}, 雙因子涵蓋{df['two_factor'].sum()}/{len(df)}) ===")
    for d in ["UP", "DOWN"]:
        sub = df[df["direction"]==d].copy()
        sub["log_ratio"] = np.log(sub["vol_ratio"])
        cm = sub.groupby("date")["log_ratio"].mean()
        n = len(cm); mean = cm.mean(); se = cm.std()/np.sqrt(n)
        t = mean/se
        print(f"  {d}: n={len(sub)}, 日期群={n}, 比值={np.exp(mean):.4f}({(np.exp(mean)-1)*100:+.1f}%), t={t:.2f}")
    return df


run("equal", 1, "①等權重、門檻>=1(現有做法)")
run("equal", 3, "②等權重、門檻>=3")
run("cap", 1, "③市值加權、門檻>=1")
run("cap", 3, "④市值加權、門檻>=3(建議的新方法)")
