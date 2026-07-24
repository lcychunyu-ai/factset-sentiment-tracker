"""
事件後波動度(風險)測試：不看平均報酬有沒有偏移，看事件後報酬的「離散程度」
有沒有放大——就算沒有方向性優勢，波動度放大本身對倉位管理/風控就有價值
(例如縮小部位、放寬停損、避免被洗出場)。

方法：對每個事件，比較估計窗[-250,-30]的AR標準差(平常的波動基準) vs
事件窗[+1,+20]的AR標準差(事件後)，算比值，檢定比值的分布是否顯著>1。

2026-07-24更新：產業基準改市值加權(原等權重)+樣本數<3檔退化成大盤(原<1檔)，
理由同event_study_full.py同一天的更新記錄，兩版結果差異在誤差範圍內。
"""
import json
import numpy as np
import pandas as pd

events = [e for e in json.load(open("factset_data/events_unified_target_full.json")) if e.get("direction") in ("UP", "DOWN")]
prices_raw = json.load(open("factset_data/prices_full.json"))
taiex_raw = json.load(open("factset_data/taiex_full.json"))
shares = json.load(open("factset_data/shares_outstanding.json"))

taiex = pd.Series(taiex_raw).sort_index()
taiex.index = pd.to_datetime(taiex.index)
taiex_ret = taiex.pct_change().dropna()
common_all = taiex_ret.index

stock_ret = {}
stock_price = {}
for t, d in prices_raw.items():
    ser = pd.Series(d["prices"]).sort_index()
    ser.index = pd.to_datetime(ser.index)
    stock_price[t] = ser.reindex(common_all)
    stock_ret[t] = ser.pct_change().dropna().reindex(common_all)

ticker_industry = {e["ticker"]: e["industry_canonical"] for e in events}
industry_tickers = {}
for t, ind in ticker_industry.items():
    industry_tickers.setdefault(ind, []).append(t)

MIN_INDUSTRY_MEMBERS = 3

def industry_benchmark(industry, exclude_ticker):
    members = [t for t in industry_tickers.get(industry, []) if t != exclude_ticker and t in stock_ret]
    if len(members) < MIN_INDUSTRY_MEMBERS:
        return None
    return pd.concat([stock_ret[t] for t in members], axis=1).mean(axis=1, skipna=True)

rows = []
for ev in events:
    ticker = ev["ticker"]; industry = ev["industry_canonical"]
    ev_date = pd.Timestamp(ev["date"])
    if ticker not in stock_ret:
        continue
    sret = stock_ret[ticker]; mret = taiex_ret
    iret = industry_benchmark(industry, ticker)
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

    # 估計窗本身的AR標準差(平常基準)
    est_expected = alpha + beta_mkt*xm[valid] + beta_ind*xi[valid]
    est_ar = y[valid] - est_expected
    est_vol = est_ar.std()

    # 事件後窗口[+1,+20]的AR標準差
    win_start, win_end = pos+1, min(len(common_all), pos+21)
    wy = sret.iloc[win_start:win_end]; wm = mret.iloc[win_start:win_end]; wi = iret.iloc[win_start:win_end]
    post_expected = alpha + beta_mkt*wm + beta_ind*wi
    post_ar = (wy - post_expected).dropna()
    if len(post_ar) < 15 or est_vol == 0:
        continue
    post_vol = post_ar.std()

    rows.append({"ticker": ticker, "industry": industry, "date": ev["date"], "direction": ev["direction"],
                 "est_vol": est_vol, "post_vol": post_vol, "vol_ratio": post_vol/est_vol})

df = pd.DataFrame(rows)
print(f"樣本數: {len(df)}")
print(f"\n事件後波動度/平常波動度 比值分布：")
print(f"  平均比值: {df['vol_ratio'].mean():.3f}  中位數: {df['vol_ratio'].median():.3f}")
print(f"  比值>1(波動放大)比例: {(df['vol_ratio']>1).mean()*100:.1f}%")
print(f"  比值>1.5(波動放大50%以上)比例: {(df['vol_ratio']>1.5).mean()*100:.1f}%")

# 對log(vol_ratio)做t檢定(比值取log更接近常態，檢定"是否顯著偏離0"=vol_ratio是否顯著偏離1)
log_ratio = np.log(df['vol_ratio'])
mean_log = log_ratio.mean()
se_log = log_ratio.std()/np.sqrt(len(log_ratio))
t_stat = mean_log/se_log
print(f"\n  log(vol_ratio)均值={mean_log:.4f}, t值={t_stat:.2f} (檢定波動度是否顯著偏離平常水準)")

print(f"\n=== 依方向拆解 ===")
for d in ["UP","DOWN"]:
    sub = df[df["direction"]==d]
    lr = np.log(sub['vol_ratio'])
    t = lr.mean()/(lr.std()/np.sqrt(len(lr)))
    print(f"{d} (n={len(sub)}): 平均比值={sub['vol_ratio'].mean():.3f}, t值={t:.2f}")

df.to_csv("factset_data/event_volatility_results.csv", index=False)
