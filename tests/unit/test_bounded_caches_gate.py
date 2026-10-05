"""有界缓存注册表门禁必须"能变红"（铁律 #8），红的必须是**下一个没登记的容器**，而不是已知那两个。

起因：第六轮审计 §三 把"新增有界缓存必须同时给 TTL 与硬上限，并在测试里断言纯写不读也被回收"
记成"约定 + 门禁可见"，可约定的两半里当时没有任何能判红的东西（只活在那份审计正文里）。
裁定 20261004 §一 3 判 B（注册表式）而非 C（统一基类）：天真静态口径首跑命中 76 个增长容器、
真正两条腿齐全的只有 2 个 ⇒ 硬扫"无界"只会得到一堆永久红加一张豁免表（而豁免表正是 §二之十九
扫掉的那族"第二份名单"）。所以本门不猜"有没有界"，只核对 `af_bounded_caches.py` 说没说实话。

五条判据各自单独可红（A 腿的名字不在模块里 / B 测试 id 不被收集 / C 新增容器没登记 /
D 固定键表的理由没写进代码、基线里的容器已消失 / E 死写容器），射程读不成退 2 不退 1。
反空洞：注册表为空、扫到 0 个容器、注册表读不出 ⇒ 一律 exit 2——"没有发现"不等于"没有问题"。

判据 E（稳定性审计 §六 P1）是这一批新加的第五条：只写不读、又没登记裁剪的增长容器判红。
它与判据 C 的区别是 C 问"有没有登记"（基线里的 73 个全算登记过），E 问"有没有人读"
——`node_visits` 那一族正是躺在基线口径够不着的地方被删掉的。
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


def test_b_scope_message_names_the_missing_precondition(monkeypatch):
    """射程消息要自己说出原因。run 68 在 CI 的 `quality-gates` 作业撞的是"那个作业没装 pytest"，
    原因写在 stderr、stdout 全空，而第一版只截 `stdout[-300:]` ⇒ 红消息停在冒号后面什么都没有。
    本机不制造"没装 pytest"的环境（也不许 pip install），这里替换的只是子进程返回值——要判红的
    是消息组装那一半。
    """
    gate = _gate()

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "/usr/bin/python3: No module named pytest\n"

    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: _Proc())
    findings, errs = gate.check_tests(
        [{"module": "af_demo", "attr": "x", "cap": "c", "ttl": "t", "trim": "r",
          "test": REAL_TEST_ID}]
    )
    assert findings == []
    assert any("没有 pytest" in e for e in errs), errs


def test_b_scope_message_carries_whichever_stream_has_the_text(monkeypatch):
    """非"缺 pytest"那一半也不能丢：rc≠0 时贴出有字的那一路，两路都空要说"均为空"，
    否则下一个人读到的还是一条没有内容的冒号。"""
    gate = _gate()

    class _Proc:
        def __init__(self, out, err):
            self.returncode, self.stdout, self.stderr = 2, out, err

    monkeypatch.setattr(gate.subprocess, "run",
                        lambda *a, **k: _Proc("", "ImportError: cannot import name 'zzz'"))
    _f, errs = gate.check_tests([{"module": "m", "attr": "x", "cap": "c", "ttl": "t",
                                  "trim": "r", "test": REAL_TEST_ID}])
    assert any("ImportError" in e for e in errs), errs

    monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: _Proc("", ""))
    _f, errs = gate.check_tests([{"module": "m", "attr": "x", "cap": "c", "ttl": "t",
                                  "trim": "r", "test": REAL_TEST_ID}])
    assert any("均为空" in e for e in errs), errs


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


# ── 判据 E：死写容器（BUG-01 那一族的防复发）────────────────────────
# 本节的口径是"按名字在全仓数读取点"（不分持有者）。更严的"只在容器所在文件里数"试过并被
# **否掉**，反例是真仓里两处跨文件读取：`af_vhass/harness.py:269` 读 `self.adapter.calls`、
# `af_executor.py:792` 读 `self.bus.emitted`——按文件数会把它们判成死写，那是假红。
# 保守方向的代价是真仓 `HAAdapter.intents` / `HTTPAdapter.intents` 被 `af_apply.py:271`
# 的同名局部变量掩护掉（本门抓不到它们，但同一批已按 BUG-01 同类收口，见
# tests/unit/test_diagnostic_ring_bounds.py）。盲区写在这里，不许当作没看见。

DEAD_WRITE_MODULE = '''
class Recorder:

    def __init__(self):
        self.log: list = []

    def record(self, item):
        self.log.append(item)
'''

READ_BACK_MODULE = '''
class Recorder:

    def __init__(self):
        self.log: list = []

    def record(self, item):
        self.log.append(item)

    def peek(self):
        return list(self.log)
'''

# `af_conflict.py:521` 的真实形状：setdefault 把已有内容交给调用方，那一次就是读取。
SETDEFAULT_MODULE = '''
class Arbiter:

    def __init__(self):
        self._release_log: dict = {}

    def note(self, key, now):
        log = self._release_log.setdefault(key, [])
        log.append(now)
        self._release_log[key] = [t for t in log if t >= now - 1]
'''

# 只有写入通道的模块：读取点收集器在这里必须数出 0（判据 E 的射程塌了要说出来）
WRITE_ONLY_MODULE = '''
class Recorder:

    def __init__(self):
        self.log: list = []

    def record(self):
        self.log.append(1)
'''

MASKING_MODULE = '''
def summarize(items):
    intents = {i for _, i in items}
    return sorted(intents)
'''


def _dead(gate, src, *, registry=frozenset(), fixed=frozenset(), markers=None):
    containers, lines, errs = gate.scan(src)
    assert errs == []
    reads, read_errs = gate.count_reads(src)
    assert read_errs == []
    return gate.check_dead_writes(containers, lines, reads, set(registry), set(fixed), markers or {})


def _only(tmp_path, **modules: str):
    """只放注册表 + 指定模块的树（`demo=""`）：判据 E 的每条腿都只对自己那份代码负责。"""
    return _fixture(tmp_path, bounded="", demo="", extra=modules)


def test_e_append_only_container_goes_red(tmp_path):
    gate = _gate()
    findings = _dead(gate, _only(tmp_path, **{"af_rec.py": DEAD_WRITE_MODULE}))
    assert any("af_rec.py::Recorder.log" in f and "死写容器" in f for f in findings), findings


def test_e_a_read_back_clears_it(tmp_path):
    """有消费者就不该判红：否则这条门的红线会被"全部加读取"这种装饰性改动绕过。"""
    gate = _gate()
    src = _only(tmp_path, **{"af_rec.py": READ_BACK_MODULE})
    findings = _dead(gate, src)
    assert findings == [], findings
    # 同一份树把读取那半删掉就必须红：证明绿的是那句 `list(self.log)`，不是这条门没射程
    src2 = _only(tmp_path / "second", **{"af_rec.py": DEAD_WRITE_MODULE})
    assert _dead(gate, src2) != []


def test_e_setdefault_counts_as_a_read(tmp_path):
    """真仓反例：`af_conflict.py:521` 的 `ConflictArbiter._release_log` 只被 `setdefault` 取出来消费。
    把它数成"只写"会给一条不存在的泄漏判红——`setdefault`/`pop`/`popitem` 会把已有内容交回调用方。
    """
    gate = _gate()
    assert _dead(gate, _only(tmp_path, **{"af_arb.py": SETDEFAULT_MODULE})) == []


def test_e_method_name_is_not_a_read_of_a_container_with_that_name(tmp_path):
    """`other.update()` 里的 `update` 是方法名，不是对同名容器的读取：把它数成读取，
    `self.update: list = []` 这种死写容器就蒙混过关了（本批首版正是这么漏的）。
    """
    gate = _gate()
    module = '''
class Bag:

    def __init__(self):
        self.update: list = []

    def put(self, other):
        self.update.append(other)
        other.update()
'''
    src = _only(tmp_path, **{"af_bag.py": module})
    findings = _dead(gate, src)
    assert any("af_bag.py::Bag.update" in f for f in findings), findings


def test_e_baseline_does_not_exempt_dead_writes(tmp_path):
    """基线冻的是"有没有界"，冻不掉"这份数据根本没人读"。

    HEAD 上被本判据抓到的 `af_scheduler.py::Scheduler.rejections` 正是一条**基线内**的容器：
    把它按基线放过，这条门对 BUG-01 那一族就等于没装。
    """
    gate = _gate()
    src = _only(tmp_path, **{"af_rec.py": DEAD_WRITE_MODULE})
    containers, lines, _errs = gate.scan(src)
    reads, _e = gate.count_reads(src)
    key = "af_rec.py::Recorder.log"
    assert key in containers
    findings = gate.check_dead_writes({key}, lines, reads, set(), set(), {})
    assert any("死写容器" in f for f in findings), findings
    assert "baseline" not in gate.check_dead_writes.__code__.co_varnames


def test_e_registered_cache_needs_no_reader(tmp_path):
    """注册表项带 trim（裁剪本身要读容器），所以"全仓没人读"对已登记的缓存不判红。"""
    gate = _gate()
    src = _only(tmp_path, **{"af_rec.py": DEAD_WRITE_MODULE})
    containers, lines, _errs = gate.scan(src)
    reads, _e = gate.count_reads(src)
    key = "af_rec.py::Recorder.log"
    assert gate.check_dead_writes(containers, lines, reads, {key}, set(), {}) == []
    assert gate.check_dead_writes(containers, lines, reads, set(), set(), {}) != []


def test_e_shared_name_in_another_module_masks_the_finding(tmp_path):
    """钉住盲区（口径按名字数，不分持有者）：别的模块里一个同名局部变量就能掩护它——
    真仓的 `HAAdapter.intents` / `HTTPAdapter.intents` 就是被 `af_apply.py:271` 的同名局部变量
    掩护掉的。记这一条是为了让"把它改严"的人先撞上反例（按文件数会把 `af_vhass/harness.py:269`
    跨文件读的 `calls`、`af_executor.py:792` 跨文件读的 `emitted` 判成假红），而不是撞上之后随手放宽。
    """
    gate = _gate()
    src = _only(tmp_path, **{
        "af_rec.py": DEAD_WRITE_MODULE.replace("self.log", "self.intents")
                                       .replace("Recorder", "Ring").replace("item", "thing"),
        "af_sum.py": MASKING_MODULE,
    }
    )
    containers, lines, _errs = gate.scan(src)
    assert containers == {"af_rec.py::Ring.intents"}
    reads, _e = gate.count_reads(src)
    assert reads.get("intents", 0) >= 1, "掩护用的读取点必须真被数到，否则这条自证是空的"
    assert gate.check_dead_writes(containers, lines, reads, set(), set(), {}) == []


def test_e_every_container_dead_collapses_the_range(tmp_path, capsys):
    """反空洞第二档：未登记容器**全部**被判成死写 ⇒ 是读取口径塌了，不是代码同时出问题。

    本门首版的手搓探针就是把 81 个容器数成 65 个"死写"的（两个方向各错一次：把 append 的
    接收者数成读取、又把模块级 Name 读取整个漏掉）。三棵树做实验：3 个全死 ⇒ 2；2 个全死 ⇒ 1。
    """
    gate = _gate()
    three = _fixture(tmp_path / "t3", bounded=_entry(), extra={
        f"af_r{i}.py": DEAD_WRITE_MODULE.replace("Recorder", f"Recorder{i}") for i in (1, 2, 3)
    })
    containers, _lines, errs = gate.scan(three)
    # af_demo.py 那份是已登记的（注册表项带 trim，不参与 E）；未登记的三个必须全部被判死写
    assert errs == [] and len(containers) == 4, containers
    assert gate.main([str(three)]) == 2
    assert "全部判成死写" in capsys.readouterr().out

    two = _fixture(tmp_path / "t2", bounded=_entry(), extra={
        f"af_r{i}.py": DEAD_WRITE_MODULE.replace("Recorder", f"Recorder{i}") for i in (1, 2)
    })
    assert gate.main([str(two)]) == 1
    out2 = capsys.readouterr().out
    assert "全部判成死写" not in out2 and "死写容器 af_r1.py" in out2


def test_e_empty_read_map_collapses_the_range(tmp_path, capsys, monkeypatch):
    """收集器数出 0 个读取点 ⇒ 退 2。真造不出一棵"有容器却一个读取点都没有"的树（连 `self`
    都是 Load），所以这一档替换的只是收集器的返回值——要判红的是"塌了要说，不许保持沉默"。
    """
    gate = _gate()
    src = _fixture(tmp_path, bounded=_entry())
    monkeypatch.setattr(gate, "count_reads", lambda _src: ({}, []))
    assert gate.main([str(src)]) == 2
    assert "数出 0 个读取点" in capsys.readouterr().out


def test_e_main_goes_red_on_a_dead_write_container(tmp_path, capsys):
    """端到端：判据 C 与 E 各自单独可红——同一个未登记的死写容器两条都响，说明 E 不是 C 的回声。

    注册表那份 `_entry()`（已登记的 `af_demo.py::Cache._CACHE`）留着：一是空名单会先撞上
    "注册表是空的"那档射程退 2，二是它同时是 E 的对照组——已登记的那条不响，未登记的这条响。
    """
    gate = _gate()
    src = _fixture(tmp_path, bounded=_entry(), extra={"af_rec.py": DEAD_WRITE_MODULE})
    assert gate.main([str(src)]) == 1
    out = capsys.readouterr().out
    assert "新增增长容器 af_rec.py::Recorder.log" in out
    assert "死写容器 af_rec.py::Recorder.log" in out
    assert "死写容器 af_demo.py::Cache._CACHE" not in out
    assert "共 2 处判红" in out


# ── 射程自证：读不出一律 2 ───────────────────────────────────────────
def test_scope_repo_baseline_does_not_leak_into_another_tree(tmp_path, capsys):
    """基线是本仓那一份扫描的冻结快照：拿它判另一棵树，整份"已经不存在"会把 1 条真红埋掉。"""
    gate = _gate()
    src = _fixture(tmp_path, bounded=_entry(),
                   extra={"af_other.py": DEMO_MODULE.replace("_CACHE", "_BOX")})
    assert gate.main([str(src)]) == 1
    out = capsys.readouterr().out
    assert out.count("[有界缓存] 新增增长容器") == 1
    assert "已经不存在" not in out
    # 判据 E 上线后，那个未登记容器同时是死写容器（两条判据各自单独响，不是回声）
    assert out.count("[有界缓存] 死写容器") == 1
    assert "共 2 处判红" in out


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
    """钉住本批实测：注册表 2 / 固定键 2 / 扫到 75 / 基线 73。

    数字变了只有两种可能：新增了一个容器（那要走登记或豁免），或者有人动了基线。两者都不该
    悄悄发生——第六轮审计那句"四处现状已是两条腿"就是靠这种核对才没被本门照抄成假账。

    75/73 是**已提交树**的读数：稳定性批次删掉 `af_executor.py::NodeExecutor.node_visits`
    （一条写了却没人读的死代码容器）后，扫到数与基线各减 1。工作树里若躺着未提交的 WIP 模块，
    扫到数会比 75 更大而基线仍是 73——那种红的意思是"新容器没登记"，不是这行数字错了，
    登记处置归那一批自己，不许靠挪动这里的数字把它抹平。
    """
    gate = _gate()
    src = ROOT / "src" / "autoforge"
    bounded, fixed, errs = gate.read_registry(src)
    containers, _lines, scan_errs = gate.scan(src)
    assert errs == [] and scan_errs == []
    assert len(bounded) == 2 and len(fixed) == 2
    assert len(containers) == 75 and len(gate.BASELINE) == 73

    registered = {gate._registry_key(e["module"], e["attr"]) for e in bounded}
    assert registered <= containers, "注册表指向的容器扫不到：那条登记是给空气盖章"
    assert registered.isdisjoint(gate.BASELINE), "已登记的容器不许同时躺在基线里（两份口径各说各话）"


def test_real_repo_has_no_dead_write_container():
    """判据 E 在已提交树上的读数必须是 0——不是"这条门没射程"，是它扫过、找到了、已收口。

    本批收口的三处：`HAAdapter.intents`、`HTTPAdapter.intents`（这两处 E 因同名局部变量掩护抓不到，
    按同一形状一并封顶）、`Scheduler.rejections`（HEAD 上被 E 当场抓到的一处）。
    外加 `tests/unit/test_diagnostic_ring_bounds.py` 里"纯写不读也被回收"的断言。
    """
    gate = _gate()
    src = ROOT / "src" / "autoforge"
    bounded, fixed, _errs = gate.read_registry(src)
    containers, lines, _scan_errs = gate.scan(src)
    reads, read_errs = gate.count_reads(src)
    assert read_errs == []
    dead = gate.dead_write_keys(
        containers, lines, reads,
        {gate._registry_key(e["module"], e["attr"]) for e in bounded},
        {gate._registry_key(e["module"], e["attr"]) for e in fixed},
        gate._exempt_markers(src),
    )
    assert dead == set(), f"死写容器没收口：{sorted(dead)}"
    for token, path in (("INTENTS_MAX", "af_adapters/ha.py"), ("INTENTS_MAX", "af_adapters/http.py"),
                        ("REJECTIONS_MAX", "af_scheduler.py")):
        assert token in (src / path).read_text(encoding="utf-8"), f"{path} 里那条封顶腿不见了"

