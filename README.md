# Railway Timetable Validator

以 Python 標準函式庫打造的開源鐵路時刻表檢查工具。讀取 CSV 時刻表，找出同一車站、同一月台上不同列車的占用時間重疊，並列出列車編號及實際重疊時段。

## 使用方式

需要 Python 3.9 以上版本，不需要安裝任何額外套件。

```sh
git clone https://github.com/andy32012/railway-timetable-validator.git
cd railway-timetable-validator
python validator.py examples/sample_timetable.csv
```

檢查自己的檔案或查看指令說明：

```sh
python validator.py "path/to/timetable.csv"
python validator.py --help
```

若系統使用 `python3` 指令，請以 `python3` 取代 `python`；Windows 也可以使用 `py`。

## CSV 格式

檔案使用 UTF-8 編碼（可含 BOM），必須包含以下五個欄位，欄名大小寫必須一致。可調整欄位順序，但不可缺少、重複或增加欄位。

```csv
Train,Station,Platform,Arrival,Departure
101,Taipei,1,08:00,08:05
102,Taipei,2,08:01,08:06
103,Taipei,1,08:03,08:08
104,Taipei,1,08:08,08:12
105,Banqiao,1,08:03,08:07
```

| 欄位 | 說明 |
| --- | --- |
| Train | 列車編號，視為文字，保留前置零 |
| Station | 車站名稱 |
| Platform | 月台編號或名稱，視為文字 |
| Arrival | 到站時間，24 小時制 `HH:MM` |
| Departure | 離站時間，24 小時制 `HH:MM` |

欄位前後空白會被移除，完全空白的行會被略過。其餘空白值、欄位數錯誤或無效時間會顯示錯誤與可用的行號。含逗號的值需依 CSV 規則使用雙引號包住。

## 檢查規則與第一版範圍

- 每份 CSV 代表同一天；時間範圍為 `00:00` 至 `23:59`，離站必須晚於到站。跨午夜與零分鐘停站會回報輸入錯誤。
- 只有車站與月台皆相同，且列車編號不同的兩筆資料才會檢查衝突。名稱與編號區分大小寫，`1` 與 `01` 是不同月台。
- 占用區間為 `[Arrival, Departure)`：例如前車 `08:05` 離站、後車 `08:05` 到站，不算衝突。此版本沒有額外的安全間隔設定。
- 不要求 CSV 按時間排序；會找出所有重疊配對，包括完全包含另一停站時段的情況。
- 同一列車編號的資料不互相回報衝突；第一版不檢查重複資料、列車行程、站間行車時間或實際鐵路安全規範。
- 只有標題列的 CSV 代表空時刻表，沒有衝突；完全空檔案會回報格式錯誤。

## 執行範例

```sh
python validator.py examples/sample_timetable.csv
```

輸出：

```text
[CONFLICT]
Station: Taipei
Platform: 1
Train 101 overlaps with Train 103
Overlap: 08:03 - 08:05
```

有多組衝突時，各組之間以空白行分隔。沒有衝突時輸出：

```text
No conflicts found.
```

輸入或檔案讀取錯誤時，錯誤訊息以 `[ERROR]` 開頭並寫入標準錯誤輸出。程式結束碼：

| 結束碼 | 意義 |
| --- | --- |
| `0` | 檢查完成，沒有衝突 |
| `1` | 檢查完成，發現衝突（範例檔預期回傳此值） |
| `2` | 輸入格式、檔案讀取或命令列參數錯誤 |

命令列參數錯誤由 `argparse` 顯示用法與錯誤訊息。

## 執行測試

在專案根目錄執行：

```sh
python -m unittest discover -s tests -v
```

測試使用標準函式庫 `unittest`，涵蓋重疊區間、相鄰時段、不同車站／月台、未排序資料、CSV 驗證及命令列輸出與結束碼。

## 授權

本專案採用 [MIT License](LICENSE)。
