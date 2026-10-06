#!/usr/bin/env python3
"""把 `python -m homesdk.gates` 的一次运行**分类**成三种读数，而不是两种。

为什么要单独立一个分类器：`gates.sh` 原来只拿得到退出码。而依赖门禁崩掉时 Python 也退 **1**
（第十五轮 F16 实测：`scan_file` 对 `'-'×500` 与 `a.b` 重复 500 次两个形状都抛 `RecursionError`，
RC=1，homesdk 0.3.1 与 0.3.2 皆如此）。于是「AST 门判出违规」和「AST 门自己崩了」在门禁里长得
一模一样，下一个读红的人会去做一件错事：把那个文件塞进基线，或者去调 `.gates-tally.txt` 的上限——
**崩掉的门没有产出任何计数，基线无从对齐，而这次操作在门禁上留下的是『它绿了』。**

三档口径：

| 读数 | 判据 | RC |
|---|---|---|
| 崩 / 无从判定 | 输出里出现崩溃签名（与退出码无关，`rc==0` 也判崩） | **2** |
| 真红 | 无崩溃签名且 rc≠0 | 1 |
| 干净 | 无崩溃签名且 rc==0 | 0 |

崩溃签名**只认硬证据**（traceback 头 + 解释器级致命异常），不认 `✗` 之类文案——那是门禁自己的
判红格式，混进来会把真红读成崩。

空输出也判 2：什么都没读到就不是"没违规"，是"没读数"（本仓 fight 的假绿类）。

纯标准库：本机禁 pip install，要装包的门禁等于没有门禁。
"""
from __future__ import annotations

import argparse
import sys

#: 解释器级致命签名。出现即说明门禁**没有走完判定**，产出的是栈不是结论。
CRASH_MARKERS = (
    "Traceback (most recent call last)",
    "RecursionError",
    "MemoryError",
    "Fatal Python error",
)


def classify(text: str, rc: int) -> tuple[int, str]:
    """返回 (本门退出码, 给用户看的那一句)。"""
    if not text.strip():
        return 2, "依赖门禁没有任何输出：读不到判定，不是『没有违规』"
    hits = [m for m in CRASH_MARKERS if m in text]
    if hits:
        return 2, (
            f"依赖门禁崩在半路（签名：{' / '.join(hits)}）——它没产出判定，只产出栈。"
            "本门的计数面因此是空的：不许读成 0，也不许当成『AF 自己的违规』去补基线或调棘轮上限；"
            "缺陷在依赖里（`homesdk/gates/scan.py` 的递归无深度预算，第十五轮 F16），修它要库侧动刀"
        )
    if rc != 0:
        return 1, f"依赖门禁判红（rc={rc}），且输出里没有崩溃签名——这一条是真违规"
    return 0, "依赖门禁干净"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="homesdk 门禁运行结果分类")
    ap.add_argument("--rc", type=int, default=0, help="被分类的那次运行的退出码")
    args = ap.parse_args(argv[1:])
    text = sys.stdin.read()
    code, message = classify(text, args.rc)
    print(f"[门禁分类] RC={code}：{message}")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
