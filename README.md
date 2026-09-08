# Railway Timetable Validator

給時刻表資料製作者與模擬器開發者使用的輕量 CSV 檢查工具。模型中，每個 **Station + Platform** 組合代表同時只允許一個班次占用的排他性資源；使用者須確認資料符合這個假設。

結果只適用於**本次已啟用規則與所提供資料**。這不是鐵路安全認證、列車自動調度或完整號誌模擬；沒有發現重疊，並不代表真實營運安全。

本輪完成來源位置、多錯誤彙總、重複資料診斷、空資料提示與 JSON。原始碼版本由 `validator.py` 的 `__version__ = "0.2.0"` 定義，可用 `--version` 查詢；這不是 PyPI 發布版本。

## 使用方式

核心與測試僅使用 Python 標準函式庫，不需安裝套件。最低相容目標維持 Python 3.9；實測與 CI 的範圍見文末，未實測組合不代表已驗證支援。

下載本輪分支並進入專案：

```sh
git clone https://github.com/andy32012/railway-timetable-validator.git
cd railway-timetable-validator
python validator.py examples/sample_timetable.csv
```

原本的 `python validator.py timetable.csv` 入口保留；將檔案路徑換成自己的 CSV 即可。檔名有空格時加雙引號。系統使用 `python3` 時請取代 `python`，Windows 也可使用 `py`。

以下指令均在專案根目錄執行：

```sh
python validator.py --help
python validator.py --version
python validator.py examples/sample_timetable.csv --format json
python validator.py examples/clean_timetable.csv
python validator.py examples/invalid_timetable.csv --format json
python validator.py examples/multiline_timetable.csv
python validator.py examples/empty_timetable.csv
python validator.py examples/empty_timetable.csv --require-data --format json
```

依上述順序，結束碼為 `0, 0, 1, 0, 2, 1, 0, 2`。衝突範例的 `1` 和錯誤範例的 `2` 是預期結果。

## CSV 格式與保留的語意

UTF-8 編碼，可含 BOM。欄名區分大小寫，必須恰好包含以下五欄，每欄一次；可調整順序，不接受缺少、重複或額外欄位。

```csv
Train,Station,Platform,Arrival,Departure
101,Taipei,1,08:00,08:05
102,Taipei,2,08:01,08:06
103,Taipei,1,08:03,08:08
104,Taipei,1,08:08,08:12
105,Banqiao,1,08:03,08:07
```

| 欄位 | 規則 |
| --- | --- |
| Train | 非空文字，保留前置零；不是實體車組識別 |
| Station | 非空文字，依名稱精確比對 |
| Platform | 非空文字，`01` 與 `1` 不同 |
| Arrival | 同一天 24 小時制 `HH:MM`，`00:00` 至 `23:59` |
| Departure | 同上，必須嚴格晚於 Arrival |

所有欄位依原規則以 `str.strip()` 去除前後空白後判斷。不改大小寫、不做 Unicode 正規化、不把台北／臺北／Taipei 合併，也不把數字文字轉成整數。JSON 的原始欄位另保存在 `details.records[].values`，包含去空白之前的值；它是 CSV 解碼後的欄位內容，不是原始引號／分隔符位元組。

只檢查 **同 Station、同 Platform、不同 Train** 的 `[Arrival, Departure)` 重疊。前車 08:05 離站、後車 08:05 到站不算衝突，未加入交接間隔。資料可未排序，會列出所有配對，包括完全包含與三班同時重疊；不自動調整時間。

同 Train 的不同停站記錄仍不互相檢查行程。完全相同的五欄記錄則是輸入重複錯誤。不可把 Train 當作實體車組來推導折返；未來行程規則需另設計 TripId／Sequence／VehicleId。

## CSV 驗證與行為變更

