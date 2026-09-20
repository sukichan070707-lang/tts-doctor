# tts-doctor

**GPT-SoVITS / CosyVoice environment doctor for Windows.**
**GPT-SoVITS / CosyVoice 環境醫生（Windows）**

> 一條命令，告訴你為什麼「什麼都不輸出、CPU 跑滿、記憶體不漲、埠永遠不開」。
> One command to find out why your TTS server hangs with no output at all.

[![Python](https://img.shields.io/badge/python-3.9%2B-blue)]() [![License](https://img.shields.io/badge/license-MIT-green)]() [![Deps](https://img.shields.io/badge/dependencies-none-brightgreen)]()

---

## 這個工具解決什麼問題

全世界最常見、最難查、**網路上沒有任何完整答案**的那個坑：

> 你執行 `python api.py`，然後——**什麼都沒有。**
> 沒有錯誤訊息。沒有日誌。CPU 跑滿了。記憶體只有 137 MB。埠永遠不開。放三個小時也不會好。

**病因是：`numba` 在匯入 `librosa.filters` 時，在某些 Windows 環境下會永恆卡死。**

**解法是一行環境變數：`NUMBA_DISABLE_JIT=1`。**

**但沒有人知道這件事，因為它不報錯。**

`tts_doctor` 會：
- 用**逾時偵測**抓出這個卡死（不是等它報錯，它不會報錯）
- **自動加上 `NUMBA_DISABLE_JIT=1` 再測一次**，直接向你確認病因
- 順便檢查另外七個常見但同樣隱蔽的死因

---

## The problem this solves

The most common, hardest-to-diagnose, and **completely undocumented** failure mode:

> You run `python api.py` and — **nothing.**
> No error. No log. CPU pegged. Memory stuck at 137 MB. Port never opens. It will not recover.

**Root cause: `numba` deadlocks on `from librosa.filters import mel` in some Windows environments.**
**Fix: one environment variable — `NUMBA_DISABLE_JIT=1`.**
Nobody knows this, because **it never raises an error.**

---

## 快速開始 / Quick start

```bash
python tts_doctor.py
```

它會自動尋找 GPT-SoVITS 安裝目錄。找不到就指定：

```bash
python tts_doctor.py --root "D:\GPT-SoVITS-100T"
python tts_doctor.py --fix             # 順便套用可安全自動化的修復
python tts_doctor.py --json            # 給 CI 用的機器可讀輸出
python tts_doctor.py --port 9880       # 指定埠號
python tts_doctor.py --make-launcher   # ★ 產生一鍵啟動腳本（見下）
```

### ★ `--make-launcher`：把三個環境變數寫死進一個雙擊檔

診斷完之後，最大的痛點還在：**每次啟動都要手打那三行環境變數，而只要漏掉 `NUMBA_DISABLE_JIT=1`，它就會再卡死一次。**

`--make-launcher` 會讀你的 `tts_infer.yaml`、找出權重路徑，然後產生兩個檔案：

- **`啟動語音服務.bat`** —— 雙擊就能跑
- **`啟動語音服務.ps1`** —— 可在 PowerShell 執行

**環境變數、權重路徑、裝置、埠號全部寫死在裡面。** 以後不用再手打任何一行。

```bash
python tts_doctor.py --make-launcher --out "D:\我的捷徑"
python tts_doctor.py --make-launcher --pth "D:\w\a.pth" --ckpt "D:\w\a.ckpt" --device cuda
```

> 產生的腳本會包含你自己的本機路徑，**不要把它推上公開 repo**（`.gitignore` 已幫你排除日誌與權重）。


**零第三方依賴。只用 Python 標準函式庫。** 任何 Python 3.9+ 都能直接跑。

---

## 九項檢查 / What it checks

| # | 檢查項 | 為什麼重要 |
|---|---|---|
| 1 | Python 與 PyTorch（含 CUDA 偵測） | 純 CPU 能跑，但推論會極慢 |
| 2 | 必要套件（**批次匯入**） | 逐一檢查會被誤判，見下方「已知陷阱」 |
| 3 | **`numba` × `librosa` 卡死** ← 最致命 | 會自動覆驗 `NUMBA_DISABLE_JIT=1` |
| 4 | `nltk` 資料（`cmudict` 等） | 缺它 `g2p_en` 初始化會卡住，症狀與 #3 極像 |
| 5 | `jieba_fast`（`--fix` 可自動寫替身模組） | Windows 上需 MSVC，通常裝不起來 |
| 6 | 模型與權重 | hubert / roberta / sv / 自訂權重 |
| 7 | `tts_infer.yaml` 路徑是否真的存在 | 指向不存在的檔案會卡在下載重試 |
| 8 | 啟動前的環境變數 | `NUMBA_DISABLE_JIT` / `PYTHONUTF8` / `PYTHONUNBUFFERED` |
| 9 | 埠與**記憶體判讀法** | ≥3 GB＝載入成功；100–500 MB 且 CPU 一直漲＝卡死 |

---

## 記憶體判讀法（請記住這一條）

| 你看到的 | 意思 |
|---|---|
| 記憶體 **≥ 3 GB** | 模型**載入成功**了，可以送請求 |
| 記憶體 **100–500 MB**，CPU 一直漲，沒有輸出 | **它卡死了。** 回第 3 項 |
| 記憶體持續往上爬 | 正常，正在載入，等它 |

**這一條比任何錯誤訊息都有用。**

---

## 完整症狀對照表 / Symptom → cause

| 症狀 | 最可能原因 | 解法 |
|---|---|---|
| 什麼都不輸出、CPU 空轉、記憶體不漲 | `numba` × `librosa` 卡死 | `NUMBA_DISABLE_JIT=1` |
| `ModuleNotFoundError: jieba_fast` | 無 MSVC 編譯器 | `--fix` 自動寫替身模組 |
| `G2p()` 卡住 | 缺 nltk `cmudict` | `python -c "import nltk; nltk.download('cmudict')"` |
| `Errno 13 Permission denied` 指向暫存檔 | pip 沒有寫入權 | 管理員權限／改 `TEMP` |
| pip 卡十幾分鐘 | 官方源太慢 | 換鏡像源 |
| 卡在下載、CPU 空轉 | yaml 指向不存在的權重 | 改設定指向自己的權重 |
| argparse 崩在 `cp1252` | 中文說明輸出編碼 | `PYTHONUTF8=1` |
| 全部日誌都看不到 | stdout 被緩衝 | `PYTHONUNBUFFERED=1` |
| 跑很久不返回 | 純 CPU 推論上限 | 縮短文字／換小模型／租 GPU |

---

## 已知陷阱（包含本工具自己踩過的）

### 逐一檢查套件會誤判
v1.0 把 `pytorch_lightning` 和 `peft` **誤報成「缺少」**——其實裝著。

**原因**：每個套件開一個子行程、每個子行程都要冷啟動匯入 torch，15 個模組等於冷啟 15 次，被逾時一刀切。

**修法**：改成 `batch_import()`——**一次子行程批次匯入**，逾時拉長，並把「逾時」與「缺少」分開報告。

**覆驗：15 / 15 通過，50.3 秒。**

> 這個 bug 是**自測時抓到的**，不是使用者回報的。我們把它寫在文件裡，因為**它就是這個工具存在的理由**：不要相信「應該可以」。

---

## 純 CPU 的實話 / The honest truth about CPU-only

純 CPU 環境下實測：

- **模型載入**：約 2 分鐘，記憶體上到 3.2 GB ✅
- **推論**：送一段約 4 句的文字，**跑了一小時仍未返回，最終逾時** ❌

**結論：**
- ✅ 純 CPU 可以「跑起來」、可以驗證環境、可以做極短句測試
- ❌ 純 CPU 不適合日常使用、不適合長文、不適合做有聲書

**三條出路**：① 一次只送一句 ② 改走 CosyVoice2 零樣本（0.5B 小模型） ③ 租雲端 GPU

---

## 搭配的完整指南

本 repo 根目錄有一份完整實戰指南：
**《Windows 上從零把 GPT-SoVITS 跑起來——以及那七個沒人寫清楚的坑》**

裡面有逐步的診斷腳本、修復做法、以及每一條為什麼會這樣。

---

## 授權 / License

MIT。拿去用、拿去改、拿去商業使用，不用問。

---

## 這個工具是怎麼來的

它不是在辦公室裡設計出來的。

它是有人**在同一個地方卡了整整一晚**——燒掉 8837 秒的 CPU、記憶體只有 137 MB、什麼都不知道——然後一點一點挖出來的。

**如果你也在同一個地方卡住：恭喜，你踩的是同一個坑，而不是你特別笨。**

*極曦 · Jixi*
