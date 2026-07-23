# 研究交接文件：分析師目標價/EPS修正 事件研究

給外部審查者(人或AI)使用，目的是讓你不需要任何額外對話上下文，就能獨立驗證本研究的資料、方法、程式碼有沒有邏輯錯誤或誤用。

---

## 1. 專案背景與目的

把台灣股市分析師的目標價/EPS修正公開新聞（來源：鉅亨網「Factset最新調查」快訊）轉成情緒/溫度指標，並用嚴謹的事件研究方法驗證：這個指標對股價到底有沒有預測力？如果有，能不能做成賺錢的交易策略？如果沒有，還有沒有其他價值（例如風險管理）？

**資料庫**：Supabase(Postgres) project，project ref `kiiwaojcetxmeycyupvn`。
**網站**：https://lcychunyu-ai.github.io/factset-sentiment-tracker/ （即時連線同一個資料庫）
**程式碼倉庫**：本機資料夾內所有`.py`檔案，已進git版控。

---

## 2. 資料來源與蒐集方式

- 原始資料：鉅亨網公開新聞（`https://news.cnyes.com`），`tw_forecast`分類下的「Factset最新調查」快訊
- 爬蟲：`factset_scraper_v3.py`，用regex解析文章標題/內文，判斷是`TARGET_PRICE`(目標價修正)還是`EPS`(每股盈餘估值修正)類型
- 排程：`daily_update.py`，GitHub Actions每天台灣時間07:00自動執行，抓最近5天新文章，用`source_url`做upsert去重
- **資料涵蓋範圍**：2023-01-04 ~ 現在，這個新聞產品最早只到2023年，非FactSet完整資料庫，是公開可得的子集
- **不是**：完整的FactSet資料庫、不是所有台股、不含個別分析師身份

---

## 3. 資料庫結構（重點表/view）

```
factset_revisions          主表，一篇文章一列。event_type='TARGET_PRICE'或'EPS'決定哪組欄位有值
  └ factset_estimates      子表，僅EPS事件有，一個revision可能對多列(多年度/多指標)

ticker_industry_official   官方產業/市場別對照表(來源:證交所/櫃買中心ISIN公開資料)
concept_map                概念股分類(來源:台新投信ETF成分股，pilot範圍僅半導體/IC設計)

v_revisions_normalized     把上面幾張表JOIN好的view，日常查詢都從這裡讀
v_unified_target_events    【本研究最重要的view】見下方第4節
v_eps_only_target_events   同上但只用EPS內嵌目標價(比較用)
v_data_dictionary          整個資料庫的自我說明(每個表/欄位的中文解釋)
research_stats             網站顯示的統計數字，來源可追溯
```

用 `select * from v_data_dictionary` 可以看到每一張表、每個欄位的完整中文說明，這是驗證欄位意義最權威的來源，不要只看這份文件的轉述。

### 3.1 資料完整性的關鍵限制

1. **沒有個別分析師/券商身份**：`factset_revisions`每一列是「某天某篇文章回報的當下N位分析師共識快照」（`analyst_count`給的是人數，`new_target`是聚合後的單一數字，可能是均值或中位數，原始新聞沒有明講計算方式）。**沒辦法追蹤「某一位特定分析師」自己的立場怎麼隨時間變化**，只能看到聚合結果、以及同一時間點的分歧程度(`target_high`/`target_low`區間、`rating_bullish/neutral/bearish`評等結構)。
2. **`analyst_count`與`rating_bullish+neutral+bearish`的加總經常對不起來**（例如曾觀察到analyst_count=33，但三個評等人數加總=48），代表這兩組數字可能來自FactSet內部不同的統計母體，不能假設可以互相替代或用其中一個推算另一個。
3. **`factset_revisions.old_target`欄位，EPS事件100%是NULL**（不是缺漏，是EPS報告本身不一定會附「修正前」的對照數字）。**如果直接用這個原始欄位做方向判斷，會漏掉EPS報告裡內含的目標價資訊**，見下一節。

---

## 4. 核心方法論創新：統一目標價序列

### 4.1 發現的問題

EPS快訊裡也常附帶「預估目標價」（`new_target`欄位在EPS事件裡100%有值），但沒有對應的`old_target`可以直接算方向。檢查發現：**65%的EPS內嵌目標價，前後7天內完全沒有對應的TARGET_PRICE報告可以對照**——不是重複資料，是被完全忽略的獨立資訊。例如台積電(2330) 2023-04~2024-04整整一年沒有任何TARGET_PRICE類型報告，但EPS快訊顯示目標價其實從611.5元穩定爬升到715元。

只看`event_type='TARGET_PRICE'`的目標價序列也證實跳動過快：相鄰有效觀測點變動幅度中位數3.57%；納入EPS內嵌目標價後降到1.40%，更貼近真實的資訊更新頻率。

### 4.2 解法：v_unified_target_events（SQL view，邏輯寫在資料庫端）

