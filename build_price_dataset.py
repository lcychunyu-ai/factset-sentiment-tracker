"""
重建event study用的股價資料集：涵蓋 events_target_price_full.json / events_eps_full.json
裡出現的「全部」ticker(之前的full_prices.json只有145檔，漏了60檔)。
用官方market_type(上市/上櫃)決定.TW/.TWO後綴，避免用try/except猜後綴猜錯。
"""
import json
import time
import requests
import yfinance as yf
import pandas as pd

SUPABASE_URL = "https://kiiwaojcetxmeycyupvn.supabase.co"
ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImtpaXdhb2pjZXR4bWV5Y3l1cHZuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ1Mjk2NzAsImV4cCI6MjEwMDEwNTY3MH0.QPnEenJ8OtgWm1q3zhstinsAzXJAD6bunPp6JhrL4PU"
HEADERS = {"apikey": ANON_KEY, "Authorization": f"Bearer {ANON_KEY}"}

tp = json.load(open("factset_data/events_target_price_full.json"))
eps = json.load(open("factset_data/events_eps_full.json"))
tickers = sorted(set(e["ticker"] for e in tp) | set(e["ticker"] for e in eps))
print(f"需要股價的股票數: {len(tickers)}")

# 抓官方market_type決定後綴
r = requests.get(
    f"{SUPABASE_URL}/rest/v1/ticker_industry_official?select=ticker,market_type&limit=5000",
    headers=HEADERS, timeout=30,
)
r.raise_for_status()
market_map = {row["ticker"]: row["market_type"] for row in r.json()}
print(f"官方市場別對照表筆數: {len(market_map)}")

missing_market = [t for t in tickers if t not in market_map]
if missing_market:
    print(f"警告: {len(missing_market)}檔股票查不到官方市場別，將用try/except猜後綴: {missing_market}")

results = {}
failed = []
for t in tickers:
    mt = market_map.get(t)
    if mt == "上市":
        suffixes = [".TW"]
    elif mt == "上櫃":
        suffixes = [".TWO"]
    else:
        suffixes = [".TW", ".TWO"]

    ok = False
    for suf in suffixes:
        try:
            df = yf.download(f"{t}{suf}", start="2021-01-01", end="2026-07-23", progress=False, auto_adjust=True)
            if df.empty:
                continue
            close = df["Close"]
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]
            close = close.dropna()
            if len(close) < 100:
                continue
            results[t] = {"suffix": suf, "prices": {d.strftime("%Y-%m-%d"): float(v) for d, v in close.items()}}
            ok = True
            break
        except Exception as ex:
            continue
    if not ok:
        failed.append(t)
    time.sleep(0.05)

print(f"\n成功抓到股價: {len(results)}/{len(tickers)}")
if failed:
    print(f"抓不到股價的股票({len(failed)}檔，event study會排除): {failed}")

with open("factset_data/prices_full.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False)

# TAIEX
taiex_df = yf.download("^TWII", start="2021-01-01", end="2026-07-23", progress=False, auto_adjust=True)
tclose = taiex_df["Close"]
if hasattr(tclose, "columns"):
    tclose = tclose.iloc[:, 0]
tclose = tclose.dropna()
with open("factset_data/taiex_full.json", "w", encoding="utf-8") as f:
    json.dump({d.strftime("%Y-%m-%d"): float(v) for d, v in tclose.items()}, f, ensure_ascii=False)
print(f"TAIEX 筆數: {len(tclose)}")
