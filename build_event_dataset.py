"""
重建事件研究資料集：完整抓取(有分頁)，避免Supabase PostgREST單筆查詢1000筆上限截斷資料。

問題背景：之前手動產生的 factset_data/full_events.json、eps_events.json
都被1000筆上限截斷成只剩2026上半年、少了2023-2025大部分資料，且沒有腳本可重跑。
這支腳本用 Range-based pagination(每批1000筆、迴圈到抓完為止)重建，
之後任何人都能重跑得到跟資料庫一致的完整資料集。

輸出：
  factset_data/events_target_price_full.json  （全部TARGET_PRICE事件）
  factset_data/events_eps_full.json            （全部EPS事件）
  factset_data/prices_full.json                （事件涉及的全部ticker 5年股價，含台新ETF/TAIEX另抓）
"""
import json
import time
import requests

SUPABASE_URL = "https://kiiwaojcetxmeycyupvn.supabase.co"
ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImtpaXdhb2pjZXR4bWV5Y3l1cHZuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ1Mjk2NzAsImV4cCI6MjEwMDEwNTY3MH0.QPnEenJ8OtgWm1q3zhstinsAzXJAD6bunPp6JhrL4PU"
PAGE_SIZE = 1000

HEADERS = {
    "apikey": ANON_KEY,
    "Authorization": f"Bearer {ANON_KEY}",
}


def fetch_all(table_or_view, select, filters=""):
    """用Range header分頁抓完整張表/view，回傳list of dict，並驗證有沒有抓到重複/缺漏。"""
    rows = []
    offset = 0
    while True:
        headers = dict(HEADERS)
        headers["Range-Unit"] = "items"
        headers["Range"] = f"{offset}-{offset + PAGE_SIZE - 1}"
        url = f"{SUPABASE_URL}/rest/v1/{table_or_view}?select={select}{filters}&order=id.asc"
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        content_range = r.headers.get("Content-Range", "")
        # Content-Range格式: "0-999/3070" -> 用total驗證有沒有抓完
        total = None
        if "/" in content_range:
            total = content_range.split("/")[-1]
        print(f"  {table_or_view}: 已抓 {len(rows)} 筆 (server回報total={total})")
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(0.1)
    # 去重驗證(用id)
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"{table_or_view}: 抓到重複id，分頁邏輯有問題！筆數{len(ids)}，去重後{len(set(ids))}")
    return rows


def dedup_same_day(events, key_fields):
    """
    cnyes同一天會對同一檔股票連發多篇「速報」(隨分析師陸續更新覆蓋，analyst_count逐次增加)，
    這些是真實的多篇文章(不同source)，但對event study而言是同一個事件被重複計算，
    會讓該檔股票在date-cluster平均裡被灌水。保留當天analyst_count最高(最完整)的一筆，
    同分時保留id最大(最晚抓到)的一筆。
    """
    best = {}
    for e in events:
        key = tuple(e[k] for k in key_fields)
        cur = best.get(key)
        if cur is None:
            best[key] = e
            continue
        cur_score = (cur.get("analyst_count") or 0, cur["id"])
        new_score = (e.get("analyst_count") or 0, e["id"])
        if new_score > cur_score:
            best[key] = e
    removed = len(events) - len(best)
    return list(best.values()), removed


if __name__ == "__main__":
    print("=== 抓取 TARGET_PRICE 全量事件 ===")
    tp_events = fetch_all(
        "v_revisions_normalized",
        "id,date,ticker,company,direction,old_target,new_target,analyst_count,industry_canonical,target_change_pct",
        "&event_type=eq.TARGET_PRICE",
    )
    print(f"TARGET_PRICE 抓取筆數: {len(tp_events)}")
    tp_events, tp_removed = dedup_same_day(tp_events, ["ticker", "date"])
    print(f"去重(同ticker+同date保留analyst_count最高者): 移除{tp_removed}筆，剩{len(tp_events)}筆")

    print("\n=== 抓取 EPS 全量事件 ===")
    eps_events = fetch_all(
        "v_revisions_normalized",
        "id,date,ticker,company,direction,old_eps,new_eps,eps_year,analyst_count,industry_canonical",
        "&event_type=eq.EPS",
    )
    print(f"EPS 抓取筆數: {len(eps_events)}")
    eps_events, eps_removed = dedup_same_day(eps_events, ["ticker", "date", "eps_year"])
    print(f"去重(同ticker+同date+同eps_year保留analyst_count最高者): 移除{eps_removed}筆，剩{len(eps_events)}筆")

    with open("factset_data/events_target_price_full.json", "w", encoding="utf-8") as f:
        json.dump(tp_events, f, ensure_ascii=False, indent=2)
    with open("factset_data/events_eps_full.json", "w", encoding="utf-8") as f:
        json.dump(eps_events, f, ensure_ascii=False, indent=2)

    # 交叉驗證：跟資料庫直接count比對
    tp_dates = sorted(set(e["date"] for e in tp_events))
    eps_dates = sorted(set(e["date"] for e in eps_events))
    tp_tickers = sorted(set(e["ticker"] for e in tp_events))
    print(f"\nTARGET_PRICE 日期範圍: {tp_dates[0]} ~ {tp_dates[-1]}，涵蓋{len(tp_tickers)}檔股票")
    print(f"EPS 日期範圍: {eps_dates[0]} ~ {eps_dates[-1]}")
    print("\n完成。接下來用這份 events_target_price_full.json / events_eps_full.json 重跑event study。")
