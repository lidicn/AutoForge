#!/usr/bin/env python3
"""台账锚点对账器：把「源码加行导致的手工段行号漂移」从人工活变成一条命令。

为什么要有它（2026-10-29 实证）：一天之内因为给 af_service/af_mcp/af_draft 等文件
加行，`docs/可观测性清单.md` §三 与 `docs/进程模型清单.md` §三 的手工行号漂了 4 轮，
每轮都要手抄门报的现读行号去改——手工活会漏、会改反（就出过一次"同码两站互相抢一行"
的震荡）。台账同批纪律不能靠"作者记得跑 --write 之后再手改 N 行"。

它做什么：
1. 跑目标门（默认两本都跑）拿到它的**现读**输出；
2. 解析「具名码站点/生命周期站点 `path:new_line`（`CODE`」= 现读真值，
   与「登记里的 `path:old_line` 现在读不出」= 过期行；
3. 按 `code=` 配对，把过期行号改成现读行号（同码多站：按现读顺序对位，
   已占用的行号不重复分配——上一版震荡就震在这里）；
4. 循环到门绿；**改不动的**（如"同一行已不是同一件事"＝码被改名/站点删除）
   列出来交给人判断，⛔ 不猜、不自造登记。

真值源永远是门自己的现读（AST），不是本脚本的二次实现。

用法：
    PYTHONPATH=src python scripts/sync_ledger_anchors.py            # 两本
    PYTHONPATH=src python scripts/sync_ledger_anchors.py observability
    PYTHONPATH=src python scripts/sync_ledger_anchors.py process_model
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TARGETS = {
    "observability": {
        "gate": "scripts/check_observability.py",
        "ledger": "docs/可观测性清单.md",
        "site": re.compile(
            r"具名码站点 `((?:src|scripts)/[^`:]+):(\d+)`（`([A-Z][A-Z0-9_]{4,})`"),
        "stale": re.compile(
            r"登记里的 `((?:src|scripts)/[^`:]+):(\d+)` 现在读不出(?:具名码|生命周期)站点"),
        "code_in_row": re.compile(r"code=([A-Z][A-Z0-9_]{4,})"),
    },
    "process_model": {
        "gate": "scripts/check_process_model.py",
        "ledger": "docs/进程模型清单.md",
        "site": re.compile(
            r"生命周期站点 `((?:src|scripts)/[^`:]+):(\d+)`（`([^`]+)`"),
        "stale": re.compile(
            r"登记里的 `((?:src|scripts)/[^`:]+):(\d+)` 现在读不出生命周期站点"),
        # §三 生命周期行的识别：同路径同名的 `path:line` 引用（该账本没有 code= 字段）
        "code_in_row": re.compile(r"`((?:src|scripts)/[^`:]+):\d+`"),
    },
}

MAX_ROUNDS = 8


def run_gate(gate: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    r = subprocess.run([sys.executable, str(ROOT / gate)], capture_output=True,
                       text=True, cwd=ROOT, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def align(name: str) -> bool:
    cfg = TARGETS[name]
    ledger = ROOT / cfg["ledger"]
    for attempt in range(1, MAX_ROUNDS + 1):
        rc, out = run_gate(cfg["gate"])
        if rc == 0:
            print(f"[{name}] 第 {attempt} 轮：门绿 ✅")
            return True
        sites = cfg["site"].findall(out)          # [(path, line, code)]
        stale = cfg["stale"].findall(out)         # [(path, old_line)]
        if not sites or not stale:
            print(f"[{name}] 第 {attempt} 轮：门红但无行号类可自动修正项（剩其它腿）：")
            for ln in [l for l in out.splitlines() if "· " in l][:6]:
                print("   ", ln.strip()[:120])
            return False

        b = ledger.read_bytes()
        crlf = b.count(b"\r\n") > 0 and b.count(b"\r\n") == b.count(b"\n")
        lines = b.decode("utf-8").replace("\r\n", "\n").split("\n")

        # 现读站点按码分组；同码多站按出现顺序排队，避免互相抢同一行
        queue: dict[str, list[int]] = {}
        for path, line, code in sites:
            queue.setdefault(f"{path}::{code}", []).append(int(line))

        n = 0
        for path, old in stale:
            for i, line in enumerate(lines):
                if f"`{path}:{old}`" not in line:
                    continue
                if name == "observability":
                    mm = cfg["code_in_row"].search(line)
                    if not mm:
                        continue
                    key = f"{path}::{mm.group(1)}"
                else:
                    # 进程模型账本没有 code= 字段，按文件路径取队列即可（同文件多站点按顺序）
                    key = f"{path}::ANY"
                    for kk in list(queue):
                        if kk.startswith(path + "::"):
                            key = kk
                            break
                avail = queue.get(key) or []
                if not avail:
                    continue
                new = avail.pop(0)
                if str(new) != old:
                    lines[i] = line.replace(f"`{path}:{old}`", f"`{path}:{new}`", 1)
                    n += 1
                break
        print(f"[{name}] 第 {attempt} 轮：改 {n} 处（现读 {len(sites)}、过期 {len(stale)}）")
        if n == 0:
            print(f"[{name}] 无可自动修正项，剩余需人工判断（如码改名/站点删除，见门报）")
            for ln in [l for l in out.splitlines() if "· " in l][:6]:
                print("   ", ln.strip()[:130])
            return False
        text = "\n".join(lines)
        ledger.write_bytes(text.replace("\n", "\r\n").encode("utf-8") if crlf
                           else text.encode("utf-8"))
    print(f"[{name}] {MAX_ROUNDS} 轮未收敛，停（避免震荡），请人工看门报")
    return False


def regen(name: str) -> None:
    """先跑对应门的 --write 重生成自动段，再把 CRLF 复原（write_doc 会写成 LF）。"""
    gate = {"observability": "check_observability", "process_model": "check_process_model"}[name]
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(gate, ROOT / "scripts" / f"{gate}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.write_doc(ROOT)
    led = Path(mod.__dict__.get("LEDGER", ROOT / TARGETS[name]["ledger"])) if hasattr(mod, "LEDGER") \
        else ROOT / TARGETS[name]["ledger"]
    b = led.read_bytes()
    led.write_bytes(b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    print(f"[{name}] 自动段已重生成 + CRLF 复原")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("targets", nargs="*", choices=list(TARGETS), default=None)
    ap.add_argument("--no-regen", action="store_true", help="跳过 --write 重生成，只对齐手工段")
    a = ap.parse_args(argv)
    targets = a.targets or list(TARGETS)   # 不给参数 = 两本都对账（argparse 的 default
    # ⛔ 不能直接给 list：nargs="*" 会把默认列表当一个值去 choices 里校验，当场 TypeError）
    ok = True
    for name in targets:
        if not a.no_regen:
            regen(name)
        ok = align(name) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
