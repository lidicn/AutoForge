#!/usr/bin/env python
"""令牌泄漏门禁（G24，2026-10-31）：扫**入库面**，命中即 RC=1。

为什么扫入库面（`git grep HEAD`）而不是磁盘：磁盘上的未跟踪文件（如
`issued_tokens_clean.json`）不在射程内——泄漏事故的形状是「进了 commit」，
本地临时件归 .gitignore 与人眼管（裁定同口径：取数面＝HEAD 树）。

命中形状只有两族（⛔ 宽松正则会把长 entity_id 误报成令牌——AF-211 的教训，
`light.mijia_cn…`／`sensor.duka_cn…` 不是泄漏）：
- `af_` + 40 位 hex（AutoForge 访问令牌真前缀）；
- `Bearer` 后接 ≥32 位高熵串（字符集 [A-Za-z0-9._~-]，去重字符 ≥10）。

豁免标记：命中行内写 `secret-scan: exempt(理由)`，理由非空才豁免；
`exempt()`／`exempt(  )` 理由为空 ⇒ 仍红（无理由的豁免等于没有豁免）。

输出纪律：只报 文件 + 行号 + 形状名，⛔ 绝不打印命中的值本身（门禁输出会进
验收单/issue，打印值等于把泄漏再抄一遍）。

退出码：0 干净 / 1 有命中 / 2 射程读不出（不是 git 仓、git 不可用）。
"""

from __future__ import annotations

import re
import subprocess
import sys

# 形状定义：(形状名, 正则)。注意：⛔ 不加 `light\.|sensor\.` 之类实体域前缀白名单——
# 那是在替 HA 枚举域，新增域就静默漏报；命中条件本身（af_ 前缀 / Bearer 引导）已经
# 把 entity_id 族天然排除在外。
SHAPES: list[tuple[str, re.Pattern[str]]] = [
    ("af_hex40", re.compile(r"af_[0-9a-f]{40}")),
    ("bearer_long", re.compile(r"Bearer[ \t]+([A-Za-z0-9._~-]{32,})")),
]

#: 豁免标记：理由非空才成立。理由为空的豁免（exempt() / 全空白）仍在红名单里。
_EXEMPT_OK = re.compile(r"secret-scan:\s*exempt\(\s*\S[^)]*\)")
_EXEMPT_EMPTY = re.compile(r"secret-scan:\s*exempt\(\s*\)")

#: 高熵下限：命中串去重后的不同字符数。纯重复串（"aaaa…"）不是令牌。
_MIN_UNIQUE_CHARS = 10


def _entropy_ok(token: str) -> bool:
    return len(set(token)) >= _MIN_UNIQUE_CHARS


def _split_grep_line(raw: str) -> tuple[str, str, str] | None:
    """把 `HEAD:<path>:<line>:<content>`（路径可能带引号+八进制转义）拆成三段。

    返回 (path, lineno, content)；拆不动返回 None（按射程异常上报，不静默丢）。
    """
    body = raw
    if body.startswith("HEAD:"):
        body = body[len("HEAD:"):]
    if body.startswith('"'):  # 带特殊字符的路径被 git 引号包裹（八进制转义，不含裸冒号）
        end = body.find('"', 1)
        if end < 0:
            return None
        path, rest = body[1:end], body[end + 1:]
        if rest.startswith(":"):  # 引号路径后跟 `":lineno:content`，剥掉第一个冒号
            rest = rest[1:]
    else:
        cut = body.find(":")
        if cut < 0:
            return None
        path, rest = body[:cut], body[cut + 1:]
    cut = rest.find(":")
    if cut <= 0:
        return None
    lineno, content = rest[:cut], rest[cut + 1:]
    if not lineno.isdigit():
        return None
    return path, lineno, content


def main(repo: str) -> int:
    hits: list[tuple[str, str, str]] = []  # (path, lineno, shape)
    empty_exempt: list[tuple[str, str, str]] = []
    try:
        for shape_name, pattern in SHAPES:
            proc = subprocess.run(
                ["git", "grep", "-n", "-I", "-E", pattern.pattern, "HEAD"],
                cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode not in (0, 1):  # 0=有命中 1=无命中；>1=git 自身报错
                print(f"check_secrets: git grep 失败（rc={proc.returncode}）：{proc.stderr.strip()[:200]}")
                return 2
            for raw in proc.stdout.splitlines():
                if not raw.strip():
                    continue
                parsed = _split_grep_line(raw)
                if parsed is None:
                    print(f"check_secrets: git grep 输出形状读不出，按射程异常处理：{raw.split(':')[0]}…")
                    return 2
                path, lineno, content = parsed
                if pattern.pattern.startswith("Bearer"):
                    m = pattern.search(content)
                    if m and not _entropy_ok(m.group(1)):
                        continue  # 低熵长串（如占位 "xxx…"）不是令牌，不报
                if _EXEMPT_OK.search(content):
                    continue  # 带非空理由的豁免
                if _EXEMPT_EMPTY.search(content):
                    empty_exempt.append((path, lineno, shape_name))
                    continue
                hits.append((path, lineno, shape_name))
    except FileNotFoundError:
        print("check_secrets: 找不到 git 可执行文件")
        return 2
    except OSError as exc:
        print(f"check_secrets: 射程读不出（{exc}）")
        return 2

    if hits or empty_exempt:
        for path, lineno, shape in hits:
            print(f"✗ {path}:{lineno} 形状={shape}（值不打印；需豁免在该行加 secret-scan: exempt(理由)）")
        for path, lineno, shape in empty_exempt:
            print(f"✗ {path}:{lineno} 形状={shape} 豁免理由为空（exempt() 不算豁免，写明理由）")
        print(f"令牌泄漏门禁红：命中 {len(hits)} 处 + 空理由豁免 {len(empty_exempt)} 处")
        return 1
    print("✓ 令牌泄漏门禁干净（入库面无 Bearer 长串 / af_ 40hex 命中）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
