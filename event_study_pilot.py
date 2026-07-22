"""
Event Study Pilot: 驗證FactSet目標價修正的異常報酬(AR/CAR)
三個pilot產業：半導體業、電子零組件業、電腦及週邊設備業

方法：
- 估計窗口 [-250,-30] 交易日：對每支股票跑 R_i,t = alpha + beta * R_market,t 迴歸
- 事件窗口 [-10,+20] 交易日：AR_i,t = R_i,t - (alpha + beta * R_market,t)
- CAR_pre = sum(AR, [-5,-1])；CAR_post_N = sum(AR, [+1,+N])  N=5,10,20
"""
import json
import numpy as np
import pandas as pd

events = json.load(open("factset_data/pilot_events.json"))
prices_raw = json.load(open("factset_data/pilot_prices.json"))
taiex_raw = json.load(open("factset_data/pilot_taiex.json"))

taiex = pd.Series(taiex_raw).sort_index()
taiex.index = pd.to_datetime(taiex.index)
taiex_ret = taiex.pct_change().dropna()

stock_ret = {}
for t, d in prices_raw.items():
    s = pd.Series(d["prices"]).sort_index()
    s.index = pd.to_datetime(s.index)
    stock_ret[t] = s.pct_change().dropna()

issues = []
rows = []

for ev in events:
    ticker = ev["ticker"]
    ev_date = pd.Timestamp(ev["date"])
    if ticker not in stock_ret:
        issues.append(f"{ticker} {ev['date']}: 無股價資料")
        continue

    sret = stock_ret[ticker]
    common_idx = sret.index.intersection(taiex_ret.index)
    sret = sret.reindex(common_idx)
    mret = taiex_ret.reindex(common_idx)

    if ev_date not in common_idx:
        # 找最近的交易日
        after = common_idx[common_idx >= ev_date]
        if len(after) == 0:
            issues.append(f"{ticker} {ev['date']}: 事件日期在資料範圍外")
            continue
        ev_date_aligned = after[0]
    else:
        ev_date_aligned = ev_date

    pos = common_idx.get_loc(ev_date_aligned)

    est_start = pos - 250
    est_end = pos - 30
    if est_start < 0:
        issues.append(f"{ticker} {ev['date']}: 估計窗口不足250天(僅{pos}天可用) — 可能是近期新上市或資料起點不夠早")
        continue

    est_x = mret.iloc[est_start:est_end].values
    est_y = sret.iloc[est_start:est_end].values
    if len(est_x) < 100 or np.std(est_x) == 0:
        issues.append(f"{ticker} {ev['date']}: 估計窗口樣本太少或大盤報酬無變異")
        continue

    beta, alpha = np.polyfit(est_x, est_y, 1)

    win_start = max(0, pos - 10)
    win_end = min(len(common_idx), pos + 21)
    window_ret = sret.iloc[win_start:win_end]
    window_mret = mret.iloc[win_start:win_end]
    ar = window_ret - (alpha + beta * window_mret)
    ar.index = range(win_start - pos, win_end - pos)

    car_pre = ar.loc[-5:-1].sum() if all(k in ar.index for k in range(-5,0)) else np.nan
    car_post = {}
    for n in [5, 10, 20]:
        keys = [k for k in range(1, n+1) if k in ar.index]
        car_post[n] = ar.loc[keys].sum() if len(keys) == n else np.nan
        if len(keys) < n:
            issues.append(f"{ticker} {ev['date']}: 事件後+{n}天資料不足(僅{len(keys)}天可用，太接近資料終點2026-07-21)")

    rows.append({
        "ticker": ticker, "date": ev["date"], "direction": ev["direction"],
        "analyst_count": ev["analyst_count"], "beta": round(beta,3),
        "car_pre_5": car_pre, "car_post_5": car_post[5], "car_post_10": car_post[10], "car_post_20": car_post[20],
    })

df = pd.DataFrame(rows)
print(f"總事件數: {len(events)}  成功計算: {len(df)}  有問題筆數: {len(issues)}")
print(f"\n=== 問題清單抽樣（前15筆）===")
for i in issues[:15]:
    print(" -", i)
print(f"... 共 {len(issues)} 筆問題" if len(issues) > 15 else "")

print(f"\n=== 依方向彙總 CAR（扣除大盤beta後的累積異常報酬）===")
for direction in ["UP", "DOWN"]:
    sub = df[df["direction"] == direction]
    print(f"\n--- {direction} (n={len(sub)}) ---")
    for col in ["car_pre_5", "car_post_5", "car_post_10", "car_post_20"]:
        valid = sub[col].dropna()
        if len(valid) == 0:
            print(f"  {col}: 無有效樣本")
            continue
        mean = valid.mean() * 100
        se = valid.std() / np.sqrt(len(valid)) * 100
        tstat = mean / se if se > 0 else np.nan
        print(f"  {col}: n={len(valid)}, 平均={mean:.2f}%, t值={tstat:.2f}")

df.to_csv("factset_data/pilot_event_study_results.csv", index=False)
with open("factset_data/pilot_issues.json", "w", encoding="utf-8") as f:
    json.dump(issues, f, ensure_ascii=False, indent=2)
