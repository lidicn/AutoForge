#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约漂移门 —— `docs/reference/API_CONTRACT.md` 的「端点表＋枚举」两段对源码现读逐格对撞（v1.18 批次 A）。

为什么要有这道门（PM 设计卡 `AF卡_契约漂移门_v118_设计卡_20261102.md` §一）：API_CONTRACT 在
v1.17.0 之前是手写稿、落后真实 API 约 8.5 倍，根因是「靠人记得更新」；v1.17.0 起契约整节由
`scripts/render_api_contract.py` 生成，但生成器落在仓外时没有任何东西会在契约漂的时候喊一声。
本门把「文档落后于真源」变成机器拦：改 `af_api.py` 增删端点／改权限面／改 docstring 首句，
或动 `af_audit.ALL_EVENT_TYPES`／`af_scanner.CHECKS`，而不重生成契约 ⇒ 这里红。

判据（⛔ 只比两段，⛔ 不比散文）：
  ① 端点表：本门**复用生成器自身的解析函数**（importlib 装载 `render_api_contract.py`，
     ⛔ 不抄第二份解析口径——抄了就是第三真源，生成器改口径门不跟）从源码现读重建
     「(方法, 路径) → (权限面, 说明首句)」，再从仓内契约解析出同一张表，按 (方法, 路径)
     对齐后**逐格比**（权限面、说明都算格；只比端点数＝漏掉权限面/说明漂移，⛔ 禁止）。
  ② 枚举两段：`audit[].type` 与 `diagnostics[].code` 两份名单，源码侧静态现读（同生成器），
     文档侧从 §三 解析，**逐枚相等且顺序敏感**（生成器按真源顺序写；源码重排而文档没跟＝漂）。

⛔ 不比的对象（故意不进判据）：散文段（鉴权模型／三重闸／变更纪律＝人写的判断段）；
「现读坐标」表全部行（发布树版本／生成时线上 OpenAPI 版本／各计数——凡「生成时刻」的读数，
天然随生成与部署变，拿它比＝必假红）。

解析口径三条坑（PM 原型实测结论，照抄别再踩）：
  ① 别取「第一处含标记的行」——文档头部「现读坐标」表与 §二 都含同样字样，必须锚在 §三 段内；
  ② `diagnostics[].level` 与 `diagnostics[].code` 在**同一行**——必须切「`diagnostics[].code`」
     标记之后的尾部，否则会把 level 值（`error`｜`warning`）和真源名一起算进名单；
  ③ 真源文件名（`af_audit.ALL_EVENT_TYPES`／`af_scanner.CHECKS`）也在反引号里——剔除
     `af_` 开头含点号的 token，剔完才是干净的名单。

退出码：0 干净／1 有漂（红因逐格写明）/2 射程塌（真源缺失、契约不在、§二/§三 解析不出、
端点表或任一名单为空、**生成器装载/执行/真源解析抛异常**——**射程塌不许报干净**，
⛔ 崩溃也不许以 exit 1 冒充「红」，AF-736 F2）。

AF-736 F5 口径：本门只能判「**契约没重新生成**」，判不了「**生成器口径本身对不对**」——
文档侧与源码侧都出自同一个生成器 ⇒ ⇒ **⛔ 门绿 ≠ 契约对**；它只保证
『文档与源码现读同源同刻』。⛔ 不许把「门全绿」当成「契约内容正确」的证据。

行尾/空白：仓内契约是 CRLF、生成器输出是 LF ⇒ 文本按 universal newlines 读入并逐格 strip，
⛔ 不许因行尾差异判红。⛔ 本门只判不写：不重生成契约（重生成是 PM 在发布窗口做）、
⛔ 不改源码一行、只用标准库、不打网络、不依赖线上实例。

用法：python scripts/check_contract_drift.py <repo_root>
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

#: 文档里按 (方法, 路径) 认端点行的形状：| `GET` | `/api/graphs` | 权限面 | 说明 |
DOC_ROW_RE = re.compile(r"^\|\s*`([A-Z]+)`\s*\|\s*`([^`]+)`\s*\|")
#: 真源名 token 的形状（坑③）：`af_audit.ALL_EVENT_TYPES` / `af_scanner.CHECKS`
TRUE_SOURCE_TOKEN_RE = re.compile(r"^af_[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")

