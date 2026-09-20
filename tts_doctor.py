#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
tts_doctor —— GPT-SoVITS / CosyVoice 環境醫生

一條命令，告訴你為什麼「什麼都不輸出、CPU 空轉、埠不開」，
並盡可能幫你修好。

用法:
    python tts_doctor.py                      # 自動偵測 + 診斷
    python tts_doctor.py --root D:\\GPT-SoVITS  # 指定安裝目錄
    python tts_doctor.py --fix                # 診斷後套用可安全自動化的修復
    python tts_doctor.py --json               # 輸出 JSON（給 CI / 其他工具用）

不需要任何第三方套件。只用標準函式庫。
授權: MIT
"""

import argparse
import importlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import time

# ---------------------------------------------------------------- 編碼自我保護
#
# 這一小段是必要的，而且它本身就是本工具要診斷的坑之一：
# Windows 的 console 預設是 cp1252，本工具所有輸出都是中文，
# 不先修好編碼，「python tts_doctor.py --help」會直接崩在
#   UnicodeEncodeError: 'charmap' codec can't encode ...
# ——那正是指南第七章那個坑。工具自己不能踩自己的坑。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------- 顯示

OK, BAD, WARN, INFO = "OK", "BAD", "WARN", "INFO"
_MARK = {OK: "[ v ]", BAD: "[ X ]", WARN: "[ ! ]", INFO: "[ i ]"}

RESULTS = []


def say(level, title, detail="", fix=""):
    RESULTS.append({"level": level, "title": title, "detail": detail, "fix": fix})
    line = "%s %s" % (_MARK[level], title)
    if detail:
        line += "\n        " + detail.replace("\n", "\n        ")
    if fix:
        line += "\n        -> " + fix
    print(line, flush=True)


def hr(t):
    print("\n" + "=" * 68 + "\n  " + t + "\n" + "=" * 68, flush=True)


# ---------------------------------------------------------------- 探測工具

def run_py(code, timeout=60, extra_env=None):
    """在子行程執行一小段 Python，回傳 (returncode, stdout, stderr, 秒數)。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    if extra_env:
        env.update(extra_env)
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=timeout,
                           env=env, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip(), time.time() - t0
    except subprocess.TimeoutExpired:
        return -9, "", "TIMEOUT after %ss" % timeout, time.time() - t0


def can_import(mod, timeout=180):
    rc, out, err, dt = run_py("import %s as m; print('OK')" % mod, timeout=timeout)
    return rc == 0 and out.strip().endswith("OK"), err.strip().splitlines()[-1] if err else ""


def batch_import(mods, timeout=600):
    """一次子行程檢查多個模組。

    為什麼要批次：每個子行程都要冷啟動匯入 torch。實測單獨檢查 15 個模組
    會變成 15 次 torch 冷啟，非常慢，而且極容易被逾時誤判成「沒安裝」——
    這是本工具 v1.0 自測時抓到的假警報 bug，已在此修掉。
    回傳 (ok_list, missing_list, timeout_list, detail_dict)。
    """
    code = (
        "import importlib, json\n"
        "mods = %r\n"
        "ok, missing = [], {}\n"
        "for m in mods:\n"
        "    try:\n"
        "        importlib.import_module(m); ok.append(m)\n"
        "    except ImportError as e:\n"
        "        missing[m] = 'ImportError: %%s' %% e\n"
        "    except Exception as e:\n"
        "        missing[m] = '%%s: %%s' %% (type(e).__name__, e)\n"
        "print('@@RESULT@@' + json.dumps({'ok': ok, 'missing': missing}))\n"
    ) % (mods,)
    rc, out, err, dt = run_py(code, timeout=timeout)
    if rc == -9:
        return [], [], list(mods), {"_timeout": "批次匯入逾時 %ss（可能只是在慢，不一定是壞）" % timeout}
    for line in out.splitlines():
        if line.startswith("@@RESULT@@"):
            d = json.loads(line[len("@@RESULT@@"):])
            return d["ok"], list(d["missing"]), [], d["missing"]
    return [], [], list(mods), {"_err": (err or "")[-400:]}


