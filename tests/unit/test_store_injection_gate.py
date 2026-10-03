"""store 注入门禁必须"能变红"（铁律 #8），且必须"不误响"。

判据针对的形状（本仓 §二之十六 第一处真缺陷）：`af_mcp._t_live` 调 `svc.live_run(...)`
漏了 `store=store`，而 `live_run` 的 `store` 形参带默认值 ⇒ **少递一个关键字参数不报错、
不崩**，只让那条入口面的 Tier-0 设备保护静默退化成"没有 store"。默认值把漏传变成静默
降级，所以判据只能在调用边界上静态判。

第一版按**裸函数名**查签名，把 `store.snapshot([...])`、`_direction(snapshot, ...)` 这类
"变量恰好和函数同名"的正常调用判成红——28 处误报。一堵会自己响的墙必须先做到不误响，
所以 `_t_*_is_not_flagged` 那几条不是凑数：它们钉的是"这扇门又会变成噪音"的那条回去的路。
"""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_store_injection.py"

# 目标函数库：三种签名形状各一条——可选关键字 store、keyword-only store、必填位置 store。
TARGETS = '''
def live_run(graph, store=None):
    ...


def simulate_track(track, ir, *, store=None):
    ...


def status(store):
    ...


def no_store(arg):
    ...
'''


