"""有界缓存注册表门禁必须"能变红"（铁律 #8），红的必须是**下一个没登记的容器**，而不是已知那两个。

起因：第六轮审计 §三 把"新增有界缓存必须同时给 TTL 与硬上限，并在测试里断言纯写不读也被回收"
记成"约定 + 门禁可见"，可约定的两半里当时没有任何能判红的东西（只活在那份审计正文里）。
裁定 20261004 §一 3 判 B（注册表式）而非 C（统一基类）：天真静态口径首跑命中 76 个增长容器、
真正两条腿齐全的只有 2 个 ⇒ 硬扫"无界"只会得到一堆永久红加一张豁免表（而豁免表正是 §二之十九
扫掉的那族"第二份名单"）。所以本门不猜"有没有界"，只核对 `af_bounded_caches.py` 说没说实话。

四条判据各自单独可红（A 腿的名字不在模块里 / B 测试 id 不被收集 / C 新增容器没登记 /
D 固定键表的理由没写进代码、基线里的容器已消失），射程读不成退 2 不退 1。
反空洞：注册表为空、扫到 0 个容器、注册表读不出 ⇒ 一律 exit 2——"没有发现"不等于"没有问题"。
"""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_bounded_caches.py"

# 一个真实存在于本仓的测试 id：判据 B 核对的是 pytest 的收集结果，不是文件里有没有那行字。
REAL_TEST_ID = "tests/unit/test_af_session_bounds.py::test_hard_cap_evicts_oldest_even_within_ttl"

DEMO_MODULE = '''
CAP = 100
TTL_S = 60.0


class Cache:

    def __init__(self):
        self._CACHE = {}

    def put(self, key, value):
        self._CACHE[key] = value

    def _trim(self):
        pass
'''

FIXED_KEY_MODULE = '''
class Counters:

    def __init__(self):
        self._stats = {"a": 0, "b": 0}

    def bump(self):
        self._stats["a"] += 1
'''


