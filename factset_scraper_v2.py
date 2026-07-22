"""
全台股 FactSet 修正追蹤 - 資料抓取與解析（v2，正式schema，餵資料庫用）

抓 news.cnyes.com「台股預估」分類下所有「鉅亨速報 - Factset 最新調查：XXX(NNNN-TW)...」
文章，解析成 factset_revisions 資料表要用的欄位，輸出 JSON 供之後寫入 Supabase。

用法：
    python3 factset_scraper_v2.py --start 2026-07-01 --end 2026-07-21 --out factset_data/july_revisions.json
"""

import argparse
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

API_URL = "https://api.cnyes.com/media/api/v1/newslist/category/tw_forecast"
HEADERS = {"User-Agent": "Mozilla/5.0"}
TZ_TW = timezone(timedelta(hours=8))

TITLE_RE = re.compile(r"Factset\s*最新調查[：:]\s*(?P<name>[^\(]+)\((?P<code>\d{4,6})-TW\)")
EPS_TITLE_RE = re.compile(r"EPS預估(?P<dir>上修|下修)至(?P<eps_new>-?[\d.]+)元，預估目標價為(?P<target>-?[\d.]+)元")
TP_TITLE_RE = re.compile(r"目標價調(?P<dir>升|降)至(?P<target>-?[\d.]+)元，幅度約(?P<pct>-?[\d.]+)%")
RATING_RE = re.compile(r"積極樂觀(?P<bull>\d+)位、保持中立(?P<neutral>\d+)位、保守悲觀(?P<bear>\d+)位")
PRICE_RE = re.compile(
    r"今\((?P<day>\d+)日\)收盤價為(?P<price>-?[\d.]+)元。"
    r"近5日股價(?P<stock_dir>上漲|下跌)(?P<stock_chg>-?[\d.]+)%"
)
COUNT_RE = re.compile(r"共(\d+)位分析師")


def fetch_page(start_ts, end_ts, page, limit=30):
    params = {"page": page, "limit": limit, "startAt": start_ts, "endAt": end_ts}
    r = requests.get(API_URL, params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()["items"]


def fetch_all_in_range(start_dt, end_dt, sleep_sec=0.15):
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
        if cells:
            data[cells[0]] = cells[1:]
    return years, data


def split_new_old(cell):
    cell = cell.replace(",", "")
    m = re.match(r"(-?[\d.]+)\((-?[\d.]+)\)", cell)
    if m:
        return float(m.group(1)), float(m.group(2))
    try:
        return float(cell), None
    except ValueError:
        return None, None


def parse_article(item):
    title = html.unescape(item["title"])
    m_target = TITLE_RE.search(title)
    if not m_target:
        return None  # 不是台股(-TW)的Factset個股快訊，跳過（例如ADR或美股）

    content_html = html.unescape(item["content"])
    soup = BeautifulSoup(content_html, "html.parser")
    full_text = soup.get_text()

    row = {
        "source_url": f"https://news.cnyes.com/news/id/{item['newsId']}",
        "event_type": None,
        "news_time": datetime.fromtimestamp(item["publishAt"], tz=TZ_TW).isoformat(),
        "market": "TW",
        "ticker": m_target.group("code"),
        "company": m_target.group("name"),
        "direction": None,
        "old_target": None,
        "new_target": None,
        "target_change_pct": None,
        "eps_year": None,
        "old_eps": None,
        "new_eps": None,
        "highest_eps": None,
        "lowest_eps": None,
        "analyst_count": None,
        "rating_bullish": None,
        "rating_neutral": None,
        "rating_bearish": None,
        "close_price": None,
        "price_5d_pct": None,
        "concept": None,
        "raw_title": title,
    }

    m_count = COUNT_RE.search(full_text)
    if m_count:
        row["analyst_count"] = int(m_count.group(1))

    m_eps = EPS_TITLE_RE.search(title)
    m_tp = TP_TITLE_RE.search(title)

    if m_eps:
        row["event_type"] = "EPS"
        row["direction"] = "UP" if m_eps.group("dir") == "上修" else "DOWN"
        row["new_target"] = float(m_eps.group("target"))
        years, eps_table = parse_table_after(soup, "市場預估EPS")
        if years and eps_table:
            row["eps_year"] = years[0].replace("(前值)", "").strip()
            new_h, _ = split_new_old(eps_table.get("最高值", [""])[0])
            new_l, _ = split_new_old(eps_table.get("最低值", [""])[0])
            new_m, old_m = split_new_old(eps_table.get("中位數", [""])[0])
            row["highest_eps"], row["lowest_eps"] = new_h, new_l
            row["new_eps"], row["old_eps"] = new_m, old_m
    elif m_tp:
        row["event_type"] = "TARGET_PRICE"
        row["direction"] = "UP" if m_tp.group("dir") == "升" else "DOWN"
        row["new_target"] = float(m_tp.group("target"))
        pct = float(m_tp.group("pct"))
        row["target_change_pct"] = pct if m_tp.group("dir") == "升" else -pct
        if row["target_change_pct"] is not None and row["new_target"] is not None:
            row["old_target"] = round(row["new_target"] / (1 + row["target_change_pct"] / 100), 4)
    else:
        return None  # 標題格式不符預期，跳過避免存進髒資料

    m_rating = RATING_RE.search(full_text)
    if m_rating:
        row["rating_bullish"] = int(m_rating.group("bull"))
        row["rating_neutral"] = int(m_rating.group("neutral"))
        row["rating_bearish"] = int(m_rating.group("bear"))

    m_price = PRICE_RE.search(full_text)
    if m_price:
        row["close_price"] = float(m_price.group("price"))
        chg = float(m_price.group("stock_chg"))
        row["price_5d_pct"] = chg if m_price.group("stock_dir") == "上漲" else -chg

    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=TZ_TW)
    end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=TZ_TW) + timedelta(days=1)

    print(f"抓取區間：{start_dt:%Y-%m-%d} ~ {end_dt:%Y-%m-%d}")
    articles = fetch_all_in_range(start_dt, end_dt)
    print(f"tw_forecast 分類總筆數：{len(articles)}")

    rows = []
    skipped = 0
    for item in articles:
        row = parse_article(item)
        if row is None:
            skipped += 1
            continue
        rows.append(row)

    print(f"成功解析：{len(rows)} 筆，跳過（非台股個股快訊或格式不符）：{skipped} 筆")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"已輸出：{args.out}")


if __name__ == "__main__":
    main()
