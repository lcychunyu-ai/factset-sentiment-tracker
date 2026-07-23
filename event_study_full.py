"""
Event Study：目標價修正事件版本
資料來源：build_event_dataset.py / build_price_dataset.py / build_unified_target_series.py
產出的完整、去重、有分頁保證的資料集(2023-2026全樣本)。

2026-07-23更新：改用「統一目標價序列」(events_unified_target_full.json)當主要樣本，
取代原本只認event_type='TARGET_PRICE'的版本——EPS快訊裡也有內嵌目標價，65%的
EPS內嵌目標價前後7天內完全沒有TARGET_PRICE報告可對照，是被忽略的獨立資訊。
純TARGET_PRICE版本(events_target_price_full.json)保留作對照，兩者都跑，方便比較差異。

方法：市場模型 alpha+beta_mkt*R_mkt+beta_ind*R_ind，估計窗[-250,-30]交易日，
事件窗[-10,+20]交易日，AR/CAR，date-cluster標準誤。
"""
import json
import numpy as np
import pandas as pd

prices_raw = json.load(open("factset_data/prices_full.json"))
taiex_raw = json.load(open("factset_data/taiex_full.json"))

taiex = pd.Series(taiex_raw).sort_index()
taiex.index = pd.to_datetime(taiex.index)
taiex_ret = taiex.pct_change().dropna()
common_all = taiex_ret.index

stock_ret = {}
for t, d in prices_raw.items():
    ser = pd.Series(d["prices"]).sort_index()
    ser.index = pd.to_datetime(ser.index)
    stock_ret[t] = ser.pct_change().dropna().reindex(common_all)


def industry_benchmark_leave_one_out(industry_tickers, industry, exclude_ticker):
    members = [t for t in industry_tickers.get(industry, []) if t != exclude_ticker and t in stock_ret]
    if not members:
        return None
    mat = pd.concat([stock_ret[t] for t in members], axis=1)
    return mat.mean(axis=1, skipna=True)


def clustered_ttest(sub, col):
    valid = sub.dropna(subset=[col])
    if len(valid) == 0:
        return None
    cluster_means = valid.groupby("date")[col].mean()
    n = len(cluster_means)
    mean = cluster_means.mean() * 100
    se = cluster_means.std() / np.sqrt(n) * 100 if n > 1 else np.nan
    tstat = mean / se if se and se > 0 else np.nan
    return {"n_events": len(valid), "n_date_clusters": n, "mean_pct": mean, "tstat_clustered": tstat}


def win_rate_payoff(sub, col):
    valid = sub.dropna(subset=[col])
    if len(valid) == 0:
        return None
    wins = valid[valid[col] > 0][col]
    losses = valid[valid[col] < 0][col]
    win_rate = len(wins) / len(valid) * 100
    payoff = (wins.mean() / abs(losses.mean())) if len(losses) > 0 and len(wins) > 0 else np.nan
    return {"win_rate": win_rate, "payoff_ratio": payoff, "n": len(valid)}


def run_event_study(events_path, out_csv, out_issues, label):
    events = [e for e in json.load(open(events_path)) if e.get("direction") in ("UP", "DOWN")]
    ticker_industry = {e["ticker"]: e["industry_canonical"] for e in events}
    industry_tickers = {}
    for t, ind in ticker_industry.items():
        industry_tickers.setdefault(ind, []).append(t)

    issues = []
    rows = []
    for ev in events:
        ticker = ev["ticker"]
        industry = ev["industry_canonical"]
        ev_date = pd.Timestamp(ev["date"])
        if ticker not in stock_ret:
            issues.append(f"{ticker} {ev['date']}: 無股價資料(可能已下市/併購/代號變更)")
            continue

        sret = stock_ret[ticker]
        mret = taiex_ret
        iret = industry_benchmark_leave_one_out(industry_tickers, industry, ticker)
        if iret is None:
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
            issues.append(f"{ticker} {ev['date']}: 估計窗口不足250天(僅{pos}天可用) — 近期新上市")
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

        win_start, win_end = max(0, pos - 10), min(len(common_all), pos + 21)
        wy = sret.iloc[win_start:win_end]
        wm = mret.iloc[win_start:win_end]
        wi = iret.iloc[win_start:win_end]
        expected = alpha + beta_mkt * wm + beta_ind * wi
        ar = (wy - expected)
        ar.index = range(win_start - pos, win_end - pos)

        def car(lo, hi):
            keys = [k for k in range(lo, hi + 1) if k in ar.index and not pd.isna(ar.loc[k])]
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
            "analyst_count": ev.get("analyst_count"), "target_change_pct": ev.get("target_change_pct"),
            "beta_mkt": round(beta_mkt, 3), "beta_ind": round(beta_ind, 3), "two_factor": two_factor,
            "car_pre_5": car_pre5, "car_post_5": car_post5, "car_post_10": car_post10, "car_post_20": car_post20,
        })

    df = pd.DataFrame(rows)
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    print(f"總事件數: {len(events)}  成功計算: {len(df)}  問題筆數: {len(issues)}  (雙因子模型涵蓋: {df['two_factor'].sum()}/{len(df)})")
    print(f"日期範圍: {df['date'].min()} ~ {df['date'].max()}  股票數: {df['ticker'].nunique()}")

    with open(out_issues, "w", encoding="utf-8") as f:
        json.dump(issues, f, ensure_ascii=False, indent=2)
    df.to_csv(out_csv, index=False)

    print("\n--- 全樣本彙總（依方向，date-cluster標準誤）---")
    for direction in ["UP", "DOWN"]:
        sub = df[df["direction"] == direction]
        print(f"\n{direction} (n={len(sub)})")
        for col in ["car_pre_5", "car_post_5", "car_post_10", "car_post_20"]:
            r = clustered_ttest(sub, col)
            if r:
                print(f"  {col}: events={r['n_events']}, date群數={r['n_date_clusters']}, 平均={r['mean_pct']:.2f}%, t值={r['tstat_clustered']:.2f}")
        wp = win_rate_payoff(sub, "car_post_20")
        if wp:
            print(f"  car_post_20 勝率/盈虧比: n={wp['n']}, 勝率={wp['win_rate']:.1f}%, 盈虧比={wp['payoff_ratio']:.2f}")
    return df


if __name__ == "__main__":
    df_tp_only = run_event_study(
        "factset_data/events_target_price_full.json",
        "factset_data/full_event_study_results_tp_only.csv",
        "factset_data/full_issues_tp_only.json",
        "①純TARGET_PRICE版本(僅目標價修正報告)",
    )
    df_eps_only = run_event_study(
        "factset_data/events_eps_only_target_full.json",
        "factset_data/full_event_study_results_eps_only.csv",
        "factset_data/full_issues_eps_only.json",
        "②純EPS內嵌目標價版本(僅EPS快訊附帶的預估目標價)",
    )
    df_unified = run_event_study(
        "factset_data/events_unified_target_full.json",
        "factset_data/full_event_study_results.csv",
        "factset_data/full_issues.json",
        "③統一目標價序列版本(①+②合併，正式標準)",
    )