def _gate():
    spec = importlib.util.spec_from_file_location("check_bounded_caches", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _registry(bounded: str, fixed: str = "") -> str:
    return f"BOUNDED_CACHES = [\n{bounded}\n]\nFIXED_KEY_CACHES = [\n{fixed}\n]\n"


def _entry(**over: str) -> str:
    row = {"module": "af_demo", "attr": "Cache._CACHE", "cap": "CAP", "ttl": "TTL_S",
           "trim": "_trim", "test": REAL_TEST_ID}
    row.update(over)
    return "    {" + ", ".join(f'"{k}": "{v}"' for k, v in row.items() if v != "") + "},\n"


def _fixture(tmp_path: pathlib.Path, *, bounded: str, fixed: str = "",
             demo: str = DEMO_MODULE, extra: dict[str, str] | None = None) -> pathlib.Path:
    src = tmp_path / "autoforge"
    src.mkdir(parents=True, exist_ok=True)
    (src / "af_bounded_caches.py").write_text(_registry(bounded, fixed), encoding="utf-8")
    if demo:
        (src / "af_demo.py").write_text(demo, encoding="utf-8")
    for name, code in (extra or {}).items():
        (src / name).write_text(code, encoding="utf-8")
    return src


# ── 对照组：先证明"登记齐全的形状真的能绿"──────────────────────────────
def test_control_a_registered_two_legged_cache_is_green(tmp_path):
    """一个形状正确的登记必须让整条链走到绿：否则下面所有"红"都可能只是射程塌了。"""
    gate = _gate()
    src = _fixture(tmp_path, bounded=_entry())
    assert gate.main([str(src)]) == 0


def test_control_the_unregistered_container_would_be_scanned(tmp_path):
    """判据 C 的对照：同一个容器**不**登记时确实被扫到并判红——否则"登记才绿"可以只是扫不出来。"""
    gate = _gate()
    src = _fixture(tmp_path, bounded="")
    containers, _lines, errs = gate.scan(src)
    assert errs == []
    assert containers == {"af_demo.py::Cache._CACHE"}


# ── 判据 A：注册表给的那条腿必须真在模块里 ────────────────────────────
def test_a_leg_name_that_is_not_in_the_module_goes_red(tmp_path):
    gate = _gate()
    src = _fixture(tmp_path, bounded=_entry(ttl="TTL_S_BUT_RENAMED_AWAY"))
    bounded, _fixed, errs = gate.read_registry(src)
    assert errs == []
    findings = gate.check_legs(bounded, src)
    assert any("ttl" in f and "TTL_S_BUT_RENAMED_AWAY" in f for f in findings), findings


def test_a_half_written_entry_goes_red(tmp_path):
    """只写硬上限、不写 TTL ⇒ 半条约定，注册表不许给说法盖章。"""
    gate = _gate()
    src = _fixture(tmp_path, bounded=_entry(trim=""))
    bounded, _fixed, _errs = gate.read_registry(src)
    findings = gate.check_legs(bounded, src)
    assert any("缺字段 `trim`" in f for f in findings), findings


def test_a_pointing_at_a_missing_module_goes_red(tmp_path):
    gate = _gate()
    src = _fixture(tmp_path, bounded=_entry(module="af_moved_away"))
    bounded, _fixed, _errs = gate.read_registry(src)
    findings = gate.check_legs(bounded, src)
    assert any("模块不存在" in f for f in findings), findings


# ── 判据 B：测试 id 要真被 pytest 收集 ────────────────────────────────
def test_b_test_id_pointing_at_a_missing_file_goes_red(tmp_path):
    gate = _gate()
    src = _fixture(tmp_path, bounded=_entry(test="tests/unit/test_no_such_bounds.py::test_x"))
    bounded, _fixed, _errs = gate.read_registry(src)
    findings, errs = gate.check_tests(bounded)
    assert errs == []
    assert any("测试文件不存在" in f for f in findings), findings


def test_b_test_id_pytest_does_not_collect_goes_red(tmp_path):
    """文件名对、用例名是编的：`grep` 版判据会绿，收集版判据必须红。"""
    gate = _gate()
    bogus = "tests/unit/test_af_session_bounds.py::test_this_case_does_not_exist"
    src = _fixture(tmp_path, bounded=_entry(test=bogus))
    bounded, _fixed, _errs = gate.read_registry(src)
    findings, errs = gate.check_tests(bounded)
    assert errs == []
    assert any("没被收集" in f for f in findings), findings


def test_b_collect_failure_reads_as_scope_not_as_red(tmp_path):
    """收集失败（rc≠0）不能判成"这个测试没了"再退 1：那是把射程塌了说成代码有问题。"""
    gate = _gate()
    findings, errs = gate.check_tests(
        [{"module": "af_demo", "attr": "x", "cap": "c", "ttl": "t", "trim": "r",
          "test": "tests/conftest.py::nothing"}]
    )
    assert findings == []
    assert any("收集未成功" in e for e in errs), errs
    src = _fixture(tmp_path, bounded=_entry(test="tests/conftest.py::nothing"))
    assert gate.main([str(src)]) == 2


# ── 判据 C：新增增长容器必须登记 / 就地豁免 / 不在基线之外 ────────────
def test_c_new_container_without_registration_goes_red(tmp_path):
    gate = _gate()
    src = _fixture(tmp_path, bounded="")
    containers, lines, errs = gate.scan(src)
    assert errs == []
    findings = gate.check_membership(containers, set(), set(), set(), {}, lines)
    assert any("新增增长容器 af_demo.py::Cache._CACHE" in f for f in findings), findings


def test_c_in_line_exemption_is_enough_and_names_the_line(tmp_path):
    gate = _gate()
    src = _fixture(tmp_path, bounded="",
                   extra={"af_flagged.py": DEMO_MODULE.replace("_CACHE", "_BOX")})
    containers, lines, _errs = gate.scan(src)
    key = "af_flagged.py::Cache._BOX"
    assert key in containers
    findings = gate.check_membership(containers, set(), set(), set(),
                                     {f"af_flagged.py:{lines[key]}": "已裁定豁免"}, lines)
    assert not any(key in f for f in findings), findings
    assert any("af_demo.py::Cache._CACHE" in f for f in findings), findings


def test_c_baseline_shrinks_only(tmp_path):
    """基线里躺着一条已经改名的容器 ⇒ 必须判红，不许"扫不到就当放过"。"""
    gate = _gate()
    containers = {"af_demo.py::Cache._CACHE"}
    findings = gate.check_membership(containers, set(), set(),
                                     {"af_demo.py::Cache._GONE"}, {}, {})
    assert any("已经不存在" in f for f in findings), findings
    assert any("af_demo.py::Cache._CACHE" in f for f in findings), findings


# ── 判据 D：固定键表的理由必须真写进代码那一行 ────────────────────────
def test_d_reason_only_in_the_table_not_in_the_code_goes_red(tmp_path):
    """理由写在表里、没写进被豁免那一行 = 第二份名单：改名时它会静默放行。"""
    gate = _gate()
    row = '    {"module": "af_flagged", "attr": "Counters._stats", "reason": "键空间封闭"},\n'
    src = _fixture(tmp_path, bounded="", fixed=row, extra={"af_flagged.py": FIXED_KEY_MODULE})
    _bounded, fixed, errs = gate.read_registry(src)
    assert errs == [] and len(fixed) == 1
    assert gate._exempt_markers(src) == {}, "这份 fixture 里代码一行标记都没写"
    findings = gate.check_exemptions(fixed, gate._exempt_markers(src))
    assert any("找不到带" in f and "af_flagged.py" in f for f in findings), findings


def test_d_marker_in_the_code_satisfies_the_table(tmp_path):
    gate = _gate()
    code = FIXED_KEY_MODULE.replace(
        'self._stats = {"a": 0, "b": 0}',
        'self._stats = {"a": 0, "b": 0}  # bounded-cache: exempt(固定键计数器)',
    )
    src = _fixture(tmp_path, bounded="", extra={"af_flagged.py": code})
    markers = gate._exempt_markers(src)
    rows = [{"module": "af_flagged", "attr": "Counters._stats", "reason": "键空间封闭"}]
    assert gate.check_exemptions(rows, markers) == []
    other = [{"module": "af_flagged", "attr": "Counters._other", "reason": "键空间封闭"}]
    assert gate.check_exemptions(other, markers) != []


def test_d_registry_docstring_marker_is_not_counted_as_an_exemption(tmp_path):
    """注册表自己的文档必然要引用标记语法；数进"就地豁免"会让绿线报出一个虚数。"""
    gate = _gate()
    src = _fixture(tmp_path, bounded="")
    (src / "af_bounded_caches.py").write_text(
        '# 形如 # bounded-cache: exempt(理由)\n'
        + (src / "af_bounded_caches.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert gate._exempt_markers(src) == {}


# ── 射程自证：读不出一律 2 ───────────────────────────────────────────
def test_scope_repo_baseline_does_not_leak_into_another_tree(tmp_path, capsys):
    """基线是本仓那一份扫描的冻结快照：拿它判另一棵树，74 条"已经不存在"会把 1 条真红埋掉。"""
    gate = _gate()
    src = _fixture(tmp_path, bounded=_entry(),
                   extra={"af_other.py": DEMO_MODULE.replace("_CACHE", "_BOX")})
    assert gate.main([str(src)]) == 1
    out = capsys.readouterr().out
    assert out.count("[有界缓存] 新增增长容器") == 1
    assert "已经不存在" not in out
    assert "共 1 处判红" in out


def test_scope_missing_registry_file_is_two(tmp_path):
    gate = _gate()
    src = tmp_path / "autoforge"
    src.mkdir(parents=True)
    (src / "af_demo.py").write_text(DEMO_MODULE, encoding="utf-8")
    assert gate.main([str(src)]) == 2


def test_scope_non_literal_registry_is_two(tmp_path):
    gate = _gate()
    src = _fixture(tmp_path, bounded="")
    (src / "af_bounded_caches.py").write_text(
        'BOUNDED_CACHES = [build_row("af_demo")]\nFIXED_KEY_CACHES = []\n', encoding="utf-8"
    )
    assert gate.main([str(src)]) == 2


def test_scope_empty_registry_is_two(tmp_path):
    """空名单会让本门永远绿——那正是 §二之十一 记过的"正文里的约定"。"""
    gate = _gate()
    src = _fixture(tmp_path, bounded=_entry())
    (src / "af_bounded_caches.py").write_text(
        "BOUNDED_CACHES = []\nFIXED_KEY_CACHES = []\n", encoding="utf-8"
    )
    assert gate.main([str(src)]) == 2


def test_scope_zero_containers_scanned_is_two(tmp_path):
    gate = _gate()
    src = _fixture(tmp_path, bounded=_entry(), demo="")
    assert gate.main([str(src)]) == 2


# ── 真仓读数：绿线里的每个数字都要能自证 ────────────────────────────
def test_real_repo_is_green(tmp_path):
    assert _gate().main([]) == 0


def test_real_repo_measurements_are_pinned():
    """钉住本批实测：注册表 2 / 固定键 2 / 扫到 76 / 基线 74。

    数字变了只有两种可能：新增了一个容器（那要走登记或豁免），或者有人动了基线。两者都不该
    悄悄发生——第六轮审计那句"四处现状已是两条腿"就是靠这种核对才没被本门照抄成假账。
    """
    gate = _gate()
    src = ROOT / "src" / "autoforge"
    bounded, fixed, errs = gate.read_registry(src)
    containers, _lines, scan_errs = gate.scan(src)
    assert errs == [] and scan_errs == []
    assert len(bounded) == 2 and len(fixed) == 2
    assert len(containers) == 76 and len(gate.BASELINE) == 74

    registered = {gate._registry_key(e["module"], e["attr"]) for e in bounded}
    assert registered <= containers, "注册表指向的容器扫不到：那条登记是给空气盖章"
    assert registered.isdisjoint(gate.BASELINE), "已登记的容器不许同时躺在基线里（两份口径各说各话）"
