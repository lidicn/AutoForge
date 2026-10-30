"""`scripts/check_observability.py` 的判据腿：真仓读数钉死 ＋ 合成树上每条规则单独可红。

真仓那批腿钉的是**读数本身**（站点数／级别分布／具名码数／无码存量、trace_id 信封、HTTP 请求侧、
`af_api.py` 的 logger 与 `ok: False` 形状、访问日志掩码三个锚点）；具体数字只写在腿里，
**不抄进本 docstring**——抄了就是第二份会过期的副本（上一批正是这样红了三门）。
合成树那批钉的是**规则形状**——B 十一种红法、C 一种、A 一种、D 四种、E 五种，另加射程塌的十一枚 `exit 2`。

合成树的两条纪律（上一批同一形状栽过，这里预先挡住）：
① 站点行号由门自己的扫描结果生成，不写死——写死就会在下次改文件时红在"登记过期"而不是红在真问题上；
② 注入只用"末尾追加"与"单行替换"两种形状，保证不移动既有站点行号。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE_PATH = REPO / "scripts" / "check_observability.py"


def _load_gate(path: Path):
    spec = importlib.util.spec_from_file_location(f"obs_gate_{path.parent.name}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate(GATE_PATH)

# --------------------------------------------------------------------------------------
# 真仓：读数本身
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    files = gate.py_files(REPO)
    assert files, "射程塌：src/autoforge 读不出任何 .py"
    trees, errs = gate.parse_all(files)
    assert not errs, f"真仓有文件解析不了：{errs[0]}"
    sites, outside = gate.scan_sites(trees, REPO)
    return {
        "trees": trees,
        "sites": sites,
        "outside": outside,
        "coded": [s for s in sites if s["code"]],
        "env": gate.scan_envelope_ids(trees, REPO),
        "req": gate.scan_request_ids(trees, REPO),
        "http": gate.scan_http_face(trees, REPO),
    }


def test_real_repo_site_readings_are_pinned(real):
    """现读站点数／文件数／级别分布六档全点。数字漂了要么连同清单一起改，要么这一腿先响。

    155 → 156、error 20 → 21（裁定 20261011《八问》§3 Q2 乙）：`Scheduler._run_delayed` 新增一枚
    带码留痕 `SCHED_DELAYED_CALLBACK_FAILED`。文件数不动（`af_scheduler.py` 本来就在射程里），
    无码存量不动（这一枚带码，不吃 §四 的格子）。

    156 → 168（执行记录 §二之一百二十六）拆两半记账，⛔ 不混成一句"漂了 12"：
    ① 156 → 160 是 **HEAD 存量**（先前批次留下的过期钉，本批不认领那 +4；那四枚是无码 WARNING，
       与 §四 C 判据现读的同一批数对齐）；文件数 30 → 31 也是那一半留下的；
    ② 160 → 168 是本批 +8：`_reap_stale_watch_scratch`／`_drop_watch_scratch`／`_gate_watch_log`
       七枚 WARNING ＋ `WATCH_SCRATCH_REAPED` 一枚 INFO，全部带码 ⇒ 不吃 §四 的无码格子。
    168 → 169（执行记录 §二之一百二十九）：`af_service.health()` 那格探测失败新增一枚带码 DEBUG
       留痕 `PREMIERE_HEALTH_PROBE_SKIPPED`（裁定 20261011 §3 的只读读数接进健康面）。带码 ⇒ 不吃
       §四 的无码格子；文件数仍 31（`af_service.py` 本来就在射程里）。
    198 → 199（20261021 收账，8664511 的 ARCH-01 补留痕）：`af_service._catalog` 那格裸吞
       补成带码 DEBUG `CATALOG_TOKEN_SOURCE_SKIPPED`。同因 DEBUG +1。
    199 → 200（v1.13.3 autoflow 经验闭环收尾）：`af_mcp._t_draft` 失败归因旁路新增
       带码 DEBUG `DRAFT_FAILURE_RECORD_SKIPPED`。同因 DEBUG +1；文件数 32 不动
       （`af_mcp.py` 本来就在射程里）。
    200 → 201（2026-10-24 AST 门禁收账）：`af_api.api_system_version` 的裸 except+pass
       改具名捕获 + WARNING `UPDATE_PREVIEW_READ_FAILED`。同因 WARNING +1；文件数不动
       （`af_api.py` 本来就在射程里，带码 ⇒ 不吃 §四 的无码格子）。
    201 → 203（2026-10-24 AF-001 inbox 重放面收口）：`af_live.read_answer_inbox` 新增两枚
       带码 WARNING `INBOX_REJECT_NO_TS`／`INBOX_REJECT_STALE`——同因同形状，+2 站 +2 码；
       文件数 32 不动（`af_live.py` 本来就在射程里）。
       ⚠️ 这两枚 1.13.4 发版时只更了 `docs/可观测性清单.md` 台账（门禁绿），**漏了本文件的 pin**，
       全量单测 3 failed 才发现——教训：台账 regen 与 pin 是两套，都得同批。
    203 → 204（2026-10-26 返工 5.1③）：`af_api._pair_record_failure` 的裸 `except Exception: pass`
       收窄为 `except OSError` + 带码 WARNING `PAIR_LINK_FAILURE_COUNT_WRITE_FAILED`——+1 站 +1 码
       （warning 137→138）。**同因又漏了本 pin**，gates 绿而全量 4 failed，这笔补齐。
    """
    from collections import Counter

    lv = Counter(s["level"] for s in real["sites"])
    # 204 → 205（2026-10-30 v1.14.21 归属收口批）：`list_quarantined` 隔离区清单
    # 新增 1 枚带码站点（warning 138→139）。AF-507 两轮未动的账，这笔随 v1.14.22 补齐。
    # 205 → 208（2026-10-30 v1.14.23 DCD 旁门批）：记账失败告警三枚带码 WARNING
    # （LIVE_RUN_AUDIT_RECORD_FAILED / UNDO_AUDIT_RECORD_FAILED / WATCH_START_AUDIT_RECORD_FAILED，
    # warning 139→142）。⚠️ 又是"台账 regen 与 pin 两套"的坑——上一笔只更了本 pin 的 sites 格，
    # coded 格漏更，CI 红。教训再记一次：新增留痕站点，本文件**三枚 pin 一起查**。
    assert len(real["sites"]) == 208
    assert len({s["path"] for s in real["sites"]}) == 32
    assert dict(lv) == {"debug": 22, "info": 9, "warning": 142, "error": 24, "exception": 11}
    assert lv["critical"] == 0, "CRITICAL 现读 0 是基线；它变了要连同 §1.6 的口径一起谈"
    assert real["outside"] == [], f"长出第二种 logger 获取形状：{real['outside'][:3]}"


def test_real_repo_named_code_readings_are_pinned(real):
    """具名码枚数／站点数与无码存量——§四 那六行上限就是照无码那组数钉的。

    带码站点 30 → 31、具名码 28 → 29（裁定 20261011《八问》§3 Q2 乙新增 `SCHED_DELAYED_CALLBACK_FAILED`）。
    无码存量 125 与它的三档分布不动——§四 的上限是照无码那一组钉的，新站带码 ⇒ 不吃那一格。

    31 → 40 站／29 → 37 枚（执行记录 §二之一百二十六）：本批新增 9 枚带码站点、8 枚新码。
    多出来的那一站是 `WATCH_SCRATCH_DELETE_FAILED` 的两站形状（见下面白名单）。
    无码存量 125 → 128 拆两半：HEAD 存量 +4（那批无码 WARNING，本批不认领）− 本批 1
    （`af_config.py:134` 那枚原本不带码的凭据 ACL 留痕补上 `CRED_FILE_ACL_NOT_RESTRICTED`）＝ 128。
    ⚠️ §四 的 WARNING 上限 83 一字未动，门在 C 判据上仍红（现读 86）——本批只是把"现读"钉回真值，
    没有把欠账读成绿，也没有替存量抬上限。

    40 → 41 站／37 → 38 枚（执行记录 §二之一百二十九）：本批 +1 站 +1 码，同一枚
    `PREMIERE_HEALTH_PROBE_SKIPPED`（`af_service.py:295`，DEBUG 带码）。⛔ 无码存量与 §四 上限同样不动。

    41 → 42 站／38 → 39 枚（20261021 收账，8664511 的 ARCH-01 补留痕）：`af_service._catalog` 那格
    裸吞补成带码 DEBUG `CATALOG_TOKEN_SOURCE_SKIPPED`——同因同形状，+1 站 +1 码。
    ⛔ 无码存量 114 与 §四 上限 83 同样一字未动（带码 ⇒ 不吃那一格）；白名单三例也没动。

    42 → 43 站／39 → 40 枚（v1.13.3 autoflow 经验闭环收尾）：`af_mcp._t_draft` 失败归因旁路
    新增带码 DEBUG `DRAFT_FAILURE_RECORD_SKIPPED`——同因同形状，+1 站 +1 码。
    ⛔ 无码存量与 §四 上限同样一字未动；白名单三例没动。

    43 → 44 站／40 → 41 枚（2026-10-24 AST 门禁收账）：`af_api.api_system_version` 的裸
    except+pass 改具名捕获 + WARNING `UPDATE_PREVIEW_READ_FAILED`——同因同形状，+1 站 +1 码。
    ⛔ 无码存量与 §四 上限同样一字未动；白名单三例没动。

    44 → 46 站／41 → 43 枚（2026-10-24 AF-001 inbox 重放面收口）：`af_live.read_answer_inbox`
    新增两枚带码 WARNING `INBOX_REJECT_NO_TS`／`INBOX_REJECT_STALE`——同因同形状，+2 站 +2 码。
    ⛔ 无码存量与 §四 上限同样一字未动；白名单三例没动。
    ⚠️ 1.13.4 发版时只更了台账漏了本 pin，全量单测 3 failed 才发现（见上一格的同条教训）。
    """
    from collections import Counter

    coded = real["coded"]
    # 89→90 站／86→87 枚：返工 5.1③ `PAIR_LINK_FAILURE_COUNT_WRITE_FAILED`（af_api），出处链见上两条腿
    # 90→91 站／87→88 枚（2026-10-30 v1.14.21）：`af_service.list_quarantined` 隔离区归属
    # 读不出的降级留痕（AF-501 批），同因同形状，+1 站 +1 码。
    # 91→94 站／88→91 枚（2026-10-30 v1.14.23 DCD 旁门批）：审计流水三事件
    # LIVE_RUN_EXECUTED / UNDO_DEPLOYED / WATCH_STARTED + 三枚记账失败告警（同批，
    # AF-403 后半 authorized_by 落流水），+3 站 +3 码。
    assert len(coded) == 94
    assert len({s["code"] for s in coded}) == 91
    # 41 站／38 枚＝仓内三例"一码两站"，各自的原因不同，所以钉成白名单而不是放宽：
    # ① `DEPLOY_AUDIT_PERSIST_FAILED` 同时出自「这一条没写进盘」与「盘上那份读不回来，裁剪跳过」
    #    两条腿——同一句承诺（台账没落稳）；
    # ② `OWNER_ALERT_PUBLISH_FAILED` 出自「投递抛出」与「投递返回未被接受（code=…）」两条腿——
    #    同一句承诺（这条告警没送到收件箱），裁定 20261011 §3 Q8.1。
    # ③ `WATCH_SCRATCH_DELETE_FAILED` 出自「清扫器逐个删旧目录」与「start_watch 收尾删本次 scratch」
    #    两条腿——同一句承诺（那个一次性目录没删掉，磁盘卫生没做成），执行记录 §二之一百二十六。
    # 免得下一个人把这几条例外读成"码可以随便复用"。
    dup = {code for code, n in Counter(s["code"] for s in coded).items() if n > 1}
    assert dup == {
        "DEPLOY_AUDIT_PERSIST_FAILED",
        "OWNER_ALERT_PUBLISH_FAILED",
        "WATCH_SCRATCH_DELETE_FAILED",
    }, f"重码名单漂了：{dup}"
    nc = Counter(s["level"] for s in real["sites"] if not s["code"])
    assert sum(nc.values()) == 114
    assert nc["warning"] == 72 and nc["exception"] == 11 and nc["info"] == 7


Q83_CODES = ("DEVICE_GUARD_CATALOG_INJECT_FAILED", "DEVICE_ACL_LOAD_FAILED")


def test_real_repo_q83_security_degradations_are_error(real):
    """裁定 20261011《十三问》§3 Q8.3：两枚安全降级留痕**代码面与 §三 登记面都得是 ERROR**。

    只钉代码面会漏掉"码改了、清单没改"；只钉清单面会漏掉"清单改了、码没改"。两面对撞才是一条腿。
    """
    by_code = {s["code"]: s for s in real["coded"]}
    doc = (REPO / gate.DOC_REL).read_text(encoding="utf-8")
    for code in Q83_CODES:
        site = by_code.get(code)
        assert site is not None, f"{code} 从射程里消失了——升档之后不该有人把它整条删掉"
        assert site["level"] == "error", f"{code} 代码面是 {site['level']}：安全能力瞎了一维按 §1.6 就是需人介入"
        rows = [l for l in doc.splitlines() if f"code={code}" in l]
        assert len(rows) == 1, f"{code} 在 §三 出现 {len(rows)} 行（应恰好一行）"
        assert " · level=error · " in rows[0], f"{code} 登记面不是 error：{rows[0]}"
        assert "Q8.3" in rows[0], f"{code} 的登记没引裁定小节，下一个人会当成随手改档"


def test_real_repo_q83_promotion_did_not_move_the_uncoded_ceiling(real):
    """这两枚本来就带具名码 ⇒ 升档不吃 §四 的无码格子，棘轮未动是**推导出来的**，不是巧合。

    83 → 86（执行记录 §二之一百二十六）：HEAD 现读 87，那个 +4 是先前批次留下的无码 WARNING 存量
    （pin 停在 83 已经过期，⛔ 不是本批引入的）；本批反向 **收紧 1 枚**——`af_config.py:134`
    那枚凭据 ACL 留痕补上了具名码。**§四 的上限 83 一个数字没动**，门在 C 判据上照旧红。
    """
    from collections import Counter

    nc = Counter(s["level"] for s in real["sites"] if not s["code"])
    by_code = {s["code"] for s in real["coded"]}
    assert set(Q83_CODES) <= by_code, "带码这一前提是'棘轮未动'的根据，码没了就要重新谈上限"
    assert nc["error"] == 10 and nc["warning"] == 72, (
        f"无码存量漂了（error={nc['error']} warning={nc['warning']}）——这跟 Q8.3 无关，"
        "但要在这里响，免得有人把两件事记成同一件"
    )


def test_real_repo_every_named_code_is_registered(real):
    """B 判据在真仓上是"存量全认领"： findings 里不许出现任何 B 项。"""
    findings, info = gate.check(REPO)
    b = [f for f in findings if f.startswith("B：")]
    assert not b, "\n".join(b)
    # 81 → 82 枚／84 → 85 站（20261021 收账）：`CATALOG_TOKEN_SOURCE_SKIPPED` 新码入 §三
    # （8664511 的 ARCH-01 补留痕），上面那条腿已经红过——本批对齐钉针。
    # 82 → 83 枚／85 → 86 站（v1.13.3）：`DRAFT_FAILURE_RECORD_SKIPPED` 新码入 §三
    # （af_mcp._t_draft 失败归因旁路），同上一批对齐钉针。
    # 83 → 84 枚／86 → 87 站（2026-10-24 AST 门禁收账）：`UPDATE_PREVIEW_READ_FAILED`
    # 新码入 §三（af_api.api_system_version 吞错改具名），同上一批对齐钉针。
    # 84 → 86 枚／87 → 89 站（2026-10-24 AF-001 收口）：`INBOX_REJECT_NO_TS`／
    # `INBOX_REJECT_STALE` 两枚新码入 §三（af_live.read_answer_inbox），同上。
    # 86 → 87 枚／89 → 90 站（2026-10-26 返工 5.1③）：`PAIR_LINK_FAILURE_COUNT_WRITE_FAILED`
    # 入 §三（af_api._pair_record_failure 吞异常改留痕），同上。
    assert info["codes"] == 91 and info["coded"] == 94


def test_real_repo_correlation_id_asymmetry_is_on_the_books(real):
    """信封侧有 trace_id、HTTP 请求侧没有——这个不对称是登记过的现状，不是漏读。"""
    assert len(real["env"]) == 6
    assert len({e["path"] for e in real["env"]}) == 3
    paths = {e["path"] for e in real["env"]}
    assert "src/autoforge/af_mqtt_bridge.py" in paths
    assert "src/autoforge/af_linkage_feed.py" in paths
    assert "src/autoforge/af_adapters/inbox.py" in paths
    assert real["req"] == [], f"HTTP 请求级 id 出现了：{real['req'][:3]}——§五 那行要跟着改状态"


def test_real_repo_http_face_readings_are_pinned(real):
    """`af_api.py` 的失败出口形状。⛔ 带账的两格钉「§五 登记 == 现读」，不带账的两格才钉死现状＋出处。

    裁定 `20261012-AF可观测性台账过期三格` §三 第三步点名上一版的形状：它写的是 `logger_sites == 0`，
    于是涨成 1 时只会喊「1 != 0」，说不出「账又过期了」——现在这腿直接对撞 §五 的登记值，
    账实一脱钩就喊改账（连同状态），要漂也得先有人把 §五 那一行改了。
    `ok_true`／`depends` 不是 §五 的读数键、没有账可对，按 #134/#135 的既有口径重钉现读并留出处。

    ⚠️ 取数面＝**入库面**（#118 那批）。在途工作树曾读到 91——那条腿本地绿、CI 红，
    红的就是期望值取自并发在途面。
    """
    http = real["http"]
    debt, errs = gate.parse_debt((REPO / gate.DOC_REL).read_text(encoding="utf-8"))
    assert not errs, "\n".join(errs)
    assert debt["af_api-logger-sites"]["num"] == http["logger_sites"], (
        f"§五 `af_api-logger-sites` 登记 {debt['af_api-logger-sites']['num']}，现读 "
        f"{http['logger_sites']}——要么改账（连同状态），要么把这次改动说明白"
    )
    assert debt["af_api-ok-false-sites"]["num"] == len(http["ok_false"]), (
        f"§五 `af_api-ok-false-sites` 登记 {debt['af_api-ok-false-sites']['num']}，现读 "
        f"{len(http['ok_false'])}——同上"
    )
    assert len(http["handlers"]) == 1, "两条装饰器挂同一函数，处理器数按函数计"
    assert len(http["middleware"]) == 1
    # 无账可对的两格，现状重钉：28 → 32、89 → 96，出处 `117f4dd`（WebUI 一键升级新增端点）
    # ＋ `86bd023`／`0055e61`（round-005 审计确证 bug 修复）；96 → 98 出处 2026-10-17 基础设施敲打
    # （09d93a7：agent delete/rename 加 authenticated Depends 主体安全，两处）。
    # 返工 5.1：98→102——第四批批 0-5 在 af_api 新增端点与 Depends 装配。
    # ok_true 现读 32（返工 5.1 把 agent-token/pair-link/pair 三处的 `"ok": True` 字面量
    # 改成派生值，先例=api_pair_redeem 的 ok=verified，消 AST 门 fake-ok-const 3 枚）。
    # ok_true 32 → 31（2026-10-30 v1.14.21）：`af_api` 的 enable 端点回执 ok 改派生值
    #（归属/码闸收口批），无账可对格按 #134 口径重钉现读。
    # depends 102 → 103（同批）：enable 端点装配 caller 主体 Depends（归属收口）。
    assert http["ok_true"] == 31
    assert http["depends"] == 103


def test_real_repo_debt_rows_match_fresh_readings(real):
    """E 判据的仓内那半：§五 四枚读数键与**现读**逐一对撞。⛔ 不写死数字——写死就等于再造一份「账停在旧值」。

    状态档跟着对撞：`账过期·待补码`（裁定 20261012 §二 1 新引入的第四档）只许用在
    「登记值已按现读改对、但这一格欠的是补码」——补码落地后改的是**状态**，不是 §四 的六行上限。
    """
    doc = (REPO / gate.DOC_REL).read_text(encoding="utf-8")
    debt, errs = gate.parse_debt(doc)
    assert not errs, "\n".join(errs)
    assert set(debt) == set(gate.DEBT_KEYS)
    fresh = {
        "af_api-logger-sites": real["http"]["logger_sites"],
        "af_api-ok-false-sites": len(real["http"]["ok_false"]),
        "envelope-trace-id-sites": len(real["env"]),
        "http-request-id-sites": len(real["req"]),
    }
    for key, n in fresh.items():
        assert debt[key]["num"] == n, f"`{key}` 登记 {debt[key]['num']}，现读 {n}——账停在旧值"
    assert debt["af_api-logger-sites"]["status"] == "已还"   # 返工 5.1⑥：三枚站点全部具名码化，账过期档解除
    assert debt["af_api-ok-false-sites"]["status"] == "账过期·待补码"
    assert debt["envelope-trace-id-sites"]["status"] == "已还"
    assert debt["http-request-id-sites"]["status"] == "欠账"


def test_real_repo_ceilings_did_not_rise_with_the_debt_rows(real):
    """裁定 §四 验收矩阵的**开关行**（row 3）：§五 改账之后，§四 六行必须一格未抬。

    这一腿红了＝裁定被读反了（把「账实相符」当成「授权 AF 替自己上调棘轮」，正是判例 A 堵的形状）。
    真要动这六行：先补码让现读下降，或另开一件评审——⛔ 不许顺手把期望值跟着现读改。
    """
    doc = (REPO / gate.DOC_REL).read_text(encoding="utf-8")
    ceilings, errs = gate.parse_ceilings(doc)
    assert not errs, "\n".join(errs)
    assert ceilings == {
        "debug": 14, "info": 7, "warning": 83, "error": 10, "exception": 11, "critical": 0,
    }, f"§四 六行动了：{ceilings}"
    # 2026-10-17 基础设施敲打补码后：warning 无码现读 86 → 72（已低于上限 83），超限差集清零，
    # 棘轮不仅没抬、反而被收紧了 11 枚余量。这一钉从"差集新鲜度"（86−83＝3）改为"收紧后基线"（72）：
    # 今后谁再新增无码 warning 把它顶回 83 以上，这里照样红；上限 83 一个数字没动是本腿的核心。
    nc = gate._no_code_counts(real["sites"])
    assert nc["warning"] == 72, (
        f"warning 无码现读是 {nc['warning']}，不是 72（补码后基线）——新增无码留痕请先补码进 §三，"
        "上限 83 仍不许抬"
    )


def test_real_repo_access_log_anchor_present():
    """D 判据：常量、定义、缺省参数指向常量、调用与 `uvicorn.run` 同函数——四格都在位。"""
    files = gate.py_files(REPO)
    trees, errs = gate.parse_all(files)
    assert not errs
    assert gate.access_log_shape(trees, REPO) == []


def test_real_repo_gate_is_green():
    findings, _info = gate.check(REPO)
    assert not findings, "\n".join(findings[:12])


def test_real_repo_conventions_are_written_down():
    """§1.6 是"报告说没有成文口径"那一格的落点：六档语义必须真在文档里。"""
    doc = (REPO / gate.DOC_REL).read_text(encoding="utf-8")
    assert "1.6 成文级别口径" in doc
    for lvl in gate.LEVELS:
        assert f"`{lvl.upper()}`" in doc, f"级别 {lvl} 没有写进成文口径"
    assert "需要人介入" in doc


# --------------------------------------------------------------------------------------
# 合成树：规则形状
# --------------------------------------------------------------------------------------

MOD_A = '''"""合成模块 A：三枚具名码 ＋ 两枚无码 ＋ 一枚 trace_id 信封。"""
import json
import logging

logger = logging.getLogger(__name__)


def load(path):
    try:
        data = json.loads(path)
    except ValueError:
        logger.error("MOD_A_LOAD_FAILED path=%s", path)
        return {}
    logger.warning("这一条没有码，只是句话")
    return data


def save(blob):
    logger.info("saved once")
    if not blob:
        logger.error("MOD_A_EMPTY_BLOB")
    return blob


def envelope():
    return {"trace_id": "t-1", "topic": "autoforge/x"}


def extra():
    logger.debug("MOD_A_EXTRA_DEBUG x=%s", 1)
'''

# 追加在末尾的新站点（不动既有行号）——形状与 MOD_A 里的具名码一致
EXTRA_SITED = '''

def late():
    logger.error("MOD_A_LATE_CODE tag=%s", "z")
'''

EXTRA_UNCODED = '''

def late2():
    logger.warning("又一条不带码的")
'''

CLI_TXT = '''"""合成 CLI：访问日志掩码三件套 ＋ serve 里的 uvicorn.run。"""
import logging

import uvicorn

_ACCESS_LOG = "uvicorn.access"
log = logging.getLogger("autoforge.cli")


class _Mask(logging.Filter):
    def filter(self, record):
        return True


def install_access_log_token_mask(logger_name: str = _ACCESS_LOG) -> bool:
    lg = logging.getLogger(logger_name)
    for f in lg.filters:
        if isinstance(f, _Mask):
            return False
    lg.addFilter(_Mask())
    return True


def serve(host="127.0.0.1", port=8848):
    app_ = object()
    bridge = object()
    install_access_log_token_mask()
    try:
        uvicorn.run(app_, host=host, port=port)
    finally:
        bridge.stop()


def stray():
    log.warning("合成 CLI 的一枚无码站点")
'''

API_TXT = '''"""合成 API：只有 `ok:` 字面量与异常处理器，没有任何日志。"""
from fastapi import Depends, FastAPI

app = FastAPI()


def _read():
    return True


@app.on_event("startup")
def boot():
    app.add_middleware(object)


@app.exception_handler(ValueError)
def on_value_error(request, exc):
    return {"ok": False, "error": str(exc)}


def endpoint(x=Depends(_read)):
    if not x:
        return {"ok": False, "error": "bad"}
    return {"ok": True, "data": x}
'''


def _mk(root: Path) -> dict:
    """建一棵能过射程前提的合成仓，并返回门自己的扫描结果（行号由它生成，不写死）。"""
    pkg = root / "src" / "autoforge"
    pkg.mkdir(parents=True)
    (pkg / "mod_a.py").write_text(MOD_A, encoding="utf-8")
    (pkg / "af_cli.py").write_text(CLI_TXT, encoding="utf-8")
    (pkg / "af_api.py").write_text(API_TXT, encoding="utf-8")
    docs = root / "docs"
    docs.mkdir()
    files = gate.py_files(root)
    trees, errs = gate.parse_all(files)
    assert not errs, errs
    sites, outside = gate.scan_sites(trees, root)
    assert outside == []
    coded = [s for s in sites if s["code"]]
    assert len(coded) == 3, f"合成树预设被改坏：具名码应 3 枚，现读 {len(coded)}"
    from collections import Counter

    nc = Counter(s["level"] for s in sites if not s["code"])
    rows = [
        f"- `{s['path']}:{s['lineno']}` · code={s['code']} · level={s['level']}"
        " · 级别语义：合成用例里的这一档 · 介入：不需人介入"
        f" · 依据：{s['path']} 的 `{s['func']}` · 理由：合成用例，说明这一枚码为什么可以长在这里"
        " · 认领：AF"
        for s in coded
    ]
    ceilings = "\n".join(f"- {lvl} = {nc[lvl]}" for lvl in gate.LEVELS)
    debt = "\n".join(
        [
            "- `af_api-logger-sites` = 0 · 状态：欠账 · 依据：src/autoforge/af_api.py · 说明：合成树上同样不带日志 · 认领：待裁",
            "- `af_api-ok-false-sites` = 2 · 状态：欠账 · 依据：src/autoforge/af_api.py · 说明：合成树里两枚 · 认领：待裁",
            "- `envelope-trace-id-sites` = 1 · 状态：已还 · 依据：src/autoforge/mod_a.py · 说明：合成信封一枚 · 认领：AF",
            "- `http-request-id-sites` = 0 · 状态：欠账 · 依据：src/autoforge/af_api.py · 说明：合成树也没有 · 认领：待裁",
        ]
    )
    doc = f"""# 可观测性清单（合成）

