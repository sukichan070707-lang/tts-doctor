# 《Windows 上從零把 GPT-SoVITS 跑起來》
## ——以及那七個沒人寫清楚的坑

> 版本 1.0 ｜ 2026-09-20
> 本文所有步驟均在 **Windows 11 + Python 3.12 + 無獨立顯卡（純 CPU）** 環境下親手驗證。
> 作者實測環境：`C:\Users\<你>\AppData\Local\<你的聲音資料夾>\gsv\GPT-SoVITS-main`

---

## 寫在前面：這篇文章是給誰看的

**如果你用的是官方「整合包」（例如 `GPT-SoVITS-100T-cu128` 這類自帶 Python 與依賴的一鍵包），本文對你沒用——雙擊就能跑。**

**但如果你的整合包丟了、清掉了、換電腦了**，而你手邊只剩：

- GPT-SoVITS 的**原始碼**（`GPT-SoVITS-main`）
- 你**自己訓練好的兩個權重**（`xxx.ckpt` ＋ `xxx.pth`）
- 一段**參考音**（`ref.wav`）

**那你會遇到本文要解決的全部問題。** 這些問題的共通點是：**錯誤訊息完全看不出原因，而且會讓你以為是自己弄壞了。**

---

## 第一章 · 先搞清楚你缺什麼

一個能跑的 GPT-SoVITS，需要**三層**東西：

| 層 | 內容 | 常見誤解 |
|---|---|---|
| **權重層** | `xxx.ckpt`（GPT）＋ `xxx.pth`（SoVITS）＋ 參考音 | 以為有權重就能跑 → **錯，還缺下面兩層** |
| **模型層** | `chinese-hubert-base`、`chinese-roberta-wwm-ext-large`、`sv\pretrained_eres2net*.ckpt` | 整合包裡有，原始碼目錄裡**通常沒有** |
| **環境層** | Python 套件、nltk 資料、編譯器 | **本文 90% 的坑都在這一層** |

**最快的自我診斷：**

1. 你的 `GPT_SoVITS\pretrained_models\` 底下有沒有 `chinese-hubert-base` 和 `chinese-roberta-wwm-ext-large`？**沒有 → 去官方倉庫下載補齊。**
2. 你的 `xxx.ckpt` / `xxx.pth` 有幾 MB？**如果是幾百 MB（例如 148 MB ＋ 165 MB），那是完整權重，可以獨立跑。**
3. `python -c "import torch; print(torch.cuda.is_available())"` 是 True 還是 False？**False → 你是純 CPU，請看第六章。**

---

## 第二章 · 環境安裝：用鏡像，別用官方源

**問題**：`pip install` 官方源在某些地區會極慢，一個套件可以卡十幾分鐘。

**解法**：換鏡像。

```powershell
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <套件>
```

**另外一個必踩的坑**：pip 需要寫入「系統暫存目錄」和「site-packages」。**如果你的環境有沙箱、防毒、或權限限制，pip 會用一句 `Errno 13 Permission denied` 打發你，而且指向一個你根本沒動過的暫存檔。**

看到這句，**不是你的指令寫錯，是權限**。用管理員權限的終端機，或把 `TEMP` 指到你有寫入權的目錄。

**必裝清單（給中文用，實測可用）：**

```powershell
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple ^
  librosa soundfile fastapi uvicorn pytorch-lightning ^
  g2p_en jieba pypinyin cn2an chardet psutil sentencepiece ^
  peft x_transformers torchmetrics av wordsegment ^
  split-lang fast_langdetect rotary_embedding_torch matplotlib
```

> **日文／韓文／粵文使用者**：另外需要 `pyopenjtalk`（日）、`jamo`＋`g2pk2`（韓）、`ToJyutping`（粵）。**這三個在 Windows 上都要編譯，通常裝不起來。但純中文完全不需要它們。**

---

## 第三章 · 坑一（最致命）：`numba` 匯入 `librosa` 時永恆卡死

### 症狀

- 執行 `python api.py ...` 之後，**什麼都不輸出**
- **CPU 跑滿，但記憶體幾乎不漲**（例如燒掉 8000 秒 CPU，記憶體只有 137 MB）
- **埠永遠不開**
- 放幾個小時也不會好

### 為什麼看不出來

因為它不報錯。**它只是永遠不返回。**

### 怎麼定位（這招請學起來）

把匯入鏈一段一段切開，每一步都印出來：

```python
# probe.py
import sys, os, time
def p(m): print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)

G = r"<你的路徑>\GPT-SoVITS-main"
os.chdir(G)
sys.path.insert(0, os.path.join(G, "GPT_SoVITS"))
sys.path.insert(0, G)

for name, code in [
    ("torch", "import torch"),
    ("AR.t2s_lightning", "from AR.models.t2s_lightning_module import Text2SemanticLightningModule"),
    ("module.mel_processing", "from module.mel_processing import spectrogram_torch"),
    ("feature_extractor.cnhubert", "from feature_extractor import cnhubert"),
    ("text.chinese", "from text import chinese"),
    ("TTS_pack", "from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config"),
]:
    p(">>> " + name)
    t0 = time.time()
    try:
        exec(code, {}); p("    OK %.1fs" % (time.time()-t0))
    except Exception as e:
        p("    FAIL %s: %s" % (type(e).__name__, e))
