"""
統一目標價序列：把TARGET_PRICE事件的old_target/new_target，跟EPS快訊裡內嵌的
new_target合併成一條連續的每檔股票目標價觀測序列，不再只看event_type='TARGET_PRICE'。

背景：檢查發現EPS快訊裡也會附帶「預估目標價」(new_target有值，old_target沒有)，
且65%的EPS內嵌目標價前後7天內完全沒有對應的TARGET_PRICE報告可對照——是被完全
忽略的獨立資訊，不是重複資料。只看TARGET_PRICE事件會讓目標價序列跳動過快
(中位數變動3.57%)，納入EPS內嵌目標價後更平滑(中位數變動1.40%)，且能填補
長達整年(如2330在2023-04~2024-04)完全沒有TARGET_PRICE報告、但目標價其實
持續在動的空窗期。

作法：
1. 抓全部new_target不為null的revisions(不分event_type)
2. 同一天同一檔股票如果TARGET_PRICE跟EPS都有回報，優先採用TARGET_PRICE
   (該篇文章本身就有明確old/new對照，較權威)；同type多篇取analyst_count最高者
3. 按時間排序後，用「該股上一個已知目標價觀測點」(不論來源)當基準，
   重新算方向(implied_direction)跟幅度(implied_change_pct)——這才是拿去做
   溫度計算/event study該用的方向跟幅度，不是原始individual revision自帶的
   old_target/new_target對照(那是分析師報告當下自己的對照基準，兩者不同)

輸出：factset_data/events_unified_target_full.json
"""
import json
import pandas as pd
from build_event_dataset import fetch_all

TP_SELECT = "id,date,ticker,company,event_type,new_target,analyst_count,industry_canonical"


def dedup_same_day_prefer_tp(events):
    """同一天同股票，優先保留TARGET_PRICE；同type多篇取analyst_count最高"""
    best = {}
    for e in events:
        key = (e["ticker"], e["date"])
        cur = best.get(key)
        if cur is None:
            best[key] = e
            continue
        cur_score = (1 if cur["event_type"] == "TARGET_PRICE" else 0, cur.get("analyst_count") or 0, cur["id"])
        new_score = (1 if e["event_type"] == "TARGET_PRICE" else 0, e.get("analyst_count") or 0, e["id"])
        if new_score > cur_score:
            best[key] = e
    return list(best.values())


if __name__ == "__main__":
    print("=== 抓取全部有目標價觀測值的事件(TARGET_PRICE + EPS內嵌) ===")
    raw = fetch_all("v_revisions_normalized", TP_SELECT, "&new_target=not.is.null")
    print(f"抓取筆數: {len(raw)}")

    deduped = dedup_same_day_prefer_tp(raw)
    print(f"去重(同ticker+同date，優先TARGET_PRICE): {len(raw)} -> {len(deduped)}")

    by_ticker = {}
    for e in deduped:
        by_ticker.setdefault(e["ticker"], []).append(e)

    unified = []
    isolated_count = 0  # 沒有鄰近TARGET_PRICE可對照的EPS內嵌目標價，計數用
    for ticker, evs in by_ticker.items():
        evs.sort(key=lambda e: (e["date"], e["id"]))
        prev = None
        for e in evs:
            if prev is not None and prev["new_target"]:
                pct = (e["new_target"] / prev["new_target"] - 1) * 100
                direction = "UP" if pct > 0 else ("DOWN" if pct < 0 else "FLAT")
                unified.append({
                    # direction/target_change_pct命名對齊event_study_full.py既有欄位，方便直接沿用同一套分析程式碼
                    "ticker": ticker, "company": e["company"], "date": e["date"], "id": e["id"],
                    "source_event_type": e["event_type"], "industry_canonical": e["industry_canonical"],
                    "analyst_count": e["analyst_count"],
                    "prev_target": prev["new_target"], "new_target": e["new_target"],
                    "direction": direction, "target_change_pct": round(pct, 4),
                })
            prev = e

    with open("factset_data/events_unified_target_full.json", "w", encoding="utf-8") as f:
        json.dump(unified, f, ensure_ascii=False, indent=2)

    df = pd.DataFrame(unified)
    print(f"\n統一序列(有前一觀測點可比較，排除各股第一筆): {len(df)}筆")
    print(f"方向分布: {df['direction'].value_counts().to_dict()}")
    print(f"來源事件類型分布: {df['source_event_type'].value_counts().to_dict()}")
    print(f"跟純TARGET_PRICE事件比較: 舊版本(去重後)約2,993筆 -> 新版本{len(df)}筆")
    flat_pct = (df['direction']=='FLAT').mean()*100
    print(f"FLAT(無變化)比例: {flat_pct:.1f}%")
