"""
鉅亨網 Factset 分析師預估追蹤工具（Pilot：台積電 2330）

資料來源：news.cnyes.com「台股預估」分類 (tw_forecast)，每則快訊為單一個股的
FactSet 分析師 EPS / 目標價調整。此腳本透過 cnyes 公開新聞列表 API
(api.cnyes.com/media/api/v1/newslist/category/tw_forecast) 按日期區間分頁回補，
過濾出指定股票代號的快訊並解析成結構化資料，供後續建立「分析師預估修正指數」使用。

用法：
    python3 factset_tracker.py --code 2330 --name 台積電 --months 12
"""

import argparse
import html
import re
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from bs4 import BeautifulSoup

API_URL = "https://api.cnyes.com/media/api/v1/newslist/category/tw_forecast"
HEADERS = {"User-Agent": "Mozilla/5.0"}
TZ_TW = timezone(timedelta(hours=8))

EPS_TITLE_RE = re.compile(
    r"Factset\s*最新調查[：:]\s*(?P<name>[^\(]+)\((?P<code>\d{4,6})-TW\)"
    r"EPS預估(?P<dir>上修|下修)至(?P<eps_new>-?[\d.]+)元，預估目標價為(?P<target>-?[\d.]+)元"
)
TP_TITLE_RE = re.compile(
    r"Factset\s*最新調查[：:]\s*(?P<name>[^\(]+)\((?P<code>\d{4,6})-TW\)"
    r"目標價調(?P<dir>升|降)至(?P<target>-?[\d.]+)元，幅度約(?P<pct>-?[\d.]+)%"
)
RATING_RE = re.compile(
    r"積極樂觀(?P<bull>\d+)位、保持中立(?P<neutral>\d+)位、保守悲觀(?P<bear>\d+)位"
)
PRICE_RE = re.compile(
    r"今\((?P<day>\d+)日\)收盤價為(?P<price>-?[\d.]+)元。"
    r"近5日股價(?P<stock_dir>上漲|下跌)(?P<stock_chg>-?[\d.]+)%，"
    r"相關(?P<sector_name>[^近]+)近5日(?P<sector_dir>上漲|下跌)(?P<sector_chg>-?[\d.]+)%，"
    r"集中市場加權指數(?P<index_dir>上漲|下跌)(?P<index_chg>-?[\d.]+)%"
)
EPS_MEDIAN_RE = re.compile(r"中位數由(?P<old>-?[\d.]+)元(?P<dir>上修|下修)至(?P<new>-?[\d.]+)元")


def signed(direction_word, value):
    """把「上漲/上修/升」轉正號，「下跌/下修/降」轉負號"""
    if direction_word in ("下跌", "下修", "降"):
        return -abs(value)
    return abs(value)