```

**你會看到它停在：**

```
[02:35:15] >>> module.mel_processing
（然後就沒有然後了）
```

而 `module/mel_processing.py` 的第三行是：

```python
from librosa.filters import mel as librosa_mel_fn
```

### 根因

**`numba` 的即時編譯（JIT）在某些 Windows 環境下會在 `librosa` 匯入時卡死。**

### 解法（一行環境變數）

```powershell
$env:NUMBA_DISABLE_JIT = '1'
```

**實測：那一行從「永遠卡住」變成 0.4 秒。**

> 代價：`librosa` 的相關運算會走純 Python，慢一點。**但對 TTS 推論來說，這點損耗遠小於「永遠跑不起來」。**

---

## 第四章 · 坑二：`jieba_fast` 在 Windows 裝不上

### 症狀

```
ModuleNotFoundError: No module named 'jieba_fast'
```

### 為什麼裝不上

```
error: Microsoft Visual C++ 14.0 or greater is required.
ERROR: Failed building wheel for jieba_fast
```

**它是 C 擴充，需要 MSVC 編譯器。** 而且它對新版 Python（3.12+）的支援很差。

### 解法：寫一個「替身模組」

`jieba_fast` 是 `jieba` 的加速版，**API 完全相容**。所以直接做一個轉接頭。

在 `site-packages` 裡建一個資料夾 `jieba_fast\`，放四個檔案：

**`__init__.py`**
```python
# -*- coding: utf-8 -*-
"""jieba_fast shim -> jieba (API-compatible drop-in)"""
from jieba import *
import jieba as _jieba
__version__ = getattr(_jieba, "__version__", "0.42.1")
from jieba import (cut, cut_for_search, lcut, lcut_for_search,
                   Tokenizer, dt, initialize, load_userdict,
                   set_dictionary, get_FREQ, enable_parallel, disable_parallel)
```

**`posseg.py`**
```python
from jieba.posseg import *
from jieba.posseg import cut, lcut, dt, pair, POSTokenizer
```

**`analyse.py`**
```python
from jieba.analyse import *
```

**`finalseg.py`**
```python
from jieba import finalseg as _fs
from jieba.finalseg import *
```

**驗證：**
```powershell
python -c "import jieba_fast, jieba_fast.posseg; print('OK')"
```

> `site-packages` 位置：`python -c "import site; print(site.getusersitepackages())"`

---

## 第五章 · 坑三：`nltk` 資料缺失導致 `g2p_en` 初始化卡住

### 症狀

```python
from g2p_en import G2p
g = G2p()      # ← 卡在這裡
```

### 根因

`g2p_en` 初始化時需要 nltk 的 **`cmudict`**。缺它的時候，某些版本的 nltk 會**嘗試自動下載**——而那個下載在你的網路環境裡可能就是卡住。

### 診斷

```powershell
python -c "
import nltk
for pkg in ['cmudict','averaged_perceptron_tagger_eng','punkt']:
    for d in ['corpora/','tokenizers/','taggers/']:
        try:
            nltk.data.find(d+pkg); print('HAVE', pkg); break
        except Exception:
            pass
    else:
        print('MISS', pkg)
"
```

### 解法

```powershell
python -c "import nltk; nltk.download('cmudict')"
```

**驗證：**
```powershell
python -c "import time; t=time.time(); from g2p_en import G2p; G2p(); print('ok %.1fs' % (time.time()-t))"
```
應該在數秒內完成。

> `punkt` 缺不影響中文推理。若下載被權限擋住（`PermissionError: ...nltk_data\tokenizers`），可以直接跳過。

---

## 第六章 · 坑四：`tts_infer.yaml` 指向不存在的權重

### 症狀

**這個坑最陰險**：它不一定報錯，而是**讓程式去網路上找一個不存在的基礎模型，然後卡在下載重試裡**（跟第三章的症狀很像：CPU 空轉、記憶體不漲）。

### 診斷

打開 `GPT_SoVITS\configs\tts_infer.yaml`，**逐條檢查每個路徑的檔案是否真的存在**：

```powershell
$y = "<路徑>\GPT_SoVITS\configs\tts_infer.yaml"
Get-Content $y
# 然後針對每一個 t2s_weights_path / vits_weights_path 去 Test-Path
```

**原始碼目錄常見缺檔：**
- `pretrained_models\gsv-v2final-pretrained\s1bert25hz-*.ckpt`
- `pretrained_models\gsv-v2final-pretrained\s2G2333k.pth`
- `pretrained_models\s1v3.ckpt`
- `pretrained_models\v2Pro\s2Gv2ProPlus.pth`

**這些原本是整合包提供的。** 但**如果你有自己的完整權重，就不需要它們**——直接把設定指過去。

### 解法：改設定，指向你自己的權重

**先備份：**
```powershell
Copy-Item $yaml "$yaml.bak"
```

**改成（以自訂權重為例）：**

```yaml
custom:
  bert_base_path: GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
  cnhuhbert_base_path: GPT_SoVITS/pretrained_models/chinese-hubert-base
  device: cpu
  is_half: false
  t2s_weights_path: <絕對路徑>\你的權重.ckpt
  version: v2ProPlus        # ← 要跟你的權重訓練版本一致
  vits_weights_path: <絕對路徑>\你的權重.pth