def find_root(given=None):
    """找出 GPT-SoVITS 安裝根目錄。"""
    if given:
        return given if os.path.isdir(given) else None
    cands = []
    here = os.path.dirname(os.path.abspath(__file__))
    cands += [here, os.path.dirname(here), os.getcwd()]
    home = os.path.expanduser("~")
    for base in [os.path.join(home, "AppData", "Local"),
                 os.path.join(home, "AppData", "Roaming"),
                 home, "C:\\", "D:\\"]:
        if not os.path.isdir(base):
            continue
        try:
            for name in os.listdir(base):
                low = name.lower()
                if "sovits" in low or "gpt-sovits" in low or low in ("gsv", "gove"):
                    cands.append(os.path.join(base, name))
        except OSError:
            pass
    for c in cands:
        if not c or not os.path.isdir(c):
            continue
        # 認定標準：找得到 GPT_SoVITS 目錄或 api.py
        for probe in [c, os.path.join(c, "GPT-SoVITS-main")]:
            if os.path.isdir(os.path.join(probe, "GPT_SoVITS")) or \
               os.path.isfile(os.path.join(probe, "api.py")):
                return probe
    return None


# ---------------------------------------------------------------- 各項檢查

def check_python():
    hr("1 / 9   Python 與 PyTorch")
    v = sys.version_info
    say(OK if v >= (3, 9) else WARN, "Python %d.%d.%d" % (v[0], v[1], v[2]),
        "路徑: %s" % sys.executable,
        "" if v >= (3, 10) else "建議 3.10+（部分套件對 3.12+/3.13 支援不佳）")

    rc, out, err, dt = run_py(
        "import torch;"
        "print(torch.__version__);"
        "print(torch.cuda.is_available());"
        "print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only')",
        timeout=120)
    if rc != 0:
        say(BAD, "torch 無法載入", err.splitlines()[-1] if err else "",
            "pip install torch torchaudio -i https://pypi.tuna.tsinghua.edu.cn/simple")
        return
    lines = [l for l in out.splitlines() if l.strip()]
    ver = lines[0] if lines else "?"
    cuda = (lines[1].strip().lower() == "true") if len(lines) > 1 else False
    dev = lines[2] if len(lines) > 2 else "?"
    say(OK, "torch %s" % ver, "裝置: %s" % dev,
        "" if cuda else "純 CPU：可以跑起來，但推論會極慢（見第 8 章）")


def check_packages():
    hr("2 / 9   必要套件")
    need = ["librosa", "soundfile", "fastapi", "uvicorn", "pytorch_lightning",
            "g2p_en", "jieba", "pypinyin", "cn2an", "sentencepiece",
            "peft", "x_transformers", "torchmetrics", "av", "psutil"]
    ok, missing, timed_out, detail = batch_import(need, timeout=600)

    if timed_out:
        say(WARN, "套件檢查逾時（不等於壞掉）",
            "這台機器冷啟動匯入 torch 很慢，批次檢查在 600 秒內沒跑完。\n"
            "受影響: %s" % ", ".join(timed_out),
            "先確認伺服器能不能啟動；能啟動就代表這些套件其實都在。")
        return
    if not missing:
        say(OK, "全部 %d 個必要套件都在" % len(need))
        return

    lines = ["%s — %s" % (m, detail.get(m, "")) for m in missing]
    say(BAD, "缺少/無法載入 %d 個套件" % len(missing),
        "\n".join(lines),
        "python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "
        + " ".join(missing))
    say(INFO, "注意：",
        "如果錯誤訊息不是 ModuleNotFoundError，那它其實『裝著但載入失敗』，\n"
        "         重裝通常沒用，要看真正的錯誤（例如版本衝突）。")