## 一、口径与射程

### 1.6 成文级别口径（AF 提案 · 2026-10-11）

| 级别 | 语义（写这一条时我在承诺什么） | 是否代表需要人介入 | 现读存量 |
| --- | --- | --- | --- |
| `DEBUG` | 合成 | 不需 | 见 §2.1 计数行 |
| `INFO` | 合成 | 不需 | 见 §2.1 计数行 |
| `WARNING` | 合成 | 看频次 | 见 §2.1 计数行 |
| `ERROR` | 合成 | 需 | 见 §2.1 计数行 |
| `EXCEPTION` | 合成 | 需 | 见 §2.1 计数行 |
| `CRITICAL` | 合成 | 需 | 见 §2.1 计数行 |

## 二、现读面（自动段）

{gate.AUTO_BEGIN}
（占位行）
{gate.AUTO_END}

## 三、具名码登记（默认拒绝，新码不登记即红）

{chr(10).join(rows)}

## 四、每级别「无码站点」上限（只减不增）

{ceilings}

## 五、HTTP 面与关联 id 留痕欠账登记

{debt}

## 六、复测对撞表

| 报告的话 | 现读 | 定性 |
| --- | --- | --- |
| 合成 | 合成 | 合成 |

## 七、递 DCD 的那半边

合成。
"""
    (docs / "可观测性清单.md").write_text(doc, encoding="utf-8", newline="\n")
    assert gate.write_doc(root) == 0, "--write 在合成树上跑不通"
    return {"root": root, "sites": sites, "coded": coded, "nc": nc}


@pytest.fixture()
def synth(tmp_path: Path):
    return _mk(tmp_path)


def _findings(root: Path) -> list[str]:
    blocked = gate.anchor_ok(root)
    if blocked:
        return [f"射程塌：{blocked}"]
    findings, _info = gate.check(root)
    return findings


def _rules(findings: list[str]) -> set[str]:
    return {f[0] for f in findings if f[:2] in ("A：", "B：", "C：", "D：", "E：")}


def _replace(path: Path, needle: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert needle in text, f"变异锚点没命中：{needle[:40]}"
    path.write_text(text.replace(needle, new, 1), encoding="utf-8", newline="\n")


def _append(path: Path, text: str) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def test_synth_baseline_is_green(synth):
    assert _findings(synth["root"]) == []


def test_synth_main_returns_zero_when_green(synth):
    assert gate.main(["check_observability.py", str(synth["root"])]) == 0


# ---------------- B：具名码默认拒绝 ----------------


def test_b_unregistered_new_code_is_red(synth):
    root = synth["root"]
    _append(root / "src" / "autoforge" / "mod_a.py", EXTRA_SITED)
    f = _findings(root)
    assert "B" in _rules(f)
    assert any("MOD_A_LATE_CODE" in x for x in f), f


def test_b_level_mismatch_is_red(synth):
    s = synth["coded"][0]
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        f"`{s['path']}:{s['lineno']}` · code={s['code']} · level={s['level']}",
        f"`{s['path']}:{s['lineno']}` · code={s['code']} · level={'warning' if s['level'] != 'warning' else 'info'}",
    )
    f = _findings(synth["root"])
    assert "B" in _rules(f) and any("现读是" in x for x in f), f


def test_b_registered_error_downgraded_in_code_is_red(synth):
    """反向腿（Q8.3 的那一半）：登记面写 error，有人把**代码面**降回 warning ⇒ 必须红。

    上面那条注入的是清单，这一条注入的是代码——降级恰恰只会发生在代码里。
    """
    target = next(s for s in synth["coded"] if s["level"] == "error")
    _replace(
        synth["root"] / "src" / "autoforge" / "mod_a.py",
        f'logger.error("{target["code"]}',
        f'logger.warning("{target["code"]}',
    )
    f = _findings(synth["root"])
    anchor = f"{target['path']}:{target['lineno']}"
    assert any(
        x.startswith("B：") and anchor in x and "现读是 `warning`" in x for x in f
    ), f


def test_b_code_mismatch_is_red(synth):
    s = synth["coded"][1]
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        f":{s['lineno']}` · code={s['code']}",
        f":{s['lineno']}` · code=OTHER_CODE_NAME",
    )
    assert any("登记 code=" in x for x in _findings(synth["root"]))


def test_b_missing_fields_are_red(synth):
    s = synth["coded"][2]
    line = f"- `{s['path']}:{s['lineno']}` · code={s['code']} · level={s['level']}"
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        next(
            l
            for l in (synth["root"] / "docs" / "可观测性清单.md")
            .read_text(encoding="utf-8")
            .splitlines()
            if l.startswith(line)
        ),
        line,
    )
    f = _findings(synth["root"])
    assert "B" in _rules(f) and any("缺字段" in x for x in f), f


def test_b_unknown_field_is_red(synth):
    s = synth["coded"][0]
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        f"`{s['path']}:{s['lineno']}` · code={s['code']} · level={s['level']} · 级别语义：",
        f"`{s['path']}:{s['lineno']}` · code={s['code']} · level={s['level']} · 备注：x · 级别语义：",
    )
    assert any("未知字段" in x for x in _findings(synth["root"]))


def test_b_bad_intervention_tier_is_red(synth):
    """「介入」只认三档——防止下一批有人自造一档，把 §1.6 的口径掏空。"""
    s = synth["coded"][0]
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        f"{s['code']} · level={s['level']} · 级别语义：合成用例里的这一档 · 介入：不需人介入",
        f"{s['code']} · level={s['level']} · 级别语义：合成用例里的这一档 · 介入：大概要看看吧",
    )
    assert any("不在 ('需人介入'" in x or "介入档" in x for x in _findings(synth["root"])), _findings(synth["root"])


def test_b_unowned_claim_is_red(synth):
    s = synth["coded"][0]
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        f"可以长在这里 · 认领：AF",
        f"可以长在这里 · 认领：不知道谁",
    )
    assert any("不属于" in x for x in _findings(synth["root"]))


def test_b_dangling_anchor_is_red(synth):
    s = synth["coded"][0]
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        f"依据：{s['path']} 的",
        "依据：src/autoforge/no_such_module.py 的",
    )
    assert any("找不到文件" in x for x in _findings(synth["root"]))


def test_b_placeholder_reason_is_red(synth):
    s = synth["coded"][0]
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        "理由：合成用例，说明这一枚码为什么可以长在这里",
        "理由：待补",
    )
    assert any("占位词" in x for x in _findings(synth["root"]))


def test_b_single_char_placeholder_reason_is_red(synth):
    """整值「略」是占位；但「属策略」里的"略"不是——上一格正是本批第一次撞出来的假红形状。"""
    s = synth["coded"][0]
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        "理由：合成用例，说明这一枚码为什么可以长在这里",
        "理由：略",
    )
    assert any("占位词" in x for x in _findings(synth["root"]))


def test_b_reason_containing_the_char_lue_is_not_red(synth):
    """反向自证：理由里出现「策略／行略」这样的正常用词，绝不能被单字占位表误杀。"""
    s = synth["coded"][0]
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        "理由：合成用例，说明这一枚码为什么可以长在这里",
        "理由：这是策略面而非性能面，行略过一次也要能被检索到，所以留码。",
    )
    f = _findings(synth["root"])
    assert not any("占位词" in x for x in f), f


def test_b_stale_registration_is_red(synth):
    doc = synth["root"] / "docs" / "可观测性清单.md"
    # 必须插在 §三 那一段里（append 到文件末尾会落进 §七，`_section` 根本扫不到——那是一枚假绿用例）
    _replace(
        doc,
        gate.CEILING_HEADING,
        "- `src/autoforge/mod_a.py:999` · code=GONE_CODE_NAME · level=info"
        " · 级别语义：合成 · 介入：不需人介入 · 依据：src/autoforge/mod_a.py"
        " · 理由：这是一枚已经不在盘上的登记 · 认领：AF\n\n" + gate.CEILING_HEADING,
    )
    assert any("过期" in x or "读不出具名码站点" in x for x in _findings(synth["root"]))


def test_b_duplicate_registration_is_red(synth):
    s = synth["coded"][0]
    doc = synth["root"] / "docs" / "可观测性清单.md"
    line = next(
        l for l in doc.read_text(encoding="utf-8").splitlines()
        if l.startswith(f"- `{s['path']}:{s['lineno']}`")
    )
    _replace(doc, gate.CEILING_HEADING, line + "\n\n" + gate.CEILING_HEADING)
    assert any("重复登记" in x for x in _findings(synth["root"]))


# ---------------- C：无码站点上限 ----------------


def test_c_uncoded_site_over_ceiling_is_red(synth):
    root = synth["root"]
    _append(root / "src" / "autoforge" / "mod_a.py", EXTRA_UNCODED)
    f = _findings(root)
    assert "C" in _rules(f), f
    assert any("WARNING" in x and "上限" in x for x in f), f


# ---------------- A：自动段对撞 ----------------


def test_a_hand_edited_auto_section_is_red(synth):
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        "### 2.1 留痕站点分布（每文件 × 级别）",
        "### 2.1 留痕站点分布（手改过的手艺）",
    )
    assert "A" in _rules(_findings(synth["root"]))


# ---------------- D：访问日志掩码锚点 ----------------


def test_d_missing_call_is_red(synth):
    _replace(synth["root"] / "src" / "autoforge" / "af_cli.py", "    install_access_log_token_mask()\n", "")
    f = _findings(synth["root"])
    assert "D" in _rules(f) and any("uvicorn.run" in x for x in f), f


def test_d_constant_renamed_is_red(synth):
    _replace(synth["root"] / "src" / "autoforge" / "af_cli.py", '_ACCESS_LOG = "uvicorn.access"',
             '_ACCESS_LOG = "uvicorn.error"')
    assert any("x 读不出「模块级" in x or "uvicorn.access" in x for x in _findings(synth["root"]))


def test_d_default_argument_detached_is_red(synth):
    _replace(synth["root"] / "src" / "autoforge" / "af_cli.py",
             "def install_access_log_token_mask(logger_name: str = _ACCESS_LOG) -> bool:",
             "def install_access_log_token_mask(logger_name: str = \"uvicorn.access\") -> bool:")
    f = _findings(synth["root"])
    assert "D" in _rules(f) and any("缺省值" in x for x in f), f


def test_d_definition_gone_is_red(synth):
    _replace(synth["root"] / "src" / "autoforge" / "af_cli.py",
             "def install_access_log_token_mask(logger_name: str = _ACCESS_LOG) -> bool:",
             "def install_access_log_mask(logger_name: str = _ACCESS_LOG) -> bool:")
    assert any("读不出 `def install_access_log_token_mask" in x for x in _findings(synth["root"]))


# ---------------- E：欠账必须在册 ----------------


def test_e_debt_number_drift_is_red(synth):
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        "- `envelope-trace-id-sites` = 1 · 状态：已还",
        "- `envelope-trace-id-sites` = 7 · 状态：已还",
    )
    f = _findings(synth["root"])
    assert "E" in _rules(f) and any("登记 7" in x for x in f), f


def test_e_missing_debt_key_is_red(synth):
    doc = synth["root"] / "docs" / "可观测性清单.md"
    text = doc.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if l.startswith("- `http-request-id-sites`"))
    doc.write_text(text.replace(line, "", 1), encoding="utf-8", newline="\n")
    assert any("缺读数键" in x for x in _findings(synth["root"]))


def test_e_unknown_debt_key_is_red(synth):
    doc = synth["root"] / "docs" / "可观测性清单.md"
    _replace(
        doc,
        "## 六、复测对撞表",
        "- `made-up-reading-key` = 3 · 状态：按设计 · 依据：src/autoforge/af_api.py · 说明：多出来的一格 · 认领：AF\n\n"
        "## 六、复测对撞表",
    )
    assert any("不认的读数键" in x for x in _findings(synth["root"]))


def test_e_bad_status_word_is_red(synth):
    """状态只认那四档：写成"应该没事"就读不出，而读不出必须响，不能静默滑过这一行。"""
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        "- `af_api-logger-sites` = 0 · 状态：欠账",
        "- `af_api-logger-sites` = 0 · 状态：应该没事",
    )
    f = _findings(synth["root"])
    assert "E" in _rules(f), f


def test_e_ledger_expired_status_parses_and_stays_accountable(synth):
    """第四档 `账过期·待补码`（裁定 20261012 §二 1）必须能被 §五 解析——否则"改账"这一步本身会把门判红。

    反空集：把这一档换成没在册的词（`test_e_bad_status_word_is_red`）必须响；换成它在册的那一档
    且数字仍与现读对撞 ⇒ E 一条不许响。⛔ 这一档只动 §五，不碰 §四 六行。
    """
    doc = synth["root"] / "docs" / "可观测性清单.md"
    _replace(doc, "- `af_api-logger-sites` = 0 · 状态：欠账",
             "- `af_api-logger-sites` = 0 · 状态：账过期·待补码")
    f = _findings(synth["root"])
    assert "E" not in _rules(f), f
    parsed, errs = gate.parse_debt(doc.read_text(encoding="utf-8"))
    assert not errs, errs
    assert parsed["af_api-logger-sites"]["status"] == "账过期·待补码"


def test_e_claim_shape_is_red(synth):
    _replace(
        synth["root"] / "docs" / "可观测性清单.md",
        "- `af_api-ok-false-sites` = 2 · 状态：欠账 · 依据：src/autoforge/af_api.py · 说明：合成树里两枚 · 认领：待裁",
        "- `af_api-ok-false-sites` = 2 · 状态：欠账 · 依据：src/autoforge/af_api.py · 说明：合成树里两枚 · 认领：某某某",
    )
    assert any("不属于" in x for x in _findings(synth["root"]))


# ---------------- 射程塌：一律 exit 2 ----------------


def _rc(root: Path) -> int:
    return gate.main(["check_observability.py", str(root)])


@pytest.mark.parametrize("broken", ["no_doc", "no_auto", "no_three", "no_four", "no_five",
                                   "bad_py", "no_src", "no_logger_site", "ids_both_zero",
                                   "register_shape", "ceiling_shape", "debt_shape", "no_cli", "no_api"])
def test_range_collapse_exits_two(tmp_path: Path, broken: str):
    if broken in ("no_doc", "no_auto", "no_three", "no_four", "no_five", "register_shape",
                  "ceiling_shape", "debt_shape"):
        s = _mk(tmp_path)
        doc = tmp_path / "docs" / "可观测性清单.md"
        if broken == "no_doc":
            doc.unlink()
        elif broken == "no_auto":
            _replace(doc, gate.AUTO_BEGIN, "<!-- 被人重写过的标记 -->")
        elif broken == "no_three":
            _replace(doc, gate.CODE_HEADING, "## 三、具名码备忘")
        elif broken == "no_four":
            _replace(doc, gate.CEILING_HEADING, "## 四、每级别上限")
        elif broken == "no_five":
            _replace(doc, gate.DEBT_HEADING, "## 五、欠账备忘")
        elif broken == "register_shape":
            _replace(doc, gate.CODE_HEADING, gate.CODE_HEADING + "\n\n登记改成散文了，一行机器形状都没有：\n")
            text = doc.read_text(encoding="utf-8")
            body_start = text.index("- `")
            body_end = text.index("\n\n## 四、")
            doc.write_text(
                text[:body_start] + "MOD_A_LOAD_FAILED 那一枚由 AF 认领，理由写在评审记录里。" + text[body_end:],
                encoding="utf-8",
                newline="\n",
            )
        elif broken == "ceiling_shape":
            for lvl in gate.LEVELS:
                _replace(doc, f"- {lvl} = ", f"- {lvl} 上限 ")
        else:  # debt_shape
            for key in gate.DEBT_KEYS:
                _replace(doc, f"- `{key}`", f"· {key}")
    elif broken == "bad_py":
        _mk(tmp_path)
        (tmp_path / "src" / "autoforge" / "broken.py").write_text("def oops(:\n", encoding="utf-8")
    elif broken == "no_src":
        _mk(tmp_path)
        for p in (tmp_path / "src" / "autoforge").glob("*.py"):
            p.unlink()
    elif broken == "no_logger_site":
        _mk(tmp_path)
        (tmp_path / "src" / "autoforge" / "mod_a.py").write_text(
            'import json\n\n\ndef load(path):\n    return json.loads(path)\n\n\ndef envelope():\n'
            '    return {"trace_id": "t-1"}\n',
            encoding="utf-8",
        )
        # af_cli.py 里那枚 `log.warning` 也是站点：不一起摘掉，这一档根本塌不出"全树零站点"
        _replace(tmp_path / "src" / "autoforge" / "af_cli.py",
                 '    log.warning("合成 CLI 的一枚无码站点")', "    return None")
    elif broken == "ids_both_zero":
        _mk(tmp_path)
        (tmp_path / "src" / "autoforge" / "mod_a.py").write_text(
            'import json\nimport logging\n\nlogger = logging.getLogger(__name__)\n\n\n'
            'def load(path):\n    logger.warning("no code here")\n    return json.loads(path)\n',
            encoding="utf-8",
        )
    elif broken == "no_cli":
        _mk(tmp_path)
        (tmp_path / "src" / "autoforge" / "af_cli.py").unlink()
    else:  # no_api
        _mk(tmp_path)
        (tmp_path / "src" / "autoforge" / "af_api.py").unlink()
    assert _rc(tmp_path) == 2, _findings(tmp_path)


def test_scan_does_not_count_prose(synth):
    """注释／docstring 里出现 `logger.warning(` 不算站点——判调用只认 ast.Call。"""
    pkg = synth["root"] / "src" / "autoforge"
    before = len(gate.scan_sites(gate.parse_all(gate.py_files(synth["root"]))[0], synth["root"])[0])
    _append(
        pkg / "mod_a.py",
        '\n# 这里只是提到 logger.warning("MENTIONED_CODE")，不是调用\n'
        '"""提到 logging.getLogger(__name__).error 也不算。"""\n',
    )
    trees, errs = gate.parse_all(gate.py_files(synth["root"]))
    assert not errs
    after = len(gate.scan_sites(trees, synth["root"])[0])
    assert after == before, f"散文里的名字被站点化了：{before} → {after}"


def test_fstring_first_fragment_yields_code(synth):
    """f-string 首个片段带码要认得出来（JoinedStr 不是 Constant，上一批在这儿栽过）。"""
    _append(synth["root"] / "src" / "autoforge" / "mod_a.py",
            '\n\nlogger.warning(f"MOD_A_FSTRING_CODE x={1}")\n')
    trees, errs = gate.parse_all(gate.py_files(synth["root"]))
    assert not errs, f"注入的不是合法 Python，这一腿等于没测：{errs[0]}"
    sites, _ = gate.scan_sites(trees, synth["root"])
    assert any(s["code"] == "MOD_A_FSTRING_CODE" for s in sites), [s["code"] for s in sites]


def test_alias_logger_is_in_scope(synth):
    """`_logger = logging.getLogger(...)` 这类别名必须算——真仓里 `_logger` 承载 12 枚站点。"""
    _append(
        synth["root"] / "src" / "autoforge" / "mod_a.py",
        '\n\n_alias = logging.getLogger("autoforge.alias")\n\n\ndef aliased():\n'
        '    _alias.error("MOD_A_ALIAS_CODE v=%s", 1)\n',
    )
    trees, _errs = gate.parse_all(gate.py_files(synth["root"]))
    sites, outside = gate.scan_sites(trees, synth["root"])
    hit = [s for s in sites if s["code"] == "MOD_A_ALIAS_CODE"]
    assert hit and hit[0]["recv"] == "_alias", (hit, outside[:3])
    assert outside == []
