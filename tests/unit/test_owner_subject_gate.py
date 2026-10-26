"""owner 主体单一真源 ＋ `/api/user/agents/*` 主体校验门禁（裁定 20261011《八问》§3 Q1 丙）。

真仓 `src/autoforge/af_api.py` 的主体绑定已于 2026-10-17 补齐（`_AGENT_LIST_RESERVED` 单一真源
＋ `_assert_agent_subject_owner(agent_id, caller_subject)` 把调用方主体与路径主体绑定，非 owner 403），
门禁现在对真仓判绿。本文件的合成夹具同时保留 0／1／2 三档真路径：红数不钉真仓，避免
"修好了反而判红"的 perverse 激励。
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_owner_subject.py"


def _gate():
    spec = importlib.util.spec_from_file_location("check_owner_subject", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repo(tmp_path: Path, source: str) -> Path:
    """搭一棵只含 `src/autoforge/af_api.py` 的合成树，让门按真口径去读它。"""
    api = tmp_path / "src" / "autoforge" / "af_api.py"
    api.parent.mkdir(parents=True, exist_ok=True)
    api.write_text(source, encoding="utf-8")
    return tmp_path


# ── 合成夹具：一份"补齐后"的形状（门必须对它判绿，否则红永远修不掉）──────

FIXED = '''
from fastapi import Depends

_OWNER_SUBJECTS = frozenset({"owner", "shared"})


def authenticated():
    ...


class TokenInfo:
    subject: str


@app.get("/api/user/agents", dependencies=[Depends(_read)])
def api_agents_list(token_info: TokenInfo | None = Depends(authenticated())):
    mine = token_info.subject if token_info else None
    return [s for s in registry.subjects() if s["subject"] not in _OWNER_SUBJECTS and s["subject"] != mine]


@app.delete("/api/user/agents/{agent_id}", dependencies=[Depends(_write)])
def api_agent_delete(agent_id: str, token_info: TokenInfo | None = Depends(authenticated())):
    if agent_id != token_info.subject:
        raise HTTPException(403, "只能撤销自己名下的 agent")
    return {"ok": True}


@app.patch("/api/user/agents/{agent_id}", dependencies=[Depends(_write)])
def api_agent_rename(agent_id: str, token_info: TokenInfo | None = Depends(authenticated())):
    assert_agent_subject(agent_id, token_info.subject)
    return {"ok": True}
'''


def _rc(repo: Path) -> tuple[int, list[str], dict[str, object]]:
    gate = _gate()
    red, errs, info = gate.check(repo)
    rc = 2 if errs else (1 if red else 0)
    return rc, [*errs, *red], info


def test_fixed_shape_is_green(tmp_path):
    """补齐后的形状必须判绿——否则这道门不叫"逼两侧补齐"，叫永久红。"""
    ast.parse(FIXED)
    rc, findings, _info = _rc(_repo(tmp_path, FIXED))
    assert rc == 0, f"补齐后仍被判红：{findings}"


def test_mutation_dropping_the_subject_compare_goes_red(tmp_path):
    """负控（裁定 §4-2「故意去掉主体校验，腿必须红」）：删掉那一行比较，判据 B 必须响。"""
    broken = FIXED.replace(
        '    if agent_id != token_info.subject:\n        raise HTTPException(403, "只能撤销自己名下的 agent")\n',
        "",
    )
    assert broken != FIXED, "注入没生效就判 FAIL——先确认锚点还在不在"
    ast.parse(broken)
    rc, findings, _info = _rc(_repo(tmp_path, broken))
    assert rc == 1, f"去掉主体校验后门却判 rc={rc}：那道绑定根本没被看着"
    assert any(f.startswith("B：") and "DELETE" in f for f in findings), findings


def test_mutation_dropping_the_principal_dep_goes_red(tmp_path):
    """负控：把调用方身份整个拿走（只留 `agent_id` 一枚参数），腿同样要红。"""
    broken = FIXED.replace(
        "def api_agent_delete(agent_id: str, token_info: TokenInfo | None = Depends(authenticated())):",
        "def api_agent_delete(agent_id: str):",
    ).replace(
        "    if agent_id != token_info.subject:",
        "    if agent_id != _OWNER_SUBJECTS:",
    )
    assert broken != FIXED
    ast.parse(broken)
    rc, findings, _info = _rc(_repo(tmp_path, broken))
    assert rc == 1, f"没有调用方身份后门却 rc={rc}"
    hits = [f for f in findings if f.startswith("B：") and "api_agent_delete" in f]
    assert len(hits) == 1 and "没把调用方身份递进来" in hits[0], hits


def test_mutation_reintroducing_a_literal_subject_list_goes_red(tmp_path):
    """负控：判定侧再抄一份字面量名单 ⇒ 判据 A 响（owner 面只许 `_OWNER_SUBJECTS` 一枚真源）。"""
    broken = FIXED.replace('if s["subject"] not in _OWNER_SUBJECTS', 'if s["subject"] not in ("owner", "shared")')
    assert broken != FIXED
    ast.parse(broken)
    rc, findings, _info = _rc(_repo(tmp_path, broken))
    assert rc == 1, f"字面量名单重现后门却 rc={rc}"
    assert any(f.startswith("A：") for f in findings), findings


def test_helper_named_without_subject_is_not_a_pass(tmp_path):
    """helper 名字不带 subject ⇒ 本门认不出它校验了什么，仍判红（不许"我有个函数所以放过"）。"""
    broken = FIXED.replace("assert_agent_subject(agent_id, token_info.subject)", "check_thing(agent_id, token_info.subject)")
    assert broken != FIXED
    ast.parse(broken)
    rc, findings, _info = _rc(_repo(tmp_path, broken))
    assert rc == 1 and any("PATCH" in f for f in findings), findings


def test_empty_reason_exemption_is_still_red(tmp_path):
    """豁免标记写了却不写理由＝没认领，与不豁免同判红（仓内既有口径）。"""
    marked = FIXED.replace(
        "def api_agent_delete(agent_id: str, token_info: TokenInfo | None = Depends(authenticated())):",
        "def api_agent_delete(agent_id: str, token_info: TokenInfo | None = Depends(authenticated())):  # owner-subject: exempt()",
    )
    assert marked != FIXED
    ast.parse(marked)
    rc, findings, _info = _rc(_repo(tmp_path, marked))
    assert rc == 1 and any("理由为空" in f for f in findings), findings


def test_exemption_with_a_reason_suppresses_only_that_site(tmp_path):
    """写了理由 ⇒ 那一站放过，另一站（PATCH）照样红——豁免是逐站的，不是一张"本文件免检"。"""
    marked = FIXED.replace(
        "def api_agent_delete(agent_id: str, token_info: TokenInfo | None = Depends(authenticated())):",
        "def api_agent_delete(agent_id: str, token_info: TokenInfo | None = Depends(authenticated())):  # owner-subject: exempt(这一枚由内网侧代管，主体绑定在网关做)",
    ).replace("assert_agent_subject(agent_id, token_info.subject)", "pass")
    ast.parse(marked)
    rc, findings, _info = _rc(_repo(tmp_path, marked))
    assert rc == 1, findings
    assert any("PATCH" in f for f in findings) and not any("DELETE" in f for f in findings), findings


def test_real_repo_scope_has_not_collapsed():
    """真仓腿只核射程：读得到定义行、数得出端点、判据不塌成空集。

    今天真仓 rc==1（A 一处＋B 两枚）——那一格记在 `gates.sh` 与执行记录里，不钉进本腿。
    """
    gate = _gate()
    red, errs, info = gate.check(ROOT)
    assert errs == [], f"射程塌了：{errs}"
    assert info["endpoints"] >= 3, f"`/api/user/agents*` 端点数掉到 {info['endpoints']}：本门已盯不住这面"
    assert info["with_path_param"] >= 2, "带路径参数的两枚（delete／rename）没数到——判据 B 失去射程"
    assert isinstance(info["symbol_line"], int), "`_OWNER_SUBJECTS` 的定义行读不出来——判据 A 失去对照物"
    assert all(f.startswith(("A：", "B：")) for f in red), f"红消息没有可照着改的前缀：{red[:2]}"


def test_real_repo_gets_green_when_the_two_faces_are_landed(tmp_path):
    """在**真仓文本**上证明"补齐即绿"：把两格按判据写法补进 tmp 副本，A/B 两头一起消失。

    ⛔ 只写进 tmp 树，工作树一个字都不动（那格归登录线）。
    """
    src = (ROOT / "src" / "autoforge" / "af_api.py").read_text(encoding="utf-8")
    patched = src.replace(
        '            if s["subject"] not in ("shared", "unknown", "owner")',
        '            if s["subject"] not in _OWNER_SUBJECTS',
    ).replace(
        "def api_agent_delete(agent_id: str, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict[str, Any]:",
        "def api_agent_delete(agent_id: str, token_info: TokenInfo | None = Depends(authenticated())) -> dict[str, Any]:",
    ).replace(
        "def api_agent_rename(agent_id: str, body: AgentRenameBody) -> dict[str, Any]:",
        "def api_agent_rename(agent_id: str, body: AgentRenameBody, token_info: TokenInfo | None = Depends(authenticated())) -> dict[str, Any]:",
    ).replace(
        "        n = registry.revoke_by_subject(agent_id, except_token=current_token)",
        "        if agent_id != token_info.subject:\n            raise HTTPException(403, \"只能操作自己名下的 agent\")\n        n = registry.revoke_by_subject(agent_id, except_token=None)",
    ).replace(
        # 批 4：rename_subject 已包进 try/except（服务层 ValueError ⇒ 403），
        # 锚点与注入体都用 12 空格缩进，否则落在 try 块里破坏缩进。
        "            n = registry.rename_subject(agent_id, new_name)",
        "            if agent_id != token_info.subject:\n                raise HTTPException(403, \"只能操作自己名下的 agent\")\n            n = registry.rename_subject(agent_id, new_name)",
    )
    assert patched != src, "注入没生效（真仓文本漂了锚点）——先确认端点写法还在"
    ast.parse(patched)
    repo = _repo(tmp_path, patched)
    rc, findings, _info = _rc(repo)
    assert rc == 0, f"补齐两格后门仍 rc={rc}：{findings[:3]}"


def test_missing_definition_line_is_scope_collapse_not_clean(tmp_path):
    """⛔ `_OWNER_SUBJECTS` 不见了要退 2（无从判定），不许报"干净"。"""
    source = FIXED.replace('_OWNER_SUBJECTS = frozenset({"owner", "shared"})\n', "")
    ast.parse(source)
    rc, findings, _info = _rc(_repo(tmp_path, source))
    assert rc == 2, f"对照物没了却 rc={rc}：{findings}"


def test_no_agents_endpoints_is_scope_collapse(tmp_path):
    """端点面整个搬走 ⇒ 本门没有射程，退 2 而不是绿。"""
    source = "\n".join(l for l in FIXED.splitlines() if "/api/user/agents" not in l)
    ast.parse(source)
    rc, findings, _info = _rc(_repo(tmp_path, source))
    assert rc == 2, f"数不出端点还判 rc={rc}：那一格是空集给的干净"


def test_gate_is_wired_into_gates_sh():
    """独立成档的门必须真在链上跑，否则它只是仓里一个没人执行的脚本。"""
    text = (ROOT / "gates.sh").read_text(encoding="utf-8")
    assert "check_owner_subject.py" in text, "门没接进 gates.sh：那它永远不会响"


def test_script_help_runs_and_returns_the_declared_codes(tmp_path):
    """CLI 面自证：退出码 0/1 各有一条真跑得通的路径（合成好树＝0，合成坏树＝1）。

    ⛔ 不拿真仓当"必然 rc=1"的坏样本——真仓的 af_api 主体绑定补齐后该判 0，把"真仓=1"钉进
    pytest 正好是文件头 docstring 自己反对的"修好了反而判红"的 perverse 激励。rc=1 这一档用
    一棵当场构造的合成坏树来证明。
    """
    ok = subprocess.run([sys.executable, str(SCRIPT), str(_repo(tmp_path / "fixed", FIXED))],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    broken = FIXED.replace(
        '    if agent_id != token_info.subject:\n        raise HTTPException(403, "只能撤销自己名下的 agent")\n',
        "",
    )
    assert broken != FIXED
    bad = subprocess.run([sys.executable, str(SCRIPT), str(_repo(tmp_path / "broken", broken))],
                         capture_output=True, text=True)
    assert bad.returncode == 1, bad.stdout + bad.stderr