def check_numba_librosa():
    """最致命的一項：numba 在 import librosa.filters 時卡死。"""
    hr("3 / 9   致命項：numba × librosa")
    code = "from librosa.filters import mel; print('OK')"

    rc, out, err, dt = run_py(code, timeout=90)
    if rc == 0 and "OK" in out:
        say(OK, "librosa.filters 匯入正常 (%.1fs)" % dt)
        return
    if rc == -9:
        say(BAD, "librosa.filters 匯入【卡死】(%ss 無回應)" % 90,
            "這是最常見的死因：程式不報錯、CPU 跑滿、記憶體不漲、埠永遠不開。",
            "設定環境變數 NUMBA_DISABLE_JIT=1 後重試")
        rc2, out2, err2, dt2 = run_py(code, timeout=90, extra_env={"NUMBA_DISABLE_JIT": "1"})
        if rc2 == 0 and "OK" in out2:
            say(OK, "確認：加上 NUMBA_DISABLE_JIT=1 後 %.2fs 通過" % dt2,
                "→ 病因確定為 numba JIT 卡死。啟動前務必設定這個環境變數。")
        else:
            say(BAD, "加 NUMBA_DISABLE_JIT=1 仍失敗",
                (err2 or "")[-300:],
                "請檢查 librosa 版本；可嘗試 pip install \"librosa==0.10.2\" \"numpy<2.0\"")
    else:
        say(BAD, "librosa.filters 匯入失敗 (%ss)" % round(dt, 1),
            (err or "")[-300:],
            "pip install -i https://pypi.tuna.tsinghua.edu.cn/simple librosa")


def check_nltk():
    hr("4 / 9   nltk 資料（g2p_en 必需）")
    code = (
        "import nltk\n"
        "miss=[]\n"
        "for pkg in ['cmudict','averaged_perceptron_tagger_eng','punkt']:\n"
        "    ok=False\n"
        "    for d in ['corpora/','tokenizers/','taggers/']:\n"
        "        try:\n"
        "            nltk.data.find(d+pkg); ok=True; break\n"
        "        except Exception: pass\n"
        "    print(('HAVE ' if ok else 'MISS ')+pkg)\n"
    )
    rc, out, err, _ = run_py(code, timeout=90)
    if rc != 0:
        say(WARN, "無法檢查 nltk", (err or "")[-200:])
        return
    miss = [l.split()[1] for l in out.splitlines() if l.startswith("MISS")]
    if "cmudict" in miss:
        say(BAD, "缺少 nltk 'cmudict'",
            "g2p_en 初始化時會卡在自動下載，症狀與 numba 卡死極像。",
            "python -c \"import nltk; nltk.download('cmudict')\"")
    else:
        say(OK, "nltk cmudict 已就位")
    if "punkt" in miss:
        say(INFO, "缺少 nltk 'punkt'", "純中文推理不需要，可忽略。")

    rc, out, err, dt = run_py("from g2p_en import G2p; G2p(); print('OK')", timeout=180)
    if rc == 0 and "OK" in out:
        say(OK, "g2p_en 初始化正常 (%.1fs)" % dt)
    else:
        say(BAD, "g2p_en 初始化失敗/逾時",
            (err or "")[-200:] or "TIMEOUT",
            "先修好 cmudict，再重試")