```

> **版本怎麼判斷？** 看權重檔大小對照：v2 的 GPT 約 100–150 MB、SoVITS 約 97 MB；v2ProPlus 系列的 SoVITS 明顯更大。**不確定就先試 v2，再試 v2ProPlus，看哪個能載入。**

---

## 第七章 · 啟動與驗收

```powershell
$B = "<你的聲音資料夾>"
Set-Location "$B\gsv\GPT-SoVITS-main"

# --- 這四行缺一不可 ---
$env:PYTHONUTF8 = '1'              # 否則中文說明會讓 argparse 崩在 cp1252
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'        # 否則你什麼日誌都看不到
$env:NUMBA_DISABLE_JIT = '1'       # ← 沒有這一行就永遠卡死

python -u api.py `
  -s "$B\gsv\<你的>\xxx.pth" `
  -g "$B\gsv\<你的>\xxx.ckpt" `
  -d cpu -a 127.0.0.1 -p 9880
```

### 驗收標準（兩個都要滿足）

1. **埠 9880 有在聽**
   ```powershell
   Test-NetConnection 127.0.0.1 -Port 9880
   ```
2. **記憶體漲到 GB 級**
   ```powershell
   Get-Process python | Select-Object Id, @{n='MemMB';e={[int]($_.WorkingSet64/1MB)}}
   ```
   **載入成功通常是 3 GB 以上。如果你看到的是 100–500 MB 而且 CPU 在燒 → 回到第三章。**

### 送第一個請求

```python
import json, urllib.request
payload = {
    "refer_wav_path": r"<你的參考音>.wav",
    "prompt_text": "<參考音對應的文字>",
    "prompt_language": "zh",
    "text": "測試，一二三。",
    "text_language": "zh",
}
req = urllib.request.Request("http://127.0.0.1:9880/",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
open("out.wav", "wb").write(urllib.request.urlopen(req, timeout=3600).read())
```

**⚠️ 用繁體中文使用者注意：** 送進 TTS 的文字建議用**簡體**，發音會更穩。顯示給人看的版本再轉繁體。

---

## 第八章 · 關於純 CPU：先把期待放對

**這一章是實話，不是勸退。**

純 CPU 環境下：
- **模型載入**：實測約 2 分鐘（記憶體上到 3.2 GB）
- **推論**：**極慢。** 實測送一段約 4 句的文字，**跑了一小時仍未返回，最終逾時。**

**結論：**
- ✅ **純 CPU 可以「跑起來」**，可以驗證環境、可以做極短句測試
- ❌ **純 CPU 不適合「日常使用」**，不適合長文、不適合做有聲書

**如果你真的要用，三條路：**
1. **一次只送一句**（越短越有機會完成）
2. **改走 CosyVoice2 零樣本**（0.5B 小模型，比 GPT-SoVITS 有機會）
3. **租雲端 GPU**（一次性生成最划算）

---

## 附錄 · 完整症狀對照表

| 症狀 | 最可能的原因 | 解法 |
|---|---|---|
| 什麼都不輸出、CPU 空轉、記憶體不漲 | `numba` × `librosa` 卡死 | `NUMBA_DISABLE_JIT=1`（第 3 章） |
| `ModuleNotFoundError: jieba_fast` | 無 MSVC 編譯器 | 替身模組（第 4 章） |
| `G2p()` 卡住 | 缺 nltk `cmudict` | `nltk.download('cmudict')`（第 5 章） |
| `Errno 13 Permission denied` 指向暫存檔 | pip 沒有寫入權 | 管理員權限／改 TEMP（第 2 章） |
| pip 卡十幾分鐘 | 官方源太慢 | 換鏡像（第 2 章） |
| 卡在下載、CPU 空轉 | yaml 指向不存在的權重 | 改設定指向自己的權重（第 6 章） |
| argparse 崩在 `cp1252` | 中文說明輸出編碼 | `PYTHONUTF8=1`（第 7 章） |
| 全部日誌都看不到 | stdout 被緩衝 | `PYTHONUNBUFFERED=1`（第 7 章） |
| 埠不開、記憶體只有 100–500 MB | 還在卡死階段 | 回到第 3 章驗證 |
| 跑很久不返回 | 純 CPU 推論上限 | 縮短文字／換 CosyVoice2／租 GPU（第 8 章） |

---

## 最後

這篇文章裡的每一條，都是**先卡死、再挖出來、再驗證過的**。

**寫它的人不是因為很懂才寫，是因為剛好在那裡卡了一整晚。**

如果你也在同一個地方卡住——那恭喜，你踩的是**同一個坑**，而不是你特別笨。

*極曦 · 2026-09-20*