def fetch_page(start_ts, end_ts, page, limit=30):
    params = {"page": page, "limit": limit, "startAt": start_ts, "endAt": end_ts}
    r = requests.get(API_URL, params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()["items"]


def fetch_all_in_range(start_dt, end_dt, sleep_sec=0.15):
    """按日期區間分頁抓取 tw_forecast 分類全部快訊（cnyes API 單次查詢似乎有回傳筆數上限，
    因此以 30 天為窗格切塊查詢，避免大區間漏資料）"""
    articles = []
    window = timedelta(days=30)
    cur_start = start_dt
    while cur_start < end_dt:
        cur_end = min(cur_start + window, end_dt)
        s_ts, e_ts = int(cur_start.timestamp()), int(cur_end.timestamp())
        page = 1
        while True:
            items = fetch_page(s_ts, e_ts, page=page)
            articles.extend(items["data"])
            if page >= items["last_page"]:
                break
            page += 1
            time.sleep(sleep_sec)
        cur_start = cur_end
        time.sleep(sleep_sec)
    return articles


def parse_table_after(soup, marker_text):
    """找到內文中含 marker_text（如「市場預估EPS」）的 <p> 之後第一個 <table>，
    回傳 {列名: [各年份數值字串]} 與欄位年份標籤"""
    marker = soup.find(string=re.compile(marker_text))
    if not marker:
        return None, None
    table = marker.find_next("table")
    if not table:
        return None, None
    rows = table.find_all("tr")
    years = [td.get_text(strip=True) for td in rows[0].find_all("td")][1:]
    data = {}
    for tr in rows[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cells:
            continue
        data[cells[0]] = cells[1:]
    return years, data


def split_new_old(cell):
    """把 "107.74(100.9)" 拆成 (新值, 舊值)；沒有括號則舊值為 None。數值可能含千分位逗號"""
    cell = cell.replace(",", "")
    m = re.match(r"(-?[\d.]+)\((-?[\d.]+)\)", cell)
    if m:
        return float(m.group(1)), float(m.group(2))
    try:
        return float(cell), None
    except ValueError:
        return None, None


def parse_article(item, target_code):
    title = html.unescape(item["title"])
    content_html = html.unescape(item["content"])
    soup = BeautifulSoup(content_html, "html.parser")
    full_text = soup.get_text()

    row = {
        "news_id": item["newsId"],
        "published_at": datetime.fromtimestamp(item["publishAt"], tz=TZ_TW).strftime("%Y-%m-%d %H:%M"),
        "title": title,
        "url": f"https://news.cnyes.com/news/id/{item['newsId']}",
        "type": None,
        "direction": None,
        "analyst_count": None,
        "eps_year": None,
        "eps_median_new": None,
        "eps_median_old": None,
        "eps_high": None,
        "eps_low": None,
        "eps_avg": None,
        "target_price": None,
        "target_price_chg_pct": None,
        "revenue_median_new": None,
        "revenue_median_old": None,
        "rating_bull": None,
        "rating_neutral": None,
        "rating_bear": None,
        "close_price": None,
        "stock_chg_5d_pct": None,
        "sector_chg_5d_pct": None,
        "index_chg_5d_pct": None,
    }

    m_eps_title = EPS_TITLE_RE.search(title)
    m_tp_title = TP_TITLE_RE.search(title)

    m_count = re.search(r"共(\d+)位分析師", full_text)
    if m_count:
        row["analyst_count"] = int(m_count.group(1))

    if m_eps_title:
        row["type"] = "EPS"
        row["direction"] = m_eps_title.group("dir")
        row["target_price"] = float(m_eps_title.group("target"))

        years, eps_table = parse_table_after(soup, "市場預估EPS")
        if years and eps_table:
            row["eps_year"] = years[0].replace("(前值)", "").strip()
            new_h, old_h = split_new_old(eps_table.get("最高值", [""])[0])
            new_l, old_l = split_new_old(eps_table.get("最低值", [""])[0])
            new_a, old_a = split_new_old(eps_table.get("平均值", [""])[0])
            new_m, old_m = split_new_old(eps_table.get("中位數", [""])[0])
            row["eps_high"], row["eps_low"], row["eps_avg"] = new_h, new_l, new_a
            row["eps_median_new"], row["eps_median_old"] = new_m, old_m

        years_r, rev_table = parse_table_after(soup, "市場預估營收")
        if years_r and rev_table:
            new_m, old_m = split_new_old(rev_table.get("中位數", [""])[0])
            row["revenue_median_new"], row["revenue_median_old"] = new_m, old_m

    elif m_tp_title:
        row["type"] = "TARGET_PRICE"
        row["direction"] = "上修" if m_tp_title.group("dir") == "升" else "下修"
        row["target_price"] = float(m_tp_title.group("target"))
        pct = float(m_tp_title.group("pct"))
        row["target_price_chg_pct"] = signed(m_tp_title.group("dir") == "升" and "上漲" or "下跌", pct)
    else:
        row["type"] = "UNKNOWN"

    m_rating = RATING_RE.search(full_text)
    if m_rating:
        row["rating_bull"] = int(m_rating.group("bull"))
        row["rating_neutral"] = int(m_rating.group("neutral"))
        row["rating_bear"] = int(m_rating.group("bear"))

    m_price = PRICE_RE.search(full_text)
    if m_price:
        row["close_price"] = float(m_price.group("price"))
        row["stock_chg_5d_pct"] = signed(m_price.group("stock_dir"), float(m_price.group("stock_chg")))
        row["sector_chg_5d_pct"] = signed(m_price.group("sector_dir"), float(m_price.group("sector_chg")))
        row["index_chg_5d_pct"] = signed(m_price.group("index_dir"), float(m_price.group("index_chg")))

    return row


def is_target_stock(title, code):
    return f"({code}-TW)" in title


def main():
    ap = argparse.ArgumentParser(description="回補指定股票的 cnyes Factset 預估快訊")
    ap.add_argument("--code", required=True, help="股票代號，例如 2330")
    ap.add_argument("--name", required=True, help="股票名稱，例如 台積電")
    ap.add_argument("--months", type=int, default=12, help="回補月數，預設12個月")
    ap.add_argument("--out", default=None, help="輸出檔名，預設 factset_data/{code}_factset.xlsx")
    args = ap.parse_args()

    end_dt = datetime.now(tz=TZ_TW)
    start_dt = end_dt - timedelta(days=30 * args.months)

    print(f"回補區間：{start_dt:%Y-%m-%d} ~ {end_dt:%Y-%m-%d}，股票：{args.name}({args.code})")
    all_articles = fetch_all_in_range(start_dt, end_dt)
    print(f"tw_forecast 分類總筆數：{len(all_articles)}")

    matched = [a for a in all_articles if is_target_stock(html.unescape(a["title"]), args.code)]
    print(f"符合 {args.name}({args.code}) 的快訊：{len(matched)} 則")

    rows = [parse_article(item, args.code) for item in matched]
    df = pd.DataFrame(rows).sort_values("published_at").reset_index(drop=True)

    out_dir = "/Users/USER/Desktop/Matthias Agent/factset_data"
    import os
    os.makedirs(out_dir, exist_ok=True)
    out_path = args.out or f"{out_dir}/{args.code}_factset.xlsx"
    df.to_excel(out_path, index=False, sheet_name="raw_events")
    print(f"已輸出：{out_path}（{len(df)} 筆）")


if __name__ == "__main__":
    main()