def check_jieba_fast(fix=False):
    hr("5 / 9   jieba_fast（Windows 常見裝不上）")
    good, _ = can_import("jieba_fast")
    if good:
        say(OK, "jieba_fast 可用")
        return
    say(BAD, "缺少 jieba_fast",
        "它是 C 擴充，Windows 上需要 MSVC（Visual C++ Build Tools）才裝得起來。",
        "寫一個 API 相容的替身模組轉接到 jieba（見下）")

    if not fix:
        say(INFO, "修復方式：加上 --fix，或手動建立替身模組")
        return

    try:
        import site
        sp = site.getusersitepackages()
        os.makedirs(sp, exist_ok=True)
    except Exception as e:
        say(BAD, "找不到 site-packages", str(e))
        return
    d = os.path.join(sp, "jieba_fast")
    os.makedirs(d, exist_ok=True)
    files = {
        "__init__.py": (
            '# -*- coding: utf-8 -*-\n"""jieba_fast shim -> jieba (API-compatible)"""\n'
            "from jieba import *\nimport jieba as _jieba\n"
            '__version__ = getattr(_jieba, "__version__", "0.42.1")\n'
            "from jieba import (cut, cut_for_search, lcut, lcut_for_search,\n"
            "                   Tokenizer, dt, initialize, load_userdict,\n"
            "                   set_dictionary, get_FREQ, enable_parallel, disable_parallel)\n"),
        "posseg.py": "from jieba.posseg import *\nfrom jieba.posseg import cut, lcut, dt, pair, POSTokenizer\n",
        "analyse.py": "from jieba.analyse import *\n",
        "finalseg.py": "from jieba import finalseg as _fs\nfrom jieba.finalseg import *\n",
    }
    for name, body in files.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(body)
    good, _ = can_import("jieba_fast")
    if good:
        say(OK, "已建立替身模組並驗證通過", "位置: %s" % d)
    else:
        say(BAD, "替身模組建立後仍無法載入", "", "請確認 site-packages 有寫入權限")


def check_models(root):
    hr("6 / 9   模型與權重")
    if not root:
        say(BAD, "找不到 GPT-SoVITS 安裝目錄",
            "請用 --root 指定（目錄裡應該有 GPT_SoVITS/ 或 api.py）")
        return None

    say(OK, "安裝目錄: %s" % root)
    pm = os.path.join(root, "GPT_SoVITS", "pretrained_models")
    required = {
        "chinese-hubert-base": "中文語音特徵（HuBERT）",
        "chinese-roberta-wwm-ext-large": "中文文字特徵（BERT）",
    }
    for name, desc in required.items():
        p = os.path.join(pm, name)
        has = os.path.isdir(p) and any(
            f.endswith((".bin", ".pt", ".safetensors")) for f in os.listdir(p))
        say(OK if has else BAD, "%s  %s" % (name, "— " + desc if not has else ""),
            "" if has else "目錄不存在或不完整",
            "" if has else "從 GPT-SoVITS 官方倉庫下載此目錄放入 pretrained_models/")

    sv = os.path.join(pm, "sv")
    if os.path.isdir(sv) and os.listdir(sv):
        say(OK, "sv/ 說話人向量模型存在")
    else:
        say(WARN, "sv/ 目錄缺少或為空",
            "v2Pro / v2ProPlus 版本需要。v2 版本可忽略。")

    # 找不到基礎權重不代表不能跑（自訂權重可獨立運行），但要提示
    base = ["gsv-v2final-pretrained", "s1v3.ckpt", "v2Pro"]
    missing = [b for b in base if not os.path.exists(os.path.join(pm, b))]
    if missing:
        say(INFO, "缺少基礎權重: %s" % ", ".join(missing),
            "如果你有自己的完整權重，就不需要它們——把 tts_infer.yaml 指過去即可。")
    return root