- 每筆有效停站保存來源檔案與 `line_start / line_end`，從 1 起算，包含標題列與空白行。單行記錄兩者相同；多行引號記錄保存實際起訖行，不把 `csv.reader.line_num` 誤當起始行。
- 實體空行及僅含空格／Tab 的實體行可忽略，包括標題之前；引號內的空白行屬於資料，會保留。這是相對上一版新增的容許行為。
- `,,,,`、`""`、`"   "` 與多個引號空欄位仍是資料記錄，會回報欄位數或空白值錯誤。
- 欄位數、空白值、時間格式與時間順序錯誤會收集到檔案結尾。一列可有多個診斷，但錯誤記錄數只計一次。
- 完全重複以五欄去除前後空白後判定；每次重複都列出首次出現與此次重複的来源。格式錯誤的五欄記錄也可被識別為重複。原始記錄保留在診斷中，不默默刪除。
- 標題不合法會停止，不繼續猜測欄位。標準 CSV 解析器報引號破損等語法錯誤時，也停止解析；來源範圍是當時已消耗的實體行，不能假定錯誤之後已檢查。
- **有任何輸入錯誤就不執行月台檢查**，`validation_complete=false`、`conflicts=null`、結束碼 2。有效列數只描述已讀到的資料，不代表做過部分月台檢查。
- 只有標題仍可接受：預設回報 `TIMETABLE_EMPTY` 警告及「沒有可供檢查的停站資料」，結束碼 0，不顯示一般成功句。`--require-data` 要求至少一筆有效資料，否則增加 `INPUT_DATA_REQUIRED` 並回傳 2。警告本身預設不造成非零結束碼。
- 完全空檔或只有空白行的檔案，仍因缺少標題而回傳 2。有合法標題且讀至結尾、但沒有有效資料時會出現空資料警告；其他錯誤的優先級不受影響。

## 文字報告

預設 `--format text`。範例衝突的診斷區塊如下，完整輸出另外包含限定範圍的結論、完成旗標、摘要與已執行／略過的檢查：

```text
[PLATFORM_OVERLAP] error
Message: distinct trains overlap on this exclusive platform resource
File: examples/sample_timetable.csv
Sources: line 2, line 4
Station: Taipei
Platform: 1
Trains: 101, 103
Overlap: 08:03 - 08:05 (2 minutes)
```

Windows 的檔案分隔符可能顯示為反斜線，且會依顯示政策轉義。錯誤報告寫到 stderr，其他文字報告寫到 stdout。報告以 UTF-8 輸出。

文字顯示保留正常繁體中文；資料中的換行、CR、Tab、反斜線顯示為 `\n`、`\r`、`\t`、`\\`。Unicode C 類別（控制、格式等字元）以及 U+2028／U+2029 顯示為 `\uXXXX` 或 `\UXXXXXXXX`，防止資料偽造新標題或終端控制序列。此轉義只用於顯示，**不修改比對值與 JSON 資料值**。

## JSON 契約：schema_version 1.0

`--format json` 只將一份有效 JSON 寫到 stdout，不混入進度訊息；即使輸入或命令列錯誤也相同，stderr 為空。`--format=json` 亦可。JSON 採標準字串轉義，解析後的值維持原值。`--help` 與 `--version` 是獨立資訊指令，輸出說明／版本文字，不執行驗證。

| 頂層欄位 | 型別與意義 |
| --- | --- |
| schema_version | 字串，目前 `"1.0"`；消費端依此解讀契約 |
| tool_version | 字串，實際 `validator.__version__` |
| source_file | 輸入路徑字串；命令列未通過驗證時為 null |
| validation_complete | 布林；輸入有效且本次啟用檢查全部完成時為 true，即使發現衝突 |
| parsing_complete | 布林；解析已讀至 EOF 時為 true，不代表輸入有效 |
| summary | 下表所列的確定計數或 null |
| checks_executed | 陣列，每項為 `{"code": "...", "complete": true/false}`；complete 表示掃描範圍完成，不表示規則通過 |
| checks_skipped | 陣列，每項為 `{"code": "...", "reason": "..."}`；reason 是人類說明 |
| diagnostics | 診斷物件陣列，沒有診斷時為空陣列 |

檢查代碼固定為 `CSV_INPUT`、`DUPLICATE_ROWS`、`PLATFORM_OVERLAP`。解析被中止時，已嘗試的輸入檢查標記 complete=false；任何輸入錯誤都會把 PLATFORM_OVERLAP 列入 checks_skipped。

| summary 欄位 | 定義 |
| --- | --- |
| records_read | 已成功解析出記錄邊界的非空白資料筆數；不含標題與無法解析的殘缺記錄 |
| records_valid | 已讀取且通過所有列驗證的筆數；重複出現的列不屬於有效列 |
| records_error | 已讀取且有至少一項輸入錯誤的記錄數；包含重複列 |
| records_duplicate | 重複出現的記錄數，是 records_error 的子集，不含首次出現 |
| total_records | 已解析到 EOF 時等於 records_read；標題錯誤、語法錯誤、讀取失敗等中止情況為 null，不捏造整份總筆數 |
| input_errors | 輸入／命令列 error 診斷數；可大於 records_error，也可包含檔案層級錯誤 |
| conflicts | 完整執行月台檢查後的重疊配對數；未執行為 null |
| warnings | warning 診斷數 |

