"""
統一目標價序列：直接從資料庫的 v_unified_target_events view 抓取。

這個view把TARGET_PRICE事件跟EPS快訊內嵌目標價合併、去重、依時間串成
每檔股票連續序列，並用「上一個已知觀測點」算出方向/幅度——邏輯定義在
資料庫本身(見migration: create_unified_target_view)，不是這支腳本算的，
這支腳本只負責分頁抓取存檔。任何人直接查v_unified_target_events這個view
都會拿到跟這裡一樣正確、完整的資料，不需要知道或重算這套chaining邏輯。

輸出：factset_data/events_unified_target_full.json
"""
import json
from build_event_dataset import fetch_all

SELECT = "id,date,ticker,company,source_event_type,industry_canonical,analyst_count,prev_target,new_target,direction,target_change_pct"

if __name__ == "__main__":
    print("=== 抓取 v_unified_target_events(統一目標價序列，資料庫端已算好) ===")
    unified = fetch_all("v_unified_target_events", SELECT)
    print(f"抓取筆數: {len(unified)}")

    with open("factset_data/events_unified_target_full.json", "w", encoding="utf-8") as f:
        json.dump(unified, f, ensure_ascii=False, indent=2)

    directions = {}
    for e in unified:
        directions[e["direction"]] = directions.get(e["direction"], 0) + 1
    print(f"方向分布: {directions}")
    tickers = len(set(e["ticker"] for e in unified))
    dates = sorted(set(e["date"] for e in unified))
    print(f"股票數: {tickers}，日期範圍: {dates[0]} ~ {dates[-1]}")
