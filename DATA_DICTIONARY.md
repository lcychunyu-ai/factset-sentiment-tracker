# 資料字典

資料庫本身(Supabase Table Editor)每個表/欄位都已經寫了`COMMENT`，滑鼠移過去就看得到——這份文件補充Table Editor放不下的東西：欄位之間的關聯、已知限制、正確用法。兩邊互補，不是重複。

## 表結構

```
factset_revisions (主表，一篇文章一列)
  └─ factset_estimates (子表，僅EPS事件有，一個revision可能對多列，多年度/多指標)

ticker_industry_official (官方產業/市場別，獨立參照表)
industry_alias           (舊產業文字對照，補ticker_industry_official涵蓋不到的)
concept_map              (概念股分類，獨立參照表，many-to-many)

v_revisions_normalized   (view，把上面幾張表JOIN好，網站/研究都從這個view讀)
```

## factset_revisions（主表）

一列 = 一篇鉅亨網「Factset最新調查」快訊。`event_type`決定這篇是目標價修正還是EPS修正，兩種事件共用同一張表，各自只有對應的欄位有值。

| 欄位 | 說明 |
|---|---|
| `direction` | UP/DOWN，已交叉驗證跟`target_change_pct`正負號100%一致 |
| `old_target`/`new_target` | 目標價修正前後（僅`TARGET_PRICE`） |
| `target_change_pct` | `= (new_target/old_target-1)*100`，公式已驗證正確 |
| `old_eps`/`new_eps`/`eps_year` | EPS修正前後跟對應財年（僅`EPS`） |
| `analyst_count` | **該篇文章當下的快照人數，不是累計**——同一股票同一天可能有好幾篇文章、人數逐次往上更新（見下方已知限制①） |
| `industry_name` | 原始文字，**不要直接用**，cnyes三年間寫法改了好幾次，正式分析一律用`v_revisions_normalized.industry_canonical` |
| `price_5d_pct`/`industry_5d_pct`/`market_5d_pct` | 原文自帶的事件前5日報酬快照，**跟正式event study算法不同口徑**，不能拿來跟`event_study_full.py`算出來的CAR比較（見下方已知限制②） |
| `concept` | 抓取當下的原始文字，**不是**正式概念分類，正式分類看`concept_map`表 |
| `source_url` | 每篇文章唯一，`daily_update.py`用它做upsert去重 |

## factset_estimates（子表）

`revision_id` → `factset_revisions.id`。只有`event_type='EPS'`的revision才會有對應列，一個revision可能對多列（不同`fiscal_year`×`metric`組合，例如同時有2025/2026的EPS估值）。

## ticker_industry_official（權威產業/市場別）

來源：證交所+櫃買中心官方ISIN公開資料，**不是**從新聞文字解析出來的。`market_type`='上市'/'上櫃'，決定抓股價時用`.TW`還是`.TWO`後綴（用官方值，不要用try/except猜）。

## concept_map（概念股分類）

來源：台新投信官方ETF成分股揭露頁面。一檔股票可以同時有好幾筆記錄（例如聯發科同時是「半導體」+「IC設計」）。

- `dimension`：`theme`(主題曝險) 或 `value_chain`(供應鏈位置)——**這是兩個獨立維度，不是tier1/tier2的階層關係**（之前用階層設計過，被台達電案例證偽後改掉，見下方版本記錄）
- `source`：判斷依據的ETF代號。同一個`concept`可能有多個不同`source`——這是**保留的共識度資訊**（越多不同ETF都選中=市場共識越高），不是要去重的重複值

## 已知限制（做分析前務必知道）

1. **同日重複發稿**：cnyes會對同一檔股票同一天發好幾篇文章，隨分析師陸續更新覆蓋人數（`analyst_count`從3路更新到7這種情況）。DB故意保留全部（貼近真實新聞流），但做統計分析時要先去重，否則會讓那天被單一股票灌水。已在`build_event_dataset.py`的`dedup_same_day()`處理（保留當天`analyst_count`最高的一筆）。

2. **兩套「事件前5天報酬」不能混用**：`factset_revisions.price_5d_pct`是原文自帶的快照口徑；`event_study_full.py`算出來的`car_pre_5`是市場模型扣beta後的異常報酬。兩者計算基準完全不同，只是欄位名字看起來像，**不要拿來互相對照或加總**。

3. **PostgREST查詢有上限**：目前設定`pgrst.db_max_rows=5000`（2026-07-22前是1000，曾造成事件研究資料被截斷，見下方版本記錄）。單次查詢超過這個數字會被靜默截斷、不會報錯，抓大量資料一定要用分頁（參考`build_event_dataset.py`的`fetch_all()`）。

4. **`concept`欄位 vs `concept_map`表**：`factset_revisions.concept`是抓取當下的原始文字，跟`concept_map`表是完全不同的東西，命名容易搞混，正式分類一律用`concept_map`。

## 「反轉（跑票）」正式定義（2026-07-22定案）

網站個股清單的「反轉」徽章與排序選項，判斷邏輯見`index.html`的`computeReversalInfo()`。同時滿足兩條件才算一次反轉事件：

1. **前置趨勢站穩**：同一檔股票、同方向的目標價修正連續 `streak_len` ≥ 3 次，時間跨度 `span_days` ≥ 30 天
2. **反轉觸發且有材料性**：下一筆修正方向與前置趨勢相反，且 `|target_change_pct| ≥ 3%`（排除雜訊級小幅調整）

**為什麼不是「跟上一筆方向不同」就算**：分析師本來就會小幅來回調整，只比較相鄰兩筆事件抓到的多半是雜訊。這個定義要求「先有站穩的共識趨勢，才談得上打破」，對應的研究問題是「多數分析師還沒轉向時，最早出現的裂縫是否具有領先性」——這是領先訊號；相對地，「等共識已經翻轉才反應」是落後訊號（已由event study證實分析師整體偏落後）。

**資料限制**：資料庫沒有個別分析師/券商身份，`factset_revisions`每列是某天的「當下N位分析師共識」快照，所以`streak_len`量的是「連續幾次快照同方向」，不是「連續幾位不同分析師」——嚴格來說是時間軸上的訊號，不是身份層級的訊號。門檻數字(3次/30天/3%)為初始設定，可視後續驗證結果調整。

## 版本記錄（影響資料解讀的重大變更）

- **2026-07-22**：修正事件研究資料被PostgREST 1000筆上限截斷的問題（實際只涵蓋2026上半年，非完整2023-2026），重建後部分結論改變（調降延續力減弱約70%、勝率盈虧比從不利轉為接近持平、電子零組件業調升效應未能複現）。詳見`event_study_full.py`/`event_study_eps.py`的輸出。
- **2026-07-22**：`concept_map`的`tier`(1/2階層)欄位改為`dimension`(theme/value_chain獨立維度)，因為階層假設被台達電(2308)案例證偽。
- **2026-07-21**：`industry_name`原始文字解析改為`ticker_industry_official`官方對照表，`v_revisions_normalized.industry_canonical`為正式產業欄位。
