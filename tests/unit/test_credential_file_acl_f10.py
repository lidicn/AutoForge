"""安全第三轮 F-10 的判据腿：Windows 上 `os.chmod(0o600)` 不改 NTFS ACL，凭据限权必须走 ACL。

本机现读（2026-10-11，`icacls` 原样输出，⛔ 不是推导）：

- `.forge/` 的继承 ACE：`BUILTIN\\Users:(I)(RX)` 加 `NT AUTHORITY\\Authenticated Users:(I)(M)`；
- `.forge/credentials.json` 的实际 ACL 里同样挂着 `Authenticated Users:(I)(M)` 与 `Users:(I)(RX)`
  ⇒ 本机任何已认证用户**可读可改**；
- `tempfile.mkstemp` 出来的文件 `st_mode` 读回 `0o666`，`os.chmod(..., 0o600)` 之后 `st_mode`
  仍是 `0o666`、`icacls` 输出**逐字节相同**。

⇒ "限权在 rename 之前先落在 tmp 上"这条 P1-22 纪律在 Windows 上是空的：得由 ACL 完成。

腿族：A 平台口径（POSIX／Windows 各一头）／B 返回档逐一钉死且一律不影响写入结果／
C 接线在册（含把修前形状种回去的反空集腿）／D 未接线站点棘轮（射程取代码面，散文不算站点）／
E 文案与实测对撞／F 真机 `icacls` 往返（仅 Windows 跑，本机为证）。
"""
from __future__ import annotations

import ast
import logging
import os
import subprocess
from pathlib import Path

import pytest

from autoforge import af_atomic, af_config

REPO = Path(__file__).resolve().parents[2]
NT = "nt"


