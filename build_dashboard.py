"""把 factset_dashboard_data.py 產出的 JSON 灌進網頁範本，產生可發布的 HTML。

用法：
    python3 build_dashboard.py --code 2330 --name 台積電
"""
import argparse

BASE = "/Users/USER/Desktop/Matthias Agent"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--window-days", type=int, default=30)
    args = ap.parse_args()

    with open(f"{BASE}/factset_dashboard_template.html", encoding="utf-8") as f:
        template = f.read()
    with open(f"{BASE}/factset_data/{args.code}_dashboard.json", encoding="utf-8") as f:
        data_json = f.read()

    html = (
        template.replace("__TICKER__", args.code)
        .replace("__NAME__", args.name)
        .replace("__WINDOW_DAYS__", str(args.window_days))
        .replace("__DATA_JSON__", data_json)
    )

    out_path = f"{BASE}/factset_data/{args.code}_dashboard.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已輸出：{out_path}")


if __name__ == "__main__":
    main()