SECTOR2 = "## 二、"
SECTOR3 = "## 三、"
SECTOR4 = "## 四、"


class Collapse(Exception):
    """射程塌了：解析不出、真源缺失——不许报干净。"""


def _load_generator(repo: pathlib.Path):
    """装载仓内生成器模块（复用其解析函数与排除常量，⛔ 不抄第二份口径）。

    AF-736 F2：装载/执行阶段抛出的**任何**异常（SyntaxError／ImportError／…）
    都必须折成 `Collapse`（exit 2「射程塌」）——⛔ 不许让 traceback 以退出码 1
    跑成 gates.sh 的「红」分支：那一刻一条红因都没有（红因不可读＝没牙），
    运维会被引去查契约漂移，而真相是门自己坏了——崩了报「红」比报「干净」更误导。"""
    gen_p = repo / "scripts" / "render_api_contract.py"
    if not gen_p.exists():
        raise Collapse("生成器不在：%s（本门复用它的解析口径，没有它无从判定）" % gen_p)
    spec = importlib.util.spec_from_file_location("af_render_api_contract", gen_p)
    if spec is None or spec.loader is None:
        raise Collapse("生成器装载不了：%s" % gen_p)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # AF-736 F2：门自己坏了 ⇒ 射程塌，不是「红」
        raise Collapse(
            "生成器装载/执行失败（射程塌）：%s: %s" % (exc.__class__.__name__, exc)
        ) from exc
    if not hasattr(mod, "EXCLUDED_PATHS"):
        raise Collapse(
            "生成器缺少 EXCLUDED_PATHS 常量（AF-736 F1 的导出被摘了？本门"
            "⛔ 不抄第二份排除清单，无此常量即射程塌）"
        )
    return mod


def _rebuild_from_source(repo: pathlib.Path, gen):
    """源码现读重建：端点表 {(method, path): (faces_cell, doc_cell)} ＋ 两份枚举名单。

    AF-736 F2：真源解析阶段抛出的异常（如 af_api.py 语法坏 ⇒ SyntaxError）同样
    折成 `Collapse`（exit 2＋原始异常一行）——⛔ traceback 不许冒充红因。"""
    api_p = repo / "src" / "autoforge" / "af_api.py"
    audit_p = repo / "src" / "autoforge" / "af_audit.py"
    scanner_p = repo / "src" / "autoforge" / "af_scanner.py"
    for p in (api_p, audit_p, scanner_p):
        if not p.exists():
            raise Collapse("缺真源：%s（⛔ 不许用残缺结果下结论）" % p)
    try:
        api_rows = gen.parse_api_source(api_p)
        events, _event_names = gen.parse_all_event_types(audit_p)
        checks = gen.parse_checks(scanner_p)
    except Exception as exc:  # AF-736 F2：射程塌，不是「红」
        raise Collapse(
            "真源解析失败（射程塌）：%s: %s" % (exc.__class__.__name__, exc)
        ) from exc
    if not api_rows:
        raise Collapse("源码现读解析出 0 条端点（af_api.py 口径变了？射程塌，不许报干净）")
    if not events:
        raise Collapse("af_audit.ALL_EVENT_TYPES 现读为空（射程塌，不许报干净）")
    if not checks:
        raise Collapse("af_scanner.CHECKS 现读为空（射程塌，不许报干净）")
    table: dict[tuple[str, str], tuple[str, str]] = {}
    for r in api_rows:
        path = gen.normalize_route(r["path"])
        # AF-736 F1：排除清单唯一真源＝生成器常量（gen.EXCLUDED_PATHS），
        # ⛔ 门里不留第二份字面量（否则生成器改排除集合 ⇒ 假红/假绿）。
        if path in gen.EXCLUDED_PATHS:
            continue
        key = (r["method"], path)
        faces_cell = gen.esc("／".join(r["faces"]))
        # 与生成器渲染行同口径：doc 首句截断已在 parse_api_source 内做完；
        # 离线重建没有 OpenAPI description 可回退，空 docstring 落占位格。
        doc_val = (r["doc"] or "").strip() or "—(端点无 docstring)"
        table[key] = (faces_cell, gen.esc(doc_val))
    if not table:
        raise Collapse("源码端点表重建后为空（全被排除路径吃掉？射程塌，不许报干净）")
    return table, list(events), list(checks)


