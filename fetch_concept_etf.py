"""
概念股分類：抓台新投信官網ETF成分股頁面，解析成concept_map資料
來源：https://www.tsit.com.tw/ETF/Home/ETFSeriesDetail/{代號}（台新投信官方揭露，最權威）

用法：手動維護一份 ETF_CONFIG（代號、對應concept名稱、tier層級），
之後要加新的細分類，直接在這裡加一筆設定即可。
"""
import re
import json
import requests

FIRECRAWL_API_KEY = None  # 用MCP firecrawl_scrape抓好的markdown直接貼進來，這裡先示範手動流程

ETF_CONFIG = [
    {"ticker": "00904", "concept": "半導體", "tier": 1},
    {"ticker": "00947", "concept": "IC設計", "tier": 2},
]


def parse_holdings_table(markdown_text):
    """從台新投信頁面的markdown抓「股票」表格區塊，解析成 [{ticker, name, weight}]"""
    m = re.search(r"股票\s*\n+\s*\|\s*代號\s*\|\s*名稱\s*\|\s*股數\s*\|\s*持股權重\s*\|\s*\n(.*?)\n股票合計", markdown_text, re.S)
    if not m:
        return []
    rows = []
    for line in m.group(1).strip().split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        code_raw, name, shares, weight = cells[0], cells[1], cells[2], cells[3]
        ticker = code_raw.replace("TT", "").strip()
        if not re.match(r"^\d{4,6}$", ticker):
            continue
        weight_val = float(weight.replace("%", "").replace(",", ""))
        rows.append({"ticker": ticker, "name": name, "weight": weight_val})
    return rows


if __name__ == "__main__":
    print("這支腳本的parse_holdings_table()函式，會在主流程裡被呼叫，搭配firecrawl抓到的markdown使用")