def _module():
    spec = importlib.util.spec_from_file_location("check_store_injection", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _check(tmp_path: pathlib.Path, caller: str) -> tuple[list[str], int]:
    """写一颗两颗模块的小树（`af_service` 提供签名、`caller.py` 提供调用点），返回（漏传, 豁免数）。"""
    (tmp_path / "af_service.py").write_text(TARGETS, encoding="utf-8")
    (tmp_path / "caller.py").write_text(caller, encoding="utf-8")
    findings, _files, exempted = _module().check(tmp_path)
    return findings, exempted


# ── 本职：漏传判红 ────────────────────────────────────────────────────

def test_named_call_with_store_is_clean(tmp_path):
    findings, _ = _check(tmp_path, """
from . import af_service as svc


def handler(store, args):
    return svc.live_run(args["graph"], store=store)
""")
    assert findings == []


def test_missing_store_on_named_call_is_red(tmp_path):
    """本门的本职形状：漏传不报错，只是那道闸门静默少装一面。"""
    findings, _ = _check(tmp_path, """
from . import af_service as svc


def handler(store, args):
    return svc.live_run(args["graph"])
""")
    assert len(findings) == 1
    assert "af_service.live_run" in findings[0]
    assert "caller.py:" in findings[0]  # 判据要能指路：报的是文件+行号，不是抽象描述


def test_positional_store_counts_as_passed(tmp_path):
    """`store` 在位置下标 0：按顺序数得出来就算传了，不逼人造关键字。"""
    findings, _ = _check(tmp_path, """
from . import af_service as svc


def handler(store):
    return svc.status(store)
""")
    assert findings == []


def test_missing_required_positional_store_is_red(tmp_path):
    findings, _ = _check(tmp_path, """
from . import af_service as svc


def handler(store):
    return svc.status(None) if False else svc.status()
""")
    assert len(findings) == 1
    assert "af_service.status" in findings[0]


def test_keyword_only_store_needs_the_keyword(tmp_path):
    findings, _ = _check(tmp_path, """
from . import af_service as svc


def handler(track, ir, store):
    return svc.simulate_track(track, ir)
""")
    assert len(findings) == 1
    assert "af_service.simulate_track" in findings[0]


def test_vararg_after_store_still_counts_positional(tmp_path):
    """`def f(store, *rest)` 的位置下标仍然确定——早先"有 *args 就一律要求关键字"会误判这一形状。"""
    (tmp_path / "af_service.py").write_text("def run(store, *rest):\n    ...\n", encoding="utf-8")
    (tmp_path / "caller.py").write_text("""
from . import af_service as svc


def handler(the_store):
    return svc.run(the_store, 1, 2)
""", encoding="utf-8")
    assert _module().check(tmp_path)[0] == []


# ── 转发形状：`_svc(svc.live_run, ...)` 里 store 要出现在转发关键字上 ──

def test_forwarding_ref_without_store_is_red(tmp_path):
    findings, _ = _check(tmp_path, """
from . import af_service as svc


def _svc(fn, *a, **kw):
    return fn(*a, **kw)


def handler(store, args):
    return _svc(svc.live_run, args["graph"])
""")
    assert len(findings) == 1
    assert "af_service.live_run" in findings[0]


def test_forwarding_ref_with_store_is_clean(tmp_path):
    findings, _ = _check(tmp_path, """
from . import af_service as svc


def _svc(fn, *a, **kw):
    return fn(*a, **kw)


def handler(store, args):
    return _svc(svc.live_run, args["graph"], store=store)
""")
    assert findings == []


# ── 不误响：第一版 28 处误报的三条回去的路 ───────────────────────────

def test_receiver_that_is_not_a_module_is_not_flagged(tmp_path):
    """`store.snapshot([...])`：接收者是运行期的 store 对象，静态判不出函数身份。

    裸名查签名的上一版把这一句当成 `af_service.snapshot(...)` 判红（28 处误报之一族）。
    """
    (tmp_path / "af_service.py").write_text("def snapshot(store=None):\n    ...\n", encoding="utf-8")
    (tmp_path / "caller.py").write_text("""
def handler(store):
    return store.snapshot(["light.x"])
""", encoding="utf-8")
    assert _module().check(tmp_path)[0] == []


def test_same_name_in_other_module_is_not_flagged(tmp_path):
    """按 (模块, 函数名) 解析：caller 自己模块里的同名函数没有 store，就不该被邻模块的签名判红。"""
    (tmp_path / "af_service.py").write_text(TARGETS, encoding="utf-8")
    (tmp_path / "caller.py").write_text("""
def live_run(graph):
    ...


def handler(store, args):
    return live_run(args["graph"])
""", encoding="utf-8")
    assert _module().check(tmp_path)[0] == []


def test_functions_without_store_param_are_never_flagged(tmp_path):
    findings, _ = _check(tmp_path, """
from . import af_service as svc


def handler(store, args):
    return svc.no_store(args["graph"])
""")
    assert findings == []


def test_bare_call_resolves_to_own_module(tmp_path):
    """同模块的模块级函数：裸名调用也要判——漏传的形状和跨模块调用完全一样。"""
    (tmp_path / "caller.py").write_text("""
def live_run(graph, store=None):
    ...


def handler(args):
    return live_run(args["graph"])
""", encoding="utf-8")
    findings = _module().check(tmp_path)[0]
    assert len(findings) == 1
    assert "caller.live_run" in findings[0]


def test_double_star_unpack_is_not_judged(tmp_path):
    """`f(**opts)` 里字典的键名静态读不出 ⇒ 放过（和位置解包同档）。

    这是本门**有意**的漏判面：宁少判不误判。今天 src 里没有"具名目标函数 + `**` 解包"的
    形状（逐条对过：`self.mcp.call(tool, **kw)` 接收者判不出、`fn(*a, **kw)` 是变量名），
    所以这道口子没有实际让出一个真漏传。
    """
    findings, _ = _check(tmp_path, """
from . import af_service as svc


def handler(args, opts):
    return svc.live_run(args["graph"], **opts)
""")
    assert findings == []


# ── 现场豁免：必须带理由，且删掉标记就得回红 ─────────────────────────

def test_exemption_with_reason_clears(tmp_path):
    findings, exempted = _check(tmp_path, """
from . import af_service as svc


def handler(args):
    return svc.live_run(args["graph"])  # store-injection: exempt(纯仿真面，拿不到存储根)
""")
    assert findings == []
    assert exempted == 1


def test_exemption_on_line_above_clears(tmp_path):
    findings, exempted = _check(tmp_path, """
from . import af_service as svc


def handler(args):
    # store-injection: exempt(纯仿真面)
    return svc.live_run(args["graph"])
""")
    assert findings == []
    assert exempted == 1


def test_exemption_without_reason_stays_red(tmp_path):
    """空理由的豁免和没有门禁没区别，所以不认。"""
    findings, exempted = _check(tmp_path, """
from . import af_service as svc


def handler(args):
    return svc.live_run(args["graph"])  # store-injection: exempt()
""")
    assert len(findings) == 1
    assert exempted == 0


def test_unrelated_comment_does_not_clear(tmp_path):
    findings, _ = _check(tmp_path, """
from . import af_service as svc


def handler(args):
    # 这里不传 store 是因为……（没写成 exempt(理由) 的形状）
    return svc.live_run(args["graph"])
""")
    assert len(findings) == 1


# ── HEAD 全仓：门必须干净，且豁免点数量是要被看见的 ──────────────────

def test_repo_src_is_clean():
    """真缺陷已经修掉（`_t_health` 递 store、`_t_live` 递 store），HEAD 上门禁全绿。"""
    findings, files, exempted = _module().check(ROOT / "src")
    assert findings == []
    assert files >= 90                      # 扫的是整个 src，不是空树假绿
    assert exempted == 2                    # 目前只有 af_vhass/dual_track 那一一对拍调用