def _section(text: str, start_marker: str, end_markers: tuple[str, ...]) -> str:
    """取 §start 到下一个 §heading 之间的文本（锚定段内，坑①）。"""
    i = text.find(start_marker)
    if i < 0:
        raise Collapse("契约文档里找不到「%s」段（排版变了 ⇒ 本门口径要跟着改）" % start_marker)
    body_from = text[i + len(start_marker):]
    end = len(body_from)
    for m in end_markers:
        j = body_from.find(m)
        if 0 <= j < end:
            end = j
    return body_from[:end]


def _split_cells(row: str) -> list[str]:
    """按未转义的 | 切表格行（生成器 esc() 把格内 | 写成 \\|，不能 naive split）。"""
    parts = re.split(r"(?<!\\)\|", row)
    return [p.strip() for p in parts]


def _parse_doc_table(repo: pathlib.Path) -> dict[tuple[str, str], tuple[str, str]]:
    doc_p = repo / "docs" / "reference" / "API_CONTRACT.md"
    if not doc_p.exists():
        raise Collapse("契约文档不在：%s（移走了＝射程塌，不许报干净）" % doc_p)
    text = doc_p.read_text(encoding="utf-8")
    body = _section(text, SECTOR2, (SECTOR3, SECTOR4))
    table: dict[tuple[str, str], tuple[str, str]] = {}
    for raw in body.splitlines():
        line = raw.rstrip("\r").strip()
        if not line.startswith("|"):
            continue
        m = DOC_ROW_RE.match(line)
        if not m:
            continue  # 表头/分隔行/组标题行
        cells = _split_cells(line)
        if len(cells) != 6:
            raise Collapse(
                "契约端点表有一行解析不出 4 格（列数=%d）：%.80s —— 手改了表？⛔ 端点表不许手抄"
                % (len(cells) - 2, line)
            )
        method, path = m.group(1), m.group(2)
        # cells: ['', `GET`, `/api/graphs`, 权限面, 说明, '']
        faces_cell, doc_cell = cells[3], cells[4]
        key = (method, path)
        if key in table:
            raise Collapse("契约端点表重复行：%s %s（⛔ 同键两行无从对齐）" % key)
        table[key] = (faces_cell, doc_cell)
    if not table:
        raise Collapse("契约 §二 端点表解析出 0 行（清空了＝射程塌，不许报干净）")
    return table


def _doc_enum_tokens(line: str, after_marker: str | None) -> list[str]:
    """从 §三 名单行提取反引号 token，剔除真源名（坑③），必要时先切到标记之后（坑②）。"""
    sub = line
    if after_marker is not None:
        idx = sub.find(after_marker)
        if idx < 0:
            raise Collapse("枚举行里找不到标记 %r：%.80s" % (after_marker, line))
        sub = sub[idx:]
    tail = sub.split("：", 1)
    if len(tail) != 2:
        raise Collapse("枚举行没有「：」名单段：%.80s" % line)
    tokens = re.findall(r"`([^`]+)`", tail[1])
    return [t for t in tokens if not TRUE_SOURCE_TOKEN_RE.match(t)]


