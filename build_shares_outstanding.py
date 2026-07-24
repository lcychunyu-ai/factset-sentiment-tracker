"""
抓取股數(供市值加權產業基準使用，見event_study_full.py/event_study_volatility.py)。

股數視為近似固定(只抓目前值，不追蹤歷史股數變化)，因為股數變動(增資減資)
遠比股價變動少見，用目前股數×每日股價估算歷史市值是合理近似，不需要
追蹤逐日股數異動這種更貴的資料。

輸出：factset_data/shares_outstanding.json  {ticker: shares_outstanding}
"""
import json
import time
import yfinance as yf

if __name__ == "__main__":
    prices = json.load(open("factset_data/prices_full.json"))
    tickers = list(prices.keys())
    print(f"需要股數的股票數: {len(tickers)}")

    shares = {}
    failed = []
    for t in tickers:
        suf = prices[t]["suffix"]
        try:
            fi = yf.Ticker(f"{t}{suf}").fast_info
            s = fi.get("shares") if hasattr(fi, "get") else getattr(fi, "shares", None)
            if s and s > 0:
                shares[t] = s
            else:
                failed.append(t)
        except Exception:
            failed.append(t)
        time.sleep(0.03)

    print(f"成功取得股數: {len(shares)}/{len(tickers)}")
    if failed:
        print(f"失敗(市值加權會退化成equal-weight對待這些股票): {failed}")
    json.dump(shares, open("factset_data/shares_outstanding.json", "w"))