```sql
-- 完整定義見資料庫 migration，這裡是邏輯摘要
with dedup as (
  -- 同一檔股票同一天，如果TARGET_PRICE跟EPS都有回報，優先採用TARGET_PRICE
  -- (該篇文章本身就有明確old/new對照，較權威)；同type多篇取analyst_count最高者
  select distinct on (ticker, date) ...
  order by ticker, date, (event_type='TARGET_PRICE') desc, analyst_count desc, id desc
),
chained as (
  -- 按時間排序後，用LAG()窗口函數取「該股上一個已知目標價觀測點」(不論來源)
  select *, lag(new_target) over (partition by ticker order by date, id) as prev_target
  from dedup
)
select ..., 
  case when new_target > prev_target then 'UP'
       when new_target < prev_target then 'DOWN' else 'FLAT' end as direction,
  (new_target/prev_target - 1)*100 as target_change_pct
from chained where prev_target is not null;
```

**用意**：把TARGET_PRICE事件跟EPS內嵌目標價合併成每檔股票一條連續序列，用「上一個已知觀測點」（不論來源）重新計算方向/幅度——這才是拿去做溫度計算/event study該用的方向跟幅度，**跟原始revision自帶的`direction`/`target_change_pct`欄位(那是分析師報告自己回報的old/new對照)是兩回事**，兩者都保留但不要混用。

**驗證**：合併後樣本從2,989筆(純TARGET_PRICE)增加到5,650筆(統一序列)，SQL view版本跟等效的Python重算版本結果完全一致(UP=3422/DOWN=2230/FLAT=2206)，交叉驗證過沒有邏輯出入。

**三方比較**（`event_study_full.py`同時跑三個版本）：

| | 純TARGET_PRICE(n=2,989) | 純EPS內嵌目標價(n=4,100) | 統一序列(n=5,650) |
|---|---|---|---|
| 落後指標(事件前5天) | UP t=10.93 / DOWN t=-5.47 | UP t=7.86 / DOWN t=-4.29 | UP t=10.50 / DOWN t=-4.88 |
| 調降延續力(事件後20天) | t=-2.75(顯著) | t=-0.29(無效果) | t=-1.76(不顯著) |

結論：EPS內嵌目標價本身是真訊號(不是雜訊)，合併後核心的落後指標結論依然穩健，但調降延續力這個效應主要由TARGET_PRICE事件貢獻，merge後被稀釋。

---

## 5. 事件研究方法論（`event_study_full.py`）

標準市場模型事件研究法：

1. **估計窗** [-250, -30] 交易日：對每個事件跑迴歸 `股票日報酬 = α + β_mkt×大盤日報酬 + β_ind×產業基準日報酬`(OLS最小平方法)
   - 產業基準：leave-one-out——同產業其他股票日報酬的平均值(排除自己)，樣本不足時退化成只用大盤(單因子)
   - 大盤：台灣加權指數(^TWII)
   - 產業分類：`ticker_industry_official`(官方ISIN資料)，非文字解析
2. **事件窗** [-10, +20] 交易日：用估計窗算出的α、β(固定不重新估計)，預測事件窗每天的「正常報酬」，實際報酬減去預測值 = 異常報酬(AR)
3. **累積異常報酬(CAR)**：`car_pre_5` = AR在[-5,-1]加總；`car_post_N` = AR在[+1,N]加總(N=5,10,20)
4. **統計檢定**：**date-cluster標準誤**——同一天發生的多個事件，先算當天CAR平均值，再對「日期層級的平均值」做t檢定，避免同一天多檔股票齊漲跌造成的顯著性高估(這是本研究刻意的保守設計，不是隨便用naive t檢定)
5. **股價資料**：yfinance，`auto_adjust=True`(已還原股利/減資)，5年日線(2021至今)

---

## 6. 目前已驗證/已否證的發現（完整列表）

| 發現 | 統計量 | 狀態 |
|---|---|---|
| 分析師整體是落後指標 | UP pre5 t=10.50, DOWN pre5 t=-4.88 (統一序列版) | ✅ 穩健成立，多次資料修正後都存在 |
| 調降後有延續下跌 | post20 t=-2.75(純TP) → t=-1.76(統一序列，不顯著) | ⚠️ 弱、且對資料來源敏感，不算穩健edge |
| 調升後延續效果 | post20 t=-1.27~0.03 | ❌ 全部不顯著 |
| 修正幅度(連續變數)解釋力 | t=-0.96 | ❌ 無效果 |
| 反轉(跑票，見下方7.1定義) | 48組門檻組合，最強t=-1.81 | ❌ 已測試無預測力 |
| 評等結構轉變 | t=0.48 | ❌ 無效果，且發現評等/目標價幾乎同步變動，抓不到"評等先動"的子樣本 |
| **事件後波動度變化** | **UP: date-cluster t=5.57(波動+5%) / DOWN: t=-9.28(波動-8.7%)** | **✅ 目前唯一通過嚴格檢定的post-event發現，見第7節** |

---

## 7. 最新發現：事件後波動度不對稱效應

### 7.1 方法(`event_study_volatility.py`)

