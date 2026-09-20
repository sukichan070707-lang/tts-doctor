# Changelog

## v1.1 — 2026-09-20

### 新增
- **`--make-launcher`**：產生一鍵啟動腳本（`啟動語音服務.bat` / `.ps1`），把
  `NUMBA_DISABLE_JIT` / `PYTHONUTF8` / `PYTHONUNBUFFERED` 三個環境變數、
  權重路徑、裝置與埠號全部寫死在裡面。**消滅「每次啟動都要手打三行」這個痛點。**
- `--out` / `--pth` / `--ckpt` / `--device`：可覆寫啟動腳本的路徑與裝置。

### 修正
- **`--help` 在 Windows 中文 console 下會崩潰。**
  `UnicodeEncodeError: 'charmap' codec can't encode characters`
  —— 這是使用者會跑的第一條命令，卻撞上了本工具指南第七章所描述的那個坑。
  **工具自己不能踩自己的坑。** 已在檔案開頭強制 `sys.stdout/stderr` 使用 UTF-8。
  （感謝 `--help` 自測。）

---

## v1.0 — 2026-09-20

### 首次發佈
- 九項環境檢查：Python/PyTorch（含 CUDA）、必要套件、**`numba` × `librosa` 卡死**、
  nltk 資料、`jieba_fast`、模型與權重、`tts_infer.yaml` 路徑、啟動環境變數、埠與記憶體判讀。
- **第 3 項會自動覆驗 `NUMBA_DISABLE_JIT=1`**，直接向使用者確認病因，而不是只把可能性列出來。
- `--fix`：自動建立 `jieba_fast` 替身模組。
- `--json`：機器可讀輸出，可接 CI。

### 已知修正（自測抓到）
- **逐一檢查套件會誤判。**
  v1.0 把 `pytorch_lightning` 和 `peft` 誤報成「缺少」——其實裝著。
  原因：每個套件開一個子行程、每個子行程都要冷啟動匯入 torch，15 個模組等於冷啟 15 次，
  被 60 秒逾時一刀切。
  **修法**：改成 `batch_import()` 一次子行程批次匯入，逾時拉到 600 秒，
  並把「逾時」與「缺少」分開報告。
  **覆驗：15 / 15 通過，50.3 秒。**

### 設計原則
**每一個輸出行都必須是「我親自驗證過的事實」，不是「可能的原因」。**
所以第 3 項不寫「可能是 numba 的問題」，而是**自動再測一次並把結果印出來**。
