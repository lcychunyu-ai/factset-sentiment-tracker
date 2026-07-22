import requests, re, json, sys

def fetch_and_parse(mode, market_label):
    url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
    r = requests.get(url, timeout=60)
    r.encoding = "big5"
    text = r.text
    print(f"{market_label} 下載完成，長度:{len(text)}", flush=True)

    row_re = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
    td_re = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
    tag_re = re.compile(r"<[^>]+>")

    out = []
    for row_match in row_re.finditer(text):
        tds = td_re.findall(row_match.group(1))
        if len(tds) < 5:
            continue
        code_name = tag_re.sub("", tds[0]).strip()
        if "　" not in code_name:
            continue
        ticker, name = code_name.split("　", 1)
        ticker = ticker.strip()
        if not re.match(r"^\d{4,6}$", ticker):
            continue
        industry = tag_re.sub("", tds[4]).strip()
        if not industry:
            continue
        out.append({"ticker": ticker, "name": name.strip(), "industry": industry, "market_type": market_label})
    print(f"{market_label} 解析完成，有效筆數:{len(out)}", flush=True)
    return out

listed = fetch_and_parse(2, "上市")
otc = fetch_and_parse(4, "上櫃")
combined = listed + otc
with open("factset_data/twse_official_industry.json", "w", encoding="utf-8") as f:
    json.dump(combined, f, ensure_ascii=False)
print("saved total:", len(combined), flush=True)