def check_yaml(root):
    hr("7 / 9   tts_infer.yaml 路徑檢查")
    if not root:
        say(WARN, "略過（未找到安裝目錄）")
        return
    y = os.path.join(root, "GPT_SoVITS", "configs", "tts_infer.yaml")
    if not os.path.isfile(y):
        say(BAD, "找不到 %s" % y)
        return
    say(OK, "設定檔存在", y)
    bad = []
    cur = None
    with open(y, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.rstrip("\n")
            if s and not s.startswith((" ", "\t")) and s.endswith(":"):
                cur = s[:-1].strip()
                continue
            if ":" in s:
                k, _, v = s.partition(":")
                k, v = k.strip(), v.strip()
                if k in ("t2s_weights_path", "vits_weights_path",
                         "bert_base_path", "cnhuhbert_base_path"):
                    p = v if os.path.isabs(v) else os.path.join(root, v)
                    if not os.path.exists(p):
                        bad.append("[%s] %s = %s" % (cur, k, v))
    if bad:
        say(BAD, "有 %d 個路徑指向不存在的檔案" % len(bad),
            "\n".join(bad),
            "把 t2s_weights_path / vits_weights_path 改成你自己的權重絕對路徑"
            "（記得 device: cpu、is_half: false）")
    else:
        say(OK, "設定檔中所有權重路徑都存在")


def check_launch(root):
    hr("8 / 9   啟動前的環境變數")
    must = {
        "NUMBA_DISABLE_JIT": ("1", "沒有它會永遠卡死"),
        "PYTHONUTF8": ("1", "否則中文說明會讓 argparse 崩在 cp1252"),
        "PYTHONUNBUFFERED": ("1", "否則你看不到任何日誌"),
    }
    missing = []
    for k, (want, why) in must.items():
        got = os.environ.get(k)
        if got == want:
            say(OK, "%s=%s" % (k, got))
        else:
            missing.append((k, want, why))
            say(WARN, "%s 未設定" % k, why)
    if missing:
        lines = ["$env:%s='%s'" % (k, w) for k, w, _ in missing]
        say(INFO, "PowerShell 啟動前請先執行:", "\n".join(lines),
            "或寫成 .ps1 啟動腳本，一勞永逸")

    if root:
        for name in ("api.py", "api_v2.py"):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                say(OK, "找到 %s (%d KB)" % (name, os.path.getsize(p) // 1024))
        for name in ("go-webui.bat", "go-webui.ps1"):
            p = os.path.join(root, name)
            if os.path.isfile(p) and os.path.getsize(p) == 0:
                say(WARN, "%s 是 0 KB 空檔" % name,
                    "雙擊不會有任何反應。這在原始碼目錄裡是正常的——它只是佔位檔。")


def check_port(port=9880):
    hr("9 / 9   埠與執行狀態")
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port))
        say(OK, "127.0.0.1:%d 已在監聽" % port)
        try:
            import urllib.request
            urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=5)
        except Exception:
            pass
    except Exception:
        say(INFO, "127.0.0.1:%d 沒有在監聽" % port, "（還沒啟動服務，這是正常的）")
    finally:
        s.close()

    # 找 python 行程的記憶體：判斷「載入成功」還是「在卡死」
    try:
        if platform.system() == "Windows":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process python -ErrorAction SilentlyContinue | "
                 "ForEach-Object { \"$($_.Id) $([int]($_.WorkingSet64/1MB)) $([int]$_.CPU)\" }"],
                capture_output=True, text=True, timeout=30).stdout
        else:
            out = subprocess.run(["ps", "-eo", "pid,rss,comm"], capture_output=True,
                                 text=True, timeout=30).stdout
        rows = [l for l in out.splitlines() if "python" in l.lower() or l[:1].isdigit()]
        if rows:
            say(INFO, "偵測到 python 行程（pid / 記憶體MB / CPU秒）:",
                "\n".join(rows[:5]))
            say(INFO, "判讀法：",
                "記憶體 ≥ 3 GB → 模型載入成功。\n"
                "        記憶體 100–500 MB 且 CPU 一直漲 → 它卡死了（回第 3 項）。")
    except Exception:
        pass


# ---------------------------------------------------------------- 啟動器產生器