對每個事件：
1. 用第5節同樣的市場模型估計α、β
2. **平常波動基準(est_vol)** = 估計窗[-250,-30]本身的迴歸殘差標準差
3. **事件後波動(post_vol)** = 用同一組α、β預測事件窗[+1,+20]，殘差標準差
4. **vol_ratio = post_vol / est_vol**（>1代表事件後比平常吵）
5. 取log(vol_ratio)做date-cluster t檢定

### 7.2 結果

- 調升後：vol_ratio均值≈1.050(+5%)，date-cluster t=5.57
- 調降後：vol_ratio均值≈0.913(-8.7%)，date-cluster t=-9.28
- 樣本數：UP 3,341筆(928個日期群)、DOWN 2,219筆(813個日期群)

### 7.3 已排除的混淆因子

**疑慮**：會不會只是波動度均值回歸(mean reversion)的機械效果，跟調升調降本身無關？

**驗證**：①UP/DOWN事件發生前的平常波動水準(est_vol)很接近(2.05% vs 1.93%)，沒有系統性落差 ②est_vol水準本身跟log(vol_ratio)的相關係數只有-0.059，很弱 ③把樣本按est_vol分5組，**每一組裡UP的vol_ratio都持續高於DOWN**(差距0.15~0.28，五組同方向)——代表就算事件前波動水準完全一樣，方向效應依然存在，不是均值回歸的代理效果。

### 7.4 尚未做的額外穩健性檢查(交給審查者/後續研究)

- 沒有分產業檢查這個效應是否集中在特定產業
- 沒有檢查是否受少數極端值(outlier)驅動
- 沒有做regime(多空市場狀態)分段
- 沒有檢查波動度視窗長度(目前固定20天)的敏感度
- 沒有排除同一檔股票不同時間點事件視窗互相重疊的殘留相關性(只做了同日期的date-cluster，沒做同股票跨事件的重疊校正)

### 7.5 可能的實務應用（不需要方向性優勢）

- 部位管理：調升後預期波動放大，可縮小部位或放寬停損
- 波動度交易：調升後買波動度(如straddle)、調降後賣波動度，不需要猜對股價方向
- 風控旗標：把「剛被調升」標記為系統性的高波動警示

---

## 8. 已知的資料/方法限制彙總

1. 沒有個別分析師身份，所有"方向"判斷都是聚合共識層級，不是追蹤個別分析師的跑票行為
2. `analyst_count`跟評等人數加總對不起來，兩者可能來自不同統計母體
3. `concept_map`只有66/216檔股票有概念標籤(pilot範圍僅半導體/IC設計)，非全市場覆蓋
4. 資料庫查詢有5,000筆上限(`pgrst.db_max_rows`)，抓取需要分頁(見`build_event_dataset.py`的`fetch_all()`寫法)
5. 目標價序列有缺口(股票沒被追蹤的期間)，目前**沒有**做任何形式的補值(LOCF)——刻意決定，因為"多舊算過期"是策略層級的判斷，不是資料層級該解決的問題，見對話記錄裡的討論
6. `factset_revisions.concept`欄位100%是NULL，已棄用但因CASCADE風險沒有直接刪除

---

## 9. 完整程式碼清單

| 檔案 | 用途 |
|---|---|
| `factset_scraper_v3.py` | 爬蟲，解析cnyes文章 |
| `daily_update.py` | 每日排程更新腳本 |
| `build_event_dataset.py` | 分頁抓取TARGET_PRICE/EPS事件(含`fetch_all()`分頁工具函式) |
| `build_price_dataset.py` | 抓取5年股價(yfinance)，用官方market_type決定.TW/.TWO後綴 |
| `build_unified_target_series.py` | 抓取`v_unified_target_events`存檔 |
| `event_study_full.py` | 三方event study主程式(純TP/純EPS/統一序列並排比較) |
| `event_study_eps.py` | EPS事件對照版(方法同event_study_full.py) |
| `event_study_volatility.py` | 第7節的波動度分析 |
| `reversal_signal_calibration.py` | 反轉訊號48組門檻校準測試(已否證) |
| `DATA_DICTIONARY.md` | 資料庫欄位/表格中文說明+已知限制+版本記錄 |
| `index.html` | 網站原始碼，即時連線Supabase |

所有輸出的中間資料(events/prices的json、event study結果的csv)都在`factset_data/`資料夾，不進版控(可重新產生)。

---

## 10. 給審查者的建議提問方向

如果要挑戰這份研究，可以優先檢查：
1. 事件視窗[-10,+20]、估計窗[-250,-30]的選擇有沒有偏誤？換不同窗口長度結論會不會不一樣？
2. date-cluster標準誤夠嚴謹嗎？有沒有考慮過同一檔股票跨事件的序列相關(不只是同一天)？
3. 波動度發現(第7節)有沒有可能是特定幾檔權值股(如台積電)主導的，不是普遍現象？
4. 產業基準用leave-one-out平均，會不會被同產業內的極端事件汙染？
5. 48組反轉門檻測試，有沒有做多重比較校正(如Bonferroni)？目前只是描述性地說"沒有顯著"，沒有正式校正閾值