def _parse_doc_enums(repo: pathlib.Path) -> tuple[list[str], list[str]]:
    doc_p = repo / "docs" / "reference" / "API_CONTRACT.md"
    text = doc_p.read_text(encoding="utf-8")
    body = _section(text, SECTOR3, (SECTOR4,))
    audit_line = None
    diag_line = None
    for raw in body.splitlines():
        line = raw.rstrip("\r").strip()
        # 坑①：锚定 §三 段内的名单行，⛔ 不取全文第一处含标记的行
        if line.startswith("- `audit[].type`（"):
            audit_line = line
        elif line.startswith("- `diagnostics[].level`："):
            diag_line = line
    if audit_line is None:
        raise Collapse("§三 里找不到 audit[].type 名单行（排版变了 ⇒ 本门口径要跟着改）")
    if diag_line is None:
        raise Collapse("§三 里找不到 diagnostics 名单行（排版变了 ⇒ 本门口径要跟着改）")
    audit_tokens = _doc_enum_tokens(audit_line, None)
    # 坑②：level 与 code 同行——名单从 `diagnostics[].code` 标记之后切
    diag_tokens = _doc_enum_tokens(diag_line, "`diagnostics[].code`")
    if not audit_tokens:
        raise Collapse("audit[].type 名单解析出 0 枚（射程塌，不许报干净）")
    if not diag_tokens:
        raise Collapse("diagnostics[].code 名单解析出 0 枚（射程塌，不许报干净）")
    return audit_tokens, diag_tokens


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    repo = pathlib.Path(sys.argv[1]).resolve()
    try:
        gen = _load_generator(repo)
        src_table, src_events, src_checks = _rebuild_from_source(repo, gen)
        doc_table = _parse_doc_table(repo)
        doc_events, doc_checks = _parse_doc_enums(repo)
    except Collapse as exc:
        print("契约漂移门：射程塌（exit 2，⛔ 不许在这种状态下报干净）")
        print("  %s" % exc)
        return 2

    problems: list[str] = []

    # ── ① 端点表：按 (方法, 路径) 对齐后逐格比 ──────────────────────────
    src_keys = set(src_table)
    doc_keys = set(doc_table)
    for key in sorted(src_keys - doc_keys, key=lambda k: (k[1], k[0])):
        faces, doc = src_table[key]
        problems.append(
            "端点漂移·现读有文档无：%s %s（权限面=%s／说明=%s）" % (key[0], key[1], faces, doc)
        )
    for key in sorted(doc_keys - src_keys, key=lambda k: (k[1], k[0])):
        problems.append(
            "端点漂移·文档有现读无：%s %s（权限面=%s／说明=%s）"
            % (key[0], key[1], doc_table[key][0], doc_table[key][1])
        )
    for key in sorted(src_keys & doc_keys):
        s_faces, s_doc = src_table[key]
        d_faces, d_doc = doc_table[key]
        if s_faces != d_faces:
            problems.append(
                "权限面漂移：%s %s：文档=%s 现读=%s" % (key[0], key[1], d_faces, s_faces)
            )
        if s_doc != d_doc:
            problems.append(
                "说明漂移：%s %s：文档=%s 现读=%s" % (key[0], key[1], d_doc, s_doc)
            )

    # ── ② 枚举两段：逐枚相等且顺序敏感 ────────────────────────────────
    for name, doc_list, src_list in (
        ("audit[].type", doc_events, src_events),
        ("diagnostics[].code", doc_checks, src_checks),
    ):
        if doc_list == src_list:
            continue
        common = min(len(doc_list), len(src_list))
        for i in range(common):
            if doc_list[i] != src_list[i]:
                problems.append(
                    "枚举漂移：%s 第 %d 枚不等：文档=`%s` 现读=`%s`"
                    % (name, i + 1, doc_list[i], src_list[i])
                )
                break
        if len(doc_list) != len(src_list):
            extra = set(src_list) - set(doc_list)
            missing = set(doc_list) - set(src_list)
            problems.append(
                "枚举漂移：%s 枚数不等：文档 %d 枚 vs 现读 %d 枚（现读多出=%s／文档多出=%s）"
                % (name, len(doc_list), len(src_list),
                   "、".join(sorted(extra)) or "无",
                   "、".join(sorted(missing)) or "无")
            )

    if problems:
        print("契约漂移门：红（exit 1）—— 契约文档落后于源码现读，%d 处：" % len(problems))
        for p in problems:
            print("  %s" % p)
        print("  修法：发布窗口由 PM 用 scripts/render_api_contract.py --offline 重新生成契约；"
              "⛔ 不许手改端点表/枚举名单，⛔ 不许让本门宽容到只比端点数。")
        return 1

    print("契约漂移门：干净。")
    print("  端点表：%d 条 ×（方法,路径,权限面,说明）逐格相等（源码现读 vs 契约 §二）"
          % len(src_table))
    print("  audit[].type：%d 枚逐枚相等（顺序敏感）" % len(src_events))
    print("  diagnostics[].code：%d 枚逐枚相等（顺序敏感）" % len(src_checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