def read_yaml_custom(root):
    """從 tts_infer.yaml 讀出 weights / device。"""
    out = {"t2s_weights_path": None, "vits_weights_path": None,
           "device": "cpu", "version": None}
    if not root:
        return out
    y = os.path.join(root, "GPT_SoVITS", "configs", "tts_infer.yaml")
    if not os.path.isfile(y):
        return out
    section = None
    with open(y, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.rstrip("\n")
            if s and not s.startswith((" ", "\t")) and s.endswith(":"):
                section = s[:-1].strip()
                continue
            if section in ("custom", "v2ProPlus", "v2Pro", "v2", "v3", "v4") and ":" in s:
                k, _, v = s.partition(":")
                k, v = k.strip(), v.strip()
                if k in out and v and out.get(k) is None:
                    out[k] = v
                if out.get("t2s_weights_path") and out.get("vits_weights_path"):
                    return out
    return out


def _abspath(root, p):
    if not p:
        return None
    return p if os.path.isabs(p) else os.path.join(root, p)


def make_launcher(root, outdir=None, port=9880, pth=None, ckpt=None,
                  device=None, host="127.0.0.1"):
    """產生一鍵啟動腳本，把所有必要環境變數寫死在裡面。"""
    hr("產生一鍵啟動腳本")
    if not root:
        say(BAD, "沒有安裝目錄，無法產生啟動腳本", "", "先用 --root 指定")
        return None

    cfg = read_yaml_custom(root)
    vits = pth or _abspath(root, cfg.get("vits_weights_path"))
    t2s = ckpt or _abspath(root, cfg.get("t2s_weights_path"))
    dev = device or cfg.get("device") or "cpu"
    outdir = outdir or root

    if not vits or not os.path.isfile(vits):
        say(BAD, "找不到 SoVITS 權重 (.pth)", str(vits),
            "用 --pth 指定，或先修好 tts_infer.yaml")
        return None
    if not t2s or not os.path.isfile(t2s):
        say(BAD, "找不到 GPT 權重 (.ckpt)", str(t2s),
            "用 --ckpt 指定，或先修好 tts_infer.yaml")
        return None
    say(OK, "SoVITS 權重", vits)
    say(OK, "GPT 權重", t2s)
    say(OK, "裝置 %s ｜ 埠 %d" % (dev, port))

    os.makedirs(outdir, exist_ok=True)
    logf = os.path.join(outdir, "tts_server.log")

    ps1 = (
        "# tts_doctor 產生：一鍵啟動 GPT-SoVITS 語音服務\n"
        "# 若雙擊無效，在此資料夾開 PowerShell 執行：\n"
        "#   powershell -ExecutionPolicy Bypass -File \".\\啟動語音服務.ps1\"\n\n"
        "# --- 這三行是這個腳本存在的理由，缺一不可 ---\n"
        "$env:NUMBA_DISABLE_JIT = '1'   # 沒有它會【永恆卡死】，而且不報錯\n"
        "$env:PYTHONUTF8        = '1'   # 否則中文說明會讓 argparse 崩在 cp1252\n"
        "$env:PYTHONUNBUFFERED  = '1'   # 否則你看不到任何日誌\n\n"
        "Set-Location \"{root}\"\n\n"
        "Write-Host ''\n"
        "Write-Host '  啟動中……載入成功大約需要 2 分鐘。' -ForegroundColor Yellow\n"
        "Write-Host '  驗收標準：記憶體要漲到 3 GB 以上。' -ForegroundColor Yellow\n"
        "Write-Host '  若記憶體卡在 100-500 MB 且 CPU 一直跑 → 它卡死了。' -ForegroundColor DarkGray\n"
        "Write-Host ''\n\n"
        "python -u api.py `\n"
        "  -s \"{vits}\" `\n"
        "  -g \"{t2s}\" `\n"
        "  -d {dev} -a {host} -p {port} 2>&1 | Tee-Object -FilePath \"{logf}\"\n"
    ).format(root=root, vits=vits, t2s=t2s, dev=dev, host=host, port=port, logf=logf)

    bat = (
        "@echo off\n"
        "chcp 65001 >nul\n"
        "rem tts_doctor 產生：一鍵啟動 GPT-SoVITS 語音服務\n"
        "set NUMBA_DISABLE_JIT=1\n"
        "set PYTHONUTF8=1\n"
        "set PYTHONUNBUFFERED=1\n"
        "cd /d \"{root}\"\n"
        "echo.\n"
        "echo   啟動中……載入成功大約需要 2 分鐘。\n"
        "echo   驗收標準：記憶體要漲到 3 GB 以上。\n"
        "echo   若記憶體卡在 100-500 MB 且 CPU 一直跑 -^> 它卡死了。\n"
        "echo.\n"
        "python -u api.py -s \"{vits}\" -g \"{t2s}\" -d {dev} -a {host} -p {port}\n"
        "pause\n"
    ).format(root=root, vits=vits, t2s=t2s, dev=dev, host=host, port=port)

    p1 = os.path.join(outdir, "啟動語音服務.ps1")
    p2 = os.path.join(outdir, "啟動語音服務.bat")
    with open(p1, "w", encoding="utf-8-sig") as f:
        f.write(ps1)
    with open(p2, "w", encoding="utf-8") as f:
        f.write(bat)
    say(OK, "已產生 PowerShell 啟動腳本", p1)
    say(OK, "已產生批次檔（雙擊可用）", p2)
    say(INFO, "環境變數已寫死在腳本裡，以後不用每次手打。")
    return p2


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="GPT-SoVITS / CosyVoice 環境醫生")
    ap.add_argument("--root", help="GPT-SoVITS 安裝目錄")
    ap.add_argument("--fix", action="store_true", help="套用可安全自動化的修復")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    ap.add_argument("--port", type=int, default=9880)
    ap.add_argument("--make-launcher", action="store_true",
                    help="產生一鍵啟動腳本（環境變數寫死在裡面）")
    ap.add_argument("--out", help="啟動腳本輸出目錄（預設與安裝目錄相同）")
    ap.add_argument("--pth", help="SoVITS 權重路徑（覆寫 yaml）")
    ap.add_argument("--ckpt", help="GPT 權重路徑（覆寫 yaml）")
    ap.add_argument("--device", choices=["cpu", "cuda"], help="推論裝置（覆寫 yaml）")
    args = ap.parse_args()

    if not args.json:
        print("\n" + "#" * 68)
        print("#  tts_doctor —— GPT-SoVITS / CosyVoice 環境醫生")
        print("#  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        print("#" * 68)

    root = find_root(args.root) or args.root

    check_python()
    check_packages()
    check_numba_librosa()
    check_nltk()
    check_jieba_fast(fix=args.fix)
    r = check_models(root)
    check_yaml(r or root)
    check_launch(r or root)
    check_port(args.port)

    if args.make_launcher:
        make_launcher(r or root, outdir=args.out, port=args.port,
                      pth=args.pth, ckpt=args.ckpt, device=args.device)

    bad = [x for x in RESULTS if x["level"] == BAD]
    warn = [x for x in RESULTS if x["level"] == WARN]

    if not args.json:
        hr("結論")
        if not bad:
            print("  [ v ] 沒有發現致命問題。可以啟動了。\n")
            print("  啟動範例（PowerShell）：")
            print("    $env:NUMBA_DISABLE_JIT='1'; $env:PYTHONUTF8='1'; "
                  "$env:PYTHONUNBUFFERED='1'")
            print("    python -u api.py -s \"<你的>.pth\" -g \"<你的>.ckpt\" "
                  "-d cpu -a 127.0.0.1 -p 9880")
        else:
            print("  [ X ] 發現 %d 個致命問題、%d 個警告：\n" % (len(bad), len(warn)))
            for i, x in enumerate(bad, 1):
                print("  %d. %s" % (i, x["title"]))
                if x["fix"]:
                    print("     -> %s" % x["fix"])
        print("\n  記住那個最常見的死因：")
        print("    什麼都不輸出 + CPU 跑滿 + 記憶體不漲 + 埠不開")
        print("    = NUMBA_DISABLE_JIT=1\n")

    if args.json:
        print(json.dumps({"root": root, "results": RESULTS,
                          "fatal": len(bad), "warnings": len(warn)},
                         ensure_ascii=False, indent=2))

    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中止。")
        sys.exit(130)
