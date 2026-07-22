"""
反轉訊號校準：驗證「反轉」定義門檻(streak_len/span_days/materiality)是不是真的
跟股價異常報酬連動，而不是憑感覺訂的數字。

方法：
1. 用events_target_price_full.json(全樣本，2023-2026)重建每一筆事件的
   streak_len/span_days，標記成 reversal(反轉) / continuation(延續) / untagged(前置趨勢未站穩)
2. 跟event_study_full.py算好的CAR(car_pre_5/post_5/10/20)用ticker+date對應
3. 先看目前定案的門檻(3次/30天/3%)下，反轉組 vs 延續組的股價表現差異
4. 再跑參數網格，測不同門檻組合下反轉組的訊號強度，找出實際上站得住腳的門檻
   (不是只信一組猜測值)
"""
import json
import numpy as np
import pandas as pd

events = json.load(open("factset_data/events_target_price_full.json"))
car_df = pd.read_csv("factset_data/full_event_study_results.csv", dtype={"ticker": str})
car_df["key"] = car_df["ticker"] + "_" + car_df["date"]

by_ticker = {}
for e in events:
    by_ticker.setdefault(e["ticker"], []).append(e)
for t in by_ticker:
    by_ticker[t].sort(key=lambda e: (e["date"], e["id"]))


def tag_events(streak_min, span_min_days, materiality_pct):
    """對每一檔股票的每一筆事件(從有足夠前置歷史的那筆開始)打標籤"""
    tags = []
    for ticker, evs in by_ticker.items():
        for i in range(streak_min, len(evs)):
            latest = evs[i]
            if latest.get("target_change_pct") is None:
                continue
            prior = evs[:i]
            streak_dir = prior[-1]["direction"]
            streak = []
            for e in reversed(prior):
                if e["direction"] == streak_dir:
                    streak.append(e)
                else:
                    break
            streak_len = len(streak)
            if streak_len < streak_min:
                continue
            span_days = (pd.Timestamp(latest["date"]) - pd.Timestamp(streak[-1]["date"])).days
            if span_days < span_min_days:
                continue
            is_reversal_dir = latest["direction"] != streak_dir
            materiality_ok = abs(latest["target_change_pct"]) >= materiality_pct
            if is_reversal_dir and materiality_ok:
                label = "reversal"
            elif not is_reversal_dir:
                label = "continuation"
            else:
                label = "untagged"  # 方向反了但材料性不足(雜訊級反向)
            tags.append({"ticker": ticker, "date": latest["date"], "label": label,
                         "streak_len": streak_len, "span_days": span_days})
    return pd.DataFrame(tags)


def clustered_stats(sub, col):
    valid = sub.dropna(subset=[col])
    if len(valid) < 5:
        return None
    cm = valid.groupby("date")[col].mean()
    n = len(cm)
    mean = cm.mean() * 100
    se = cm.std() / np.sqrt(n) * 100 if n > 1 else np.nan
    t = mean / se if se and se > 0 else np.nan
    wins = valid[valid[col] > 0]
    win_rate = len(wins) / len(valid) * 100
    return {"n": len(valid), "clusters": n, "mean_pct": mean, "t": t, "win_rate": win_rate}


print("=" * 70)
print("① 目前定案門檻(streak>=3, span>=30天, materiality>=3%) 下的訊號強度")
print("=" * 70)
tagged = tag_events(3, 30, 3.0)
tagged["key"] = tagged["ticker"] + "_" + tagged["date"]
merged = tagged.merge(car_df, on="key", suffixes=("", "_car"))
print(f"\n標記結果: reversal={  (tagged['label']=='reversal').sum()}, "
      f"continuation={(tagged['label']=='continuation').sum()}, "
      f"untagged(方向反了但材料性不足)={(tagged['label']=='untagged').sum()}")

for label in ["reversal", "continuation"]:
    sub = merged[merged["label"] == label]
    print(f"\n--- {label} (n={len(sub)}) ---")
    for col in ["car_pre_5", "car_post_5", "car_post_10", "car_post_20"]:
        r = clustered_stats(sub, col)
        if r:
            print(f"  {col}: n={r['n']}, clusters={r['clusters']}, 平均={r['mean_pct']:.2f}%, "
                  f"t={r['t']:.2f}, 勝率={r['win_rate']:.1f}%")

print("\n" + "=" * 70)
print("② 參數網格搜索：不同門檻組合下，reversal組 car_post_20 的訊號強度")
print("=" * 70)
print(f"{'streak_min':>10} {'span_min':>9} {'materiality':>11} {'n':>6} {'mean%':>8} {'t值':>7} {'勝率%':>7}")
results = []
for streak_min in [2, 3, 4, 5]:
    for span_min in [15, 30, 45, 60]:
        for materiality in [1.0, 3.0, 5.0]:
            tg = tag_events(streak_min, span_min, materiality)
            if len(tg) == 0:
                continue
            tg["key"] = tg["ticker"] + "_" + tg["date"]
            mg = tg.merge(car_df, on="key", suffixes=("", "_car"))
            sub = mg[mg["label"] == "reversal"]
            r = clustered_stats(sub, "car_post_20")
            if r:
                results.append({"streak_min": streak_min, "span_min": span_min, "materiality": materiality, **r})
                print(f"{streak_min:>10} {span_min:>9} {materiality:>11.0f} {r['n']:>6} "
                      f"{r['mean_pct']:>8.2f} {r['t']:>7.2f} {r['win_rate']:>7.1f}")

grid_df = pd.DataFrame(results)
grid_df.to_csv("factset_data/reversal_calibration_grid.csv", index=False)
print(f"\n完整網格結果存到 factset_data/reversal_calibration_grid.csv ({len(grid_df)}組)")

print("\n" + "=" * 70)
print("③ 網格裡 |t值| 最大的前5組合(僅供參考，注意多重檢定問題——不要只挑最漂亮那組就definir)")
print("=" * 70)
top5 = grid_df.reindex(grid_df["t"].abs().sort_values(ascending=False).index).head(5)
print(top5.to_string(index=False))
