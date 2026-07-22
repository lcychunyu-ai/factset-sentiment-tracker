"""
把 factset_tracker.py 產出的原始事件 Excel，計算成 FSP.1M / FSN.1M 溫度指標
（公式定義見 /Users/USER/Downloads/FactSet-目標價修正.pdf），輸出給網頁儀表板用的 JSON。

用法：
    python3 factset_dashboard_data.py --code 2330 --name 台積電
"""

import argparse
import json
import math
from datetime import datetime, timedelta

import pandas as pd


def clean_nan(obj):
    """遞迴把 float NaN 換成 None，讓輸出是合法 JSON（pandas 的 where/fillna 在數值欄位上會被轉型打回 NaN）"""
    if isinstance(obj, dict):
        return {k: clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_nan(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


def compute_temperature(rows):
    """溫度 = sum(修正幅度% x 分析師數) / sum(分析師數)"""
    if rows.empty:
        return {"count": 0, "weight": 0, "temperature_pct": None, "samples": []}
    weight = rows["analyst_count"].sum()
    temperature = (rows["target_price_chg_pct"] * rows["analyst_count"]).sum() / weight
    samples = rows[["published_at", "title", "target_price", "target_price_chg_pct", "analyst_count", "url"]].to_dict("records")
    return {"count": len(rows), "weight": int(weight), "temperature_pct": round(temperature, 2), "samples": samples}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--window-days", type=int, default=30)
    args = ap.parse_args()

    in_path = f"/Users/USER/Desktop/Matthias Agent/factset_data/{args.code}_factset.xlsx"
    df = pd.read_excel(in_path, parse_dates=["published_at"])
    df = df.sort_values("published_at").reset_index(drop=True)

    now = datetime.now()
    window_start = now - timedelta(days=args.window_days)

    tp_events = df[df["type"] == "TARGET_PRICE"].copy()
    tp_recent = tp_events[tp_events["published_at"] >= window_start].copy()
    tp_recent["published_at"] = tp_recent["published_at"].dt.strftime("%Y-%m-%d %H:%M")

    fsp = compute_temperature(tp_recent[tp_recent["direction"] == "上修"])
    fsn = compute_temperature(tp_recent[tp_recent["direction"] == "下修"])

    # 目標價軌跡（EPS型與目標價型都有 target_price，依時間排序取聯集）
    trajectory = df[["published_at", "target_price", "type", "direction"]].dropna(subset=["target_price"])
    trajectory = trajectory.assign(published_at=trajectory["published_at"].dt.strftime("%Y-%m-%d")).to_dict("records")

    # 分析師評級軌跡（只有目標價型有評級明細）
    rating = df.dropna(subset=["rating_bull"])[["published_at", "rating_bull", "rating_neutral", "rating_bear"]]
    rating = rating.assign(published_at=rating["published_at"].dt.strftime("%Y-%m-%d")).to_dict("records")

    # 個股 vs 產業 vs 大盤 近5日表現（只有目標價型有這組數據）
    perf = df.dropna(subset=["stock_chg_5d_pct"])[
        ["published_at", "close_price", "stock_chg_5d_pct", "sector_chg_5d_pct", "index_chg_5d_pct"]
    ]
    perf = perf.assign(published_at=perf["published_at"].dt.strftime("%Y-%m-%d")).to_dict("records")

    events = df.assign(published_at=df["published_at"].dt.strftime("%Y-%m-%d %H:%M"))
    events = events.to_dict("records")

    out = {
        "ticker": args.code,
        "name": args.name,
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "window_days": args.window_days,
        "latest_close": None if perf == [] else perf[-1]["close_price"],
        "fsp_1m": fsp,
        "fsn_1m": fsn,
        "events": events,
        "target_price_trajectory": trajectory,
        "rating_trajectory": rating,
        "relative_performance": perf,
    }
    out = clean_nan(out)

    out_path = f"/Users/USER/Desktop/Matthias Agent/factset_data/{args.code}_dashboard.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"已輸出：{out_path}")
    print(f"FSP.1M 溫度：{fsp['temperature_pct']}%（{fsp['count']}筆） / FSN.1M 溫度：{fsn['temperature_pct']}%（{fsn['count']}筆）")


if __name__ == "__main__":
    main()