class _Proc:
    def __init__(self, rc: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


class _Recorder:
    """假 `subprocess.run`：按调用顺序回吐预设读数，并把 argv／kwargs 全部留下可查。"""

    def __init__(self, replies: list[_Proc], *, raises: BaseException | None = None) -> None:
        self.replies = replies
        self.raises = raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if self.raises is not None:
            raise self.raises
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


def _win(tmp: Path, replies, *, raises=None, domain="WORKSTATION", user="forgeuser"):
    """在 Windows 那一档下跑一次助手（`platform_name` 接缝，不碰真 `os.name`）。"""
    rec = _Recorder(replies, raises=raises)
    env = {"USERDOMAIN": domain, "USERNAME": user}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        state, note = af_atomic.restrict_file_acl(
            tmp / "credentials.json", runner=rec, platform_name=NT
        )
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return state, note, rec


# ── A. 平台口径：POSIX 不碰盘，Windows 才发命令 ──

def test_a1_on_posix_the_helper_is_a_no_op_and_spawns_nothing(tmp_path):
    if os.name == NT:
        pytest.skip("POSIX 那一档的真值只在 Linux／CI 上存在；Windows 本地由 A2 与 B 族承担")
    rec = _Recorder([_Proc(0)])
    state, note = af_atomic.restrict_file_acl(tmp_path / "credentials.json", runner=rec)
    assert state == "not_windows", (state, note)
    assert rec.calls == [], f"POSIX 上不该起任何进程：{rec.calls}"


def test_a2_on_windows_the_helper_issues_a_fixed_arg_list(tmp_path):
    state, note, rec = _win(
        tmp_path,
        [_Proc(0, stdout="处理成功"), _Proc(0, stdout="WORKSTATION\\forgeuser:(F)\r\nSYSTEM:(F)")],
    )
    assert state == "restricted", (state, note)
    argv, kwargs = rec.calls[0]
    assert argv[0] == "icacls"
    assert "/inheritance:r" in argv, f"没断继承＝只加不减，白做：{argv}"
    assert "WORKSTATION\\forgeuser:F" in argv, argv
    for principal in af_atomic.ACL_KEEP_PRINCIPALS:
        assert f"{principal}:F" in argv, f"保留主体被漏掉了：{argv}"
    assert kwargs.get("shell") is not True, "限权命令绝不许经 shell"
    assert all(isinstance(part, str) for part in argv), argv
    assert rec.calls[1][0] == ["icacls", str(tmp_path / "credentials.json")], "回读必须是现读，不能拿发出的那条当结果"


# ── B. 返回档：每一档都可判，且一律不改变写入结果 ──

def test_b1_refused_return_code_is_reported_not_raised(tmp_path):
    state, note, _rec = _win(tmp_path, [_Proc(1, stderr="拒绝访问。")])
    assert state == "refused", (state, note)
    assert "rc=1" in note


def test_b2_missing_icacls_is_reported_not_raised(tmp_path):
    state, note, _rec = _win(tmp_path, [_Proc(0)], raises=FileNotFoundError(2, "找不到 icacls"))
    assert state == "failed", (state, note)
    assert "FileNotFoundError" in note


def test_b3_readback_still_showing_inherited_ace_is_its_own_state(tmp_path):
    state, note, _rec = _win(tmp_path, [_Proc(0), _Proc(0, stdout="BUILTIN\\Users:(I)(RX)")])
    assert state == "still_inherited", (state, note)
    assert "(I)" in note


def test_b4_no_identity_resolves_refuses_to_guess_a_principal(tmp_path):
    state, _note, rec = _win_no_identity(tmp_path)
    assert state == "no_identity"
    assert rec.calls == [], "身份都读不出来，就不该发出任何一条授权命令"


def _win_no_identity(tmp: Path):
    rec = _Recorder([_Proc(0)])
    old = {k: os.environ.get(k) for k in ("USERDOMAIN", "USERNAME")}
    os.environ.pop("USERDOMAIN", None)
    os.environ.pop("USERNAME", None)
    try:
        state, note = af_atomic.restrict_file_acl(
            tmp / "credentials.json", runner=rec, platform_name=NT
        )
    finally:
        os.environ.update({k: v for k, v in old.items() if v is not None})
    return state, note, rec


def test_b5_every_returned_state_is_in_the_declared_catalog(tmp_path):
    for replies in ([_Proc(7, stderr="x")], [_Proc(0), _Proc(0, stdout="")], [_Proc(0), _Proc(9)]):
        state, _note, _rec = _win(tmp_path, replies)
        assert state in af_atomic.ACL_STATES, f"名单外的返回档：{state}"


def test_b6_posix_and_nt_return_values_never_raise_out_of_the_helper(tmp_path):
    for platform in ("posix", NT):
        for replies in ([_Proc(0)], [_Proc(1, stderr="拒绝")]):
            rec = _Recorder(replies)
            state, note = af_atomic.restrict_file_acl(
                tmp_path / "credentials.json", runner=rec, platform_name=platform
            )
            assert isinstance(state, str) and isinstance(note, str), (state, note)
            if platform == "posix":
                #: A1 那条在 Windows 上必然 skip，它想证的「POSIX 不发任何进程」由这一枚接缝补齐：
                #: 同一份判定、`platform_name="posix"`、本机真跑，⛔ 不留"只有远端才知"的空格。
                assert rec.calls == [], f"POSIX 档却起了进程：{rec.calls}"
                assert state == "not_windows", (state, note)


# ── C. 接线在册：凭据落盘那一手必须既 chmod、又叫助手、还消费返回值 ──

def _func_body(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _shape_is_wired(src: str, func: str = "_atomic_write") -> bool:
    """判"这函数既做了 chmod，又叫了 restrict_file_acl，还读了它的返回值"。

    "读返回值"取 **Load 语境**：只把返回值赋给一个变量、之后再不提到它，留痕代码等于被拆掉
    （本机变异 M3／C2b 实测：按名字命中会把这一档读成已接线）。

    ⚠️ 这一枚判定的射程边界（写清楚，不假装全覆盖）：`if False:` 之类**死分支**里引用返回值
    仍然算已接线——AST 层判不出分支可否达。真要钉死那一档得换成可达性分析，本批不做，
    残余登记在执行记录。
    """
    body = _func_body(ast.parse(src), func)
    if body is None:
        return False
    text = ast.unparse(body)
    reads_state = any(
        isinstance(node, ast.Name)
        and node.id.startswith("acl_state")
        and isinstance(node.ctx, ast.Load)
        for node in ast.walk(body)
    )
    return "os.chmod(" in text and "restrict_file_acl(" in text and reads_state


def test_c1_the_real_credential_writer_is_wired():
    src = (REPO / "src/autoforge/af_config.py").read_text(encoding="utf-8")
    assert _shape_is_wired(src), (
        "af_config._atomic_write 里 chmod／restrict_file_acl／读返回值三样缺一样——"
        "F-10 的修法只写在助手里而没接上落盘点，等于没修"
    )


def test_c2_the_pre_fix_shape_is_caught():
    #: 反空集腿：把修前形状（只 chmod，异常一口吞掉）种回去，必须被同一枚判定扫红。
    pre_fix = (
        "class Config:\n"
        "    def _atomic_write(self, path, data):\n"
        "        try:\n"
        "            os.chmod('t', 0o600)\n"
        "        except OSError:\n"
        "            pass\n"
    )
    assert _shape_is_wired(pre_fix) is False
    ignored = (
        "class Config:\n"
        "    def _atomic_write(self, path, data):\n"
        "        os.chmod('t', 0o600)\n"
        "        af_atomic.restrict_file_acl('t')\n"
    )
    assert _shape_is_wired(ignored) is False, "调用了却不读返回值＝返回值是装饰品"


def test_c2b_an_assigned_but_never_branched_return_is_not_wired():
    #: 本机变异跑出来的第二形态：`acl_state, acl_note = restrict_file_acl(...)` 之后把
    #: `if acl_state not in (...)` 那一支拆掉——名字还在，留痕却永远不会触发。
    #: 判定因此只认 **Load 语境**的引用，不认赋值目标（变异读数见执行记录）。
    decorative = (
        "class Config:\n"
        "    def _atomic_write(self, path, data):\n"
        "        os.chmod('t', 0o600)\n"
        "        acl_state, acl_note = af_atomic.restrict_file_acl('t')\n"
        "        return data\n"
    )
    assert _shape_is_wired(decorative) is False, "赋值给 acl_state 不等于消费它"


def test_c3_a_failed_acl_restriction_never_breaks_the_write(tmp_path, monkeypatch, caplog):
    #: 现场形状：Windows 上 icacls 一律失败 ⇒ 凭据仍必须落得下去、读得回来，且失败要留痕。
    monkeypatch.setattr(af_config, "restrict_file_acl", lambda p: ("refused", "模拟：拒绝访问"))
    root = tmp_path / ".forge"
    root.mkdir()
    cfg = af_config.Config(root)
    with caplog.at_level(logging.WARNING, logger="autoforge.af_config"):
        cfg.update_credentials(ha_token="tok-for-leg-c3")
    assert cfg.get_ha_token() == "tok-for-leg-c3"
    assert "refused" in caplog.text, "限权失败必须留痕，不许静默"


def test_c4_a_successful_restriction_stays_silent(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(af_config, "restrict_file_acl", lambda p: ("restricted", "已收紧"))
    root = tmp_path / ".forge"
    root.mkdir()
    cfg = af_config.Config(root)
    with caplog.at_level(logging.WARNING, logger="autoforge.af_config"):
        cfg.update_credentials(ha_token="tok-for-leg-c4")
    assert "已收紧" not in caplog.text, "成功不该刷告警"


# ── D. 未接线站点棘轮：只减不增，⛔ 不许新增"只靠 chmod 限权"的明文落盘点 ──

#: 此刻仍未接线的站点（文件 → 归属）。归零是目标，所以名单**只许减不许增**。
#: `af_auth.py` 那枚是明文令牌注册表（`TokenRegistry`），该文件躺着并发会话未提交的改动 ⇒ #79 窗。
NOT_WIRED_REGISTRY = {"af_auth.py": "归属 #79 窗（登录线在途文件，本批不碰）"}


def _chmod_0600_call_sites(tree: ast.AST) -> int:
    """代码面读数：AST 里真实存在的 `os.chmod(..., 0o600)` 调用点枚数。

    ⛔ 不用文本包含来定站点：`af_atomic.py` 的助手 docstring 整段在讲"为什么 chmod 不够"，
    按 `"os.chmod(" in text` 找会把**修 F-10 的那份代码**读成一枚未接线站点。
    """
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "os.chmod"
        and any(isinstance(a, ast.Constant) and a.value == 0o600 for a in node.args)
    )


def _plaintext_chmod_sites() -> dict[str, str]:
    out: dict[str, str] = {}
    unparsed: list[str] = []
    for py in sorted((REPO / "src/autoforge").rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            unparsed.append(py.name)
            continue
        if _chmod_0600_call_sites(tree):
            out[py.name] = text
    assert not unparsed, f"射程塌：src 里有文件解析不动，站点名单不可信：{sorted(unparsed)}"
    return out


def test_d0_the_site_discovery_reads_the_code_face_not_the_prose():
    #: 反例腿（判红先找反例）：docstring 里出现 `os.chmod(..., 0o600)` 字样**不该**被算成站点，
    #: 而真调用点必须被算成。两边都在同一枚判定里钉住，免得 D1 靠"把助手排除掉"蒙绿。
    prose = "def f():\n    \"\"\"为什么 os.chmod('t', 0o600) 不够。\"\"\"\n    return 1\n"
    code = "def f():\n    os.chmod('t', 0o600)\n"
    both = "def f():\n    \"\"\"见 os.chmod('t', 0o600)。\"\"\"\n    os.chmod('t', 0o600)\n"
    assert _chmod_0600_call_sites(ast.parse(prose)) == 0
    assert _chmod_0600_call_sites(ast.parse(code)) == 1
    assert _chmod_0600_call_sites(ast.parse(both)) == 1
    #: 真仓现读：助手所在那份文件自己**没有** chmod 调用点，所以它不在站点名单里。
    sites = _plaintext_chmod_sites()
    assert "af_atomic.py" not in sites, "af_atomic 只有散文提到 chmod：它不是站点，别把它当欠债"
    assert "af_config.py" in sites and "af_auth.py" in sites, f"真调用点读丢了：{sorted(sites)}"


def test_d1_every_plaintext_chmod_site_is_wired_or_registered():
    sites = _plaintext_chmod_sites()
    assert sites, "射程塌：src 里一枚 chmod 0o600 站点都读不到，这条判据就成了空气"
    unwired = {name for name, text in sites.items() if not _shape_is_wired(text)}
    assert unwired <= set(NOT_WIRED_REGISTRY), (
        f"新增的「只 chmod 限权」站点未在册即红：{sorted(unwired)}"
    )


def test_d2_the_registry_carries_no_stale_entry():
    present = {p.name for p in (REPO / "src/autoforge").rglob("*.py")}
    stale = set(NOT_WIRED_REGISTRY) - present
    assert not stale, f"名单里挂着已不存在的文件：{sorted(stale)}"


def test_d3_registered_sites_are_still_unwired():
    #: 名单不是荣誉簿：登记为"未接线"的文件若其实已接线，名单就该一起收掉（只减不增的另一头）。
    sites = _plaintext_chmod_sites()
    both = {name for name in NOT_WIRED_REGISTRY if name in sites and _shape_is_wired(sites[name])}
    assert not both, f"已接线却还挂在未接线名单上：{sorted(both)}"


# ── E. 文案与实测对撞：README 那一节不许写成通用废话 ──

README_ANCHOR = "## 4.2 明文凭据文件的可见范围：Windows 上 chmod 不算限权"


def test_e1_readme_states_the_measured_aces_not_a_generic_warning():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    parts = readme.split(README_ANCHOR)
    assert len(parts) == 2, "README 缺 §4.2 那一节（F-10 的文案半边）"
    body = parts[1].split("\n## ")[0]
    for needle in ("icacls", "Authenticated Users", "Users:(I)(RX)", "0o666", "credentials.json"):
        assert needle in body, f"文案里没有现读读数 {needle}：那一节不许写成通用提醒"


def test_e2_the_new_subprocess_site_keeps_the_fixed_argv_shape():
    #: 仓规：src 里 subprocess 站点极少且一律固定 argv、不经 shell。本批新引入的这一处同口径。
    src = (REPO / "src/autoforge/af_atomic.py").read_text(encoding="utf-8")
    assert "import subprocess" in src
    tree = ast.parse(src)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("runner")
    ]
    assert calls, "射程塌：runner 接缝一个调用点都读不到"
    for node in calls:
        assert node.args and isinstance(node.args[0], (ast.List, ast.Name)), "argv 必须是列表"
        assert "shell" not in {k.arg for k in node.keywords}


# ── F. 真机往返：不打桩，让真 `icacls` 在临时目录里走一遍 ──

@pytest.mark.skipif(
    os.name != NT,
    reason="这一枚要的是 NTFS 上的真往返，Linux CI 没有 ACL 面；本机为证（远端恒 skip，⛔ 不当远端证据引）",
)
def test_f1_a_real_icacls_roundtrip_restricts_a_probe_file(tmp_path):
    #: B 族全部走桩，只能证"返回档判得对"；这一枚证的是**那条 argv 在真 NTFS 上确实把继承断掉了**。
    probe = tmp_path / "credentials.json"
    probe.write_text('{"ha_token": "PROBE-NOT-A-REAL-TOKEN"}', encoding="utf-8")
    before = subprocess.run(["icacls", str(probe)], capture_output=True, text=True)
    assert before.returncode == 0, before.stderr
    state, note = af_atomic.restrict_file_acl(probe)
    assert state == "restricted", (state, note)
    back = subprocess.run(["icacls", str(probe)], capture_output=True, text=True)
    assert back.returncode == 0, back.stderr
    assert "(I)" not in back.stdout, f"真机回读仍见继承 ACE：{back.stdout}"
    for principal in af_atomic.ACL_KEEP_PRINCIPALS + (af_atomic._current_principal(),):
        assert principal in back.stdout, f"该保留的主体被断掉了：{principal}｜{back.stdout}"