永遠有 `records_read = records_valid + records_error`。上述記錄計數只涵蓋已可靠讀取的部分；`total_records=null` 時不能拿 records_read 當整份總數。

每個診斷固定含以下欄位：

| 欄位 | 型別與意義 |
| --- | --- |
| code | 下表的穩定字串代碼；不要以 message 判斷類型 |
| severity | `"error"` 或 `"warning"` |
| message | 可變的人類訊息 |
| source_file | 字串或 null |
| sources | `{"line_start": int, "line_end": int}` 陣列；均為含首尾的實體行範圍 |
| trains | 相關 Train 字串陣列，依來源順序；不適用為空陣列 |
| station / platform | 依既有 trim 規則的識別字串；不適用為 null |
| field | 輸入欄名或 null；欄位數／標題這類整筆結構錯誤為 null |
| details | 機器可讀的補充物件，見下表 |

| code | severity | details |
| --- | --- | --- |
| INPUT_MISSING_HEADER | error | expected：五個必要欄名 |
| INPUT_INVALID_HEADER | error | expected：必要欄名；actual：原始欄名陣列 |
| INPUT_COLUMN_COUNT | error | expected、actual：欄數；values：原始欄值陣列 |
| INPUT_EMPTY_FIELD | error | records：來源與原始欄值 |
| INPUT_INVALID_TIME | error | records：來源與原始欄值 |
| INPUT_INVALID_INTERVAL | error | records：來源與原始欄值；field 為 Departure |
| INPUT_DUPLICATE_ROW | error | records：首次與重複記錄，順序同 sources |
| INPUT_CSV_SYNTAX | error | record_boundary_reliable=false |
| INPUT_ENCODING | error | reason：原因；location_known=false |
| INPUT_READ_ERROR | error | reason：原因；location_known=false |
| INPUT_DATA_REQUIRED | error | require_data=true |
| TIMETABLE_EMPTY | warning | 空物件 |
| PLATFORM_OVERLAP | error | overlap_start／overlap_end：HH:MM；overlap_minutes：正整數；records：兩筆來源資料 |
| CLI_ARGUMENT_ERROR | error | 空物件 |

`details.records` 每項為 `{"source": {"line_start": 2, "line_end": 2}, "values": {"Train": "101", ...}}`。來源順序與 trains 對應；衝突順序沿用既有時間排序。原始欄值未做文字顯示轉義。

檔案無法開啟、解碼失敗、無標題空檔、空資料或命令列錯誤可能沒有可確定的來源行，sources 為空陣列。尤其解碼器可能預先讀取資料，不會編造出錯行號。

## 結束碼

| 結束碼 | 意義 |
| --- | --- |
| 0 | 輸入有效，完整執行本次已啟用檢查且沒有違規；空表警告預設仍為 0 |
| 1 | 輸入有效，完整檢查發現月台重疊 |
| 2 | 輸入、重複資料、讀取、命令列或 require-data 錯誤；檢查未完成 |

文字與 JSON 共用同一份 Report 和結束碼計算。輸入錯誤優先，不以剩餘有效列產生「沒有衝突」結論。

## 測試與 CI

```sh
python -m unittest discover -s tests -v
```

保留原有 23 個測試案例；只更新本輪明確改變的 CLI 文字預期與 UTF-8 擷取方式。新增來源、重複、多錯誤、空白記錄、JSON、控制字元和所有結束碼測試。固定種子隨機測試使用獨立逐對比較參考演算法，不呼叫受測重疊演算法來產生預期值。

本機驗證環境為 **Windows、Python 3.13.5**。新增 GitHub Actions 設定，規劃 `ubuntu-latest / windows-latest × Python 3.9 / 3.13`，執行同一套 unittest（包含實際 CLI 子程序），不安裝專案依賴。CI 設定存在不代表遠端已通過，請以該提交的 Actions 結果為準；本文件不宣稱未觀察到的組合已驗證。

## 刻意保留的限制

只處理同一天 HH:MM、嚴格五欄 CSV 與上述月台排他資源模型。跨午夜、HH:MM:SS、零分鐘停站均未支援。不檢查同 Train 行程、站間行車時間、車组運用、單線區間、折返、號誌與真實營運安全。

下一輪候選尚未實作：使用者指定月台交接間隔、明確服務日跨午夜、秒數、離線 HTML、安裝套件與雙語文件。GTFS／行程模型留待真實資料需求；本輪沒有資料庫、Web 伺服器或 AI 自動修正。

## 授權

維持原文 [MIT License](LICENSE)。
