#!/usr/bin/env python3
"""except:pass 吞异常计数门禁（autoflow 经验闭环照搬，2026-10-23）：

把「宽 except + body 只有 pass」的**严格吞掉型**站点钉成只减不增的棘轮。

起因（docs/audit/参考/调研_autoflow前代复盘_20261023.md）：autoflow 复盘把
「静默失效」列为前代四大死因之一——宽 except 吃掉异常后系统照常运转，坏的那一格
直到事故才被看见。AF 现存量 16 处（首跑读数，2026-10-23），多数是"旁路容错"的
合法形状（遥测失败不拖垮业务回执），但合法存量与**新增**是两件事：新增一枚就是
多一处"坏了自己不说"的面。所以本门不逼存量清零，只把存量冻死、新增判红。

口径（为什么这么窄）：
- **只收** handler 类型宽（裸 `except:` / `Exception` / `BaseException`，含元组里
  混着这两者的）且 body 恰为单条 `pass` 的站点。窄 except（`except KeyError: pass`）
  不收——那是明确说"这种错我知道、我就是要忽略"，不算静默失效的形状。
- `pass` 之外带任何语句（哪怕一句 `logger.debug`）不收：有痕就不算"吞"。
- `...`（Ellipsis）当 body 不收：本仓没有这个形状，收进来只会稀释口径。

四条判据（各自能单独判红）：
- **A 新增判红**：现读站点 - 基线 - 豁免 ⇒ 有剩即红。每一枚都给 `文件:行号`。
- **B 豁免两半对齐**：带 `# except-pass: exempt(理由)` 的行必须真在基线扫描口径里
  （豁免的是被扫到的那一枚），且理由非空。理由写在注释里却扫不到 = 口径各说各话。
- **C 基线只减不增**：现读 < 基线数 ⇒ 提示把基线下调（防"减了不说"让基线虚高）；
  基线里已消失的站点（行号漂了或删了）列出待清理，不判红但必须处理——
  行号漂移会导致"漂走的那枚 + 新位置的同一枚"被数成两枚，虚增计数。
- **D 反空洞**：全树扫出 0 枚严格吞掉型站点 ⇒ 判据失效，按射程问题退 2
  （16 枚存量在册，归零只可能是扫描器坏了，不是代码一夜清完）。

退出码：0=绿，1=有判红，2=射程读不成（src 目录缺失 / 任一 .py 解析不了 / 0 站点）。
`--print-baseline` 只打印当前扫到的全部站点（供一次性冻结与日后下调基线）。

纯标准库：对 `src/autoforge/**/*.py` 做 `ast.parse`，不导入 `autoforge`，不碰网络，
不写盘（`--print-baseline` 除外）。`scripts/`／`tests/` 不在射程内——它们不是常驻
运行时的组成部分（与 check_bounded_caches 同口径）。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPO_SCOPE = REPO / "src" / "autoforge"

EXEMPT_MARKER = "# except-pass: exempt("

#: 宽 handler 类型名（裸 except 在 AST 里是 type=None）
BROAD_NAMES = {"Exception", "BaseException"}

#: 冻结基线（2026-10-23 首跑读数，17 枚——含 af_closedloop/deepfix.py:52，首轮摸底漏数它
#: 是因为扫描跑在主 checkout 上；本基线按 worktree 现读冻结）。只减不增：减了要主动下调
#: 这一份，增了判据 A 红。行号漂移后请用 --print-baseline 重新冻结并核对是同一批站点。
BASELINE: frozenset[str] = frozenset(
    {
        # 2026-10-24 AST 门禁收账重冻结：af_api.py 两处变化——1209 是修掉的那枚
        # （裸 except+pass 改具名捕获 UPDATE_PREVIEW_READ_FAILED，本门 A 判据的正向收账）；
        # 1334 是行漂 +6 → 1340（pair SSE 心跳的 `except Exception: pass`，同一批站点，
        # api_system_version 补留痕涨了 6 行）。17 → 16 枚：净 -1 全是修掉的真缺陷。
        # 2026-10-24 傍晚再冻一次：1340 → 1345（AF-001 inbox 新鲜窗口那笔 05dc1f2 在它
        # 上方加了 5 行，**同一枚站点**，不是新增）。16 → 16 枚：本期净变化 0。
        # 2026-10-26 第四批批 0：1345 → 1384（D1-D4 在它上方加了 ~39 行：auth-code 收 owner 面、
        # login fail-closed、import/tags/enable 透传 caller）。仍是 pair SSE 心跳那枚
        # `except Exception: pass`，**同一枚站点**，不是新增。16 → 16 枚。
        # 2026-10-26 批 2：1384 → 1388（登录/注册两处签发点补 principal_kind="login" 加了 4 行）。
        # 仍是同一枚 pair SSE 心跳吞掉站点，不是新增。16 → 16 枚。
        # 2026-10-26 返工 5.1：1388 → 1443（批 5 在它上方加了 pair-link 端点 + 失败计数落盘
        # 约 55 行，把这枚顶漂；except_pass 门报 A"新增 1449"+C"基线 1388 读不到"是同一枚的
        # 两面，净数仍 16=16，未破"只减不增"）。16 → 16 枚。
        # （DB-08 曾把这枚重冻结到 1445，注释当时没续——本行起补上，链不再断。）
        # 2026-10-27 返工 5.4：1445 → 1473（SPA 兜底修复在它上方加了 _ASSET_EXT_RE/两个
        # 辅助函数（含 _NO_STORE_ASSETS 常量）+ handler 内 404/no-cache 分支。中途一度冻结到
        # 1467，最后补 index.html 直接请求的 no-cache 又 +6 行 → 终值 1473）。同一枚 pair SSE
        # 心跳吞掉站点，不是新增；门报 A"新增"+C"基线读不到"仍是同一枚两面。净 16 → 16。
        "af_api.py:1473",
        "af_closedloop/deepfix.py:52",
        "af_conflict.py:680",
        "af_conflict_audit.py:150",
        "af_conflict_runtime.py:269",
        "af_conflict_runtime.py:279",
        "af_expect.py:141",
        "af_health.py:702",
        "af_orchestrator.py:889",
        "af_orchestrator.py:1092",
        "af_runtime_plugins.py:262",
        "af_time.py:202",
        "af_version.py:440",
        "af_version.py:447",
        "af_version.py:452",
        "af_version.py:465",
    }
)


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """handler 类型是否"宽"：裸 / Exception / BaseException（含元组混入）。"""
    t = handler.type
    if t is None:
        return True
    if isinstance(t, ast.Name):
        return t.id in BROAD_NAMES
    if isinstance(t, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in BROAD_NAMES for e in t.elts)
    return False


def _is_bare_pass(handler: ast.ExceptHandler) -> bool:
    """body 是否恰为单条 `pass`（一句日志都算"有痕"，不收）。"""
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)


def scan(src_root: Path) -> tuple[set[str], list[str]]:
    """返回 `(站点集合 "相对路径:行号", 射程自证问题)`。

    键用**相对 src 根的路径**（`af_closedloop/deepfix.py:52`）：只用文件名会把子目录
    同名文件混进同一枚键（af_vhass/ 子包就是现成反例），基线对不上就成假红。
    """
    found: set[str] = set()
    errors: list[str] = []
    paths = sorted(src_root.rglob("*.py"))
    if not paths:
        return found, [f"src 下一个 .py 都没有：{src_root}"]
    for path in paths:
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(src_root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{rel} 解析不了：{exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_broad(node) and _is_bare_pass(node):
                found.add(f"{rel}:{node.lineno}")
    return found, errors


def _exempt_markers(src_root: Path) -> dict[str, str]:
    """`{"相对路径:行号": 行文本}`：写了豁免标记的行。本脚本自身排除（文档字符串引用标记语法）。"""
    hits: dict[str, str] = {}
    for path in sorted(src_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name == "check_except_pass.py":
            continue
        rel = path.relative_to(src_root).as_posix()
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if EXEMPT_MARKER in line:
                hits[f"{rel}:{i}"] = line
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", nargs="?", default=str(REPO_SCOPE), type=Path)
    ap.add_argument("--print-baseline", action="store_true", help="只打印扫到的站点（冻结/下调基线用）")
    args = ap.parse_args(argv)

    src = Path(args.src).resolve()
    if not src.is_dir():
        print(f"射程读不成：src 目录不存在 {src}")
        return 2

    try:
        found, errors = scan(src)
    except Exception as exc:
        print(f"[射程] 扫描器读不下去：{type(exc).__name__}: {exc}")
        return 2
    if errors:
        for e in errors:
            print(f"[射程] {e}")
        return 2
    if not found:
        print("[射程] 全树扫出 0 枚严格吞掉型站点：基线在册 16 枚，归零只可能是扫描器坏了（判据 D）")
        return 2

    if args.print_baseline:
        for key in sorted(found):
            print(key)
        print(f"# 共 {len(found)} 枚")
        return 0

    baseline = set(BASELINE) if src == REPO_SCOPE else frozenset()
    markers = _exempt_markers(src)
    findings: list[str] = []

    # 判据 A：新增判红（豁免行上的站点不算）
    new_sites = sorted(s for s in found - baseline if s not in markers)
    for s in new_sites:
        findings.append(f"A 新增严格吞掉型站点 {s}——要么收窄 handler/补留痕（logger/回执），"
                        f"要么就地写 `{EXEMPT_MARKER}理由)` 并说明为什么吞是对的")
    if new_sites:
        findings.append(f"A 共 {len(new_sites)} 枚新增（基线 {len(baseline)} 枚 / 现读 {len(found)} 枚）")

    # 判据 B：豁免两半对齐——豁免行必须真扫到那一枚站点（写在那里却不在扫描口径 = 第二份口径）
    for loc, text in sorted(markers.items()):
        if loc not in found:
            findings.append(f"B 豁免标记写在 {loc}，但该行不在扫描口径里（行号漂了或那不是宽 except+pass）"
                            f"——豁免的是扫描结果，不是任意一行注释")
        if not text.split(EXEMPT_MARKER, 1)[1].rstrip(")\n \t").strip():
            findings.append(f"B {loc} 的豁免理由为空——理由为空的豁免就是无豁免")

    # 判据 C：基线只减不增 + 过期基线待清理
    gone = sorted(baseline - found)
    if gone:
        findings.append(f"C 基线里 {len(gone)} 枚已不在现读（行号漂移或已删）：{', '.join(gone)}——"
                        f"行号漂移会把同一枚数成两枚，请用 --print-baseline 重冻结；"
                        f"真删了就把它们从 BASELINE 拿掉（只减不增）")
    if baseline and len(found) < len(baseline) and not gone:
        findings.append(f"C 现读 {len(found)} < 基线 {len(baseline)}：请把 BASELINE 下调到现读值（只减不增）")

    if findings:
        print(f"✗ except:pass 门禁发现 {len(findings)} 处：")
        for f in findings:
            print(f"  · {f}")
        print("修法：A 收窄 handler 类型或补留痕（吞而不说正是 autoflow「静默失效」死因）；"
              "B 豁免要写在被扫到的那一行且理由非空；C 用 --print-baseline 重冻结基线（先核对是同一批站点）。")
        return 1

    print(f"✓ except:pass 门禁干净（现读 {len(found)} 枚严格吞掉型站点 = 基线 {len(baseline)} 枚；"
          f"豁免 {len(markers)} 枚两半对齐；取数面＝磁盘现读（基线随行号漂移重冻结））")
    return 0


if __name__ == "__main__":
    sys.exit(main())
