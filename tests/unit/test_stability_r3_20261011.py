"""稳定性与功能性第三轮审计（`docs/audit/AutoForge_稳定性与功能性审计报告_20261011.md`）的判据腿。

本文件只盖**窗内可落**的七条；F-01／F-02（`af_api.py` 鉴权回归）与 F-19／F-20／F-21（`ui-user-mimo`）
属并发登录线在途文件，按归属排在 #79 窗口之后，判据不在此处。

每条都要双向：注入缺陷的形状必须判红，全绿夹具必须判绿（反空集腿另列）。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from autoforge.af_config import Config
from autoforge.af_insight_queue import UNREADABLE_MAX, InsightQueue
from autoforge.af_ir import Graph, load_automation
from autoforge.af_proposal import _ir_writes_devices
from autoforge.af_runtime import build_runtime
from autoforge.af_scheduler import _PendingFor

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "autoforge"
TESTS = REPO / "tests"


# ── F-03：测试不许往全局环境里写开关（污染源）──────────────────────────────

# 拼出来而不是写成字面量：这条腿自己扫 tests/，写成字面量就是自踩。
_BANNED = ("os.environ" + "[", "os.environ" + ".setdefault")
# 只放行"写了会还原"的那一份：它自己把值存下来、teardown 逐条 pop/set 回去。
_SAVE_RESTORE_ALLOWED = {"test_sse_pair_request_stream.py"}


def _test_py_files() -> list[Path]:
    return sorted(p for p in TESTS.rglob("*.py") if "__pycache__" not in p.parts)


def _direct_env_write_sites(tree: ast.AST) -> list[int]:
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        target = node.targets[0]
        if not isinstance(target, (ast.Subscript, ast.Attribute)):
            continue
        text = ast.unparse(target)
        if any(b in text for b in _BANNED):
            hits.append(node.lineno)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _BANNED[1] in ast.unparse(node.func):
            hits.append(node.lineno)
    return sorted(hits)


def test_no_test_file_writes_os_environ_directly():
    """conftest 那段警告只有机器执行才算数。

    `test_v0_6_tags.py` 原来在夹具里 `os.environ.setdefault("AF_ALLOW_NOAUTH","1")`：monkeypatch
    不回收别人写的键，pytest 按字母序收集时这个值会一路带给后面**故意测 fail-closed** 的
    `test_v0_8_auth.py`／`test_v1_4_token_expiry.py`／`test_dcd_20261004_auth_limits.py`——
    同一份代码"单独绿、全量红"就是这么来的（现读：合跑 11 failed／单跑 9+6+31 全绿）。
    """
    offenders = []
    for py in _test_py_files():
        if py.name in _SAVE_RESTORE_ALLOWED:
            continue
        sites = _direct_env_write_sites(ast.parse(py.read_text(encoding="utf-8-sig")))
        if sites:
            offenders.append((str(py.relative_to(REPO)), sites))
    assert offenders == [], f"测试里出现直写 os.environ 的站点（会跨测试泄漏）：{offenders}"


def test_the_env_write_scan_is_not_empty():
    """反空集腿：扫描真的看见了 tests/ 下成百的文件，不是"扫无可扫"的假绿。"""
    files = _test_py_files()
    assert len(files) > 100, len(files)
    # 被放行那一份必须真在盘上、且真带还原动作——名单不是为了遮丑而存在的。
    allowed = next(p for p in files if p.name in _SAVE_RESTORE_ALLOWED)
    assert "os.environ" in allowed.read_text(encoding="utf-8")


# ── F-05：revision.json 形状不对，按"读不出来"处理，不抛穿──────────────────

@pytest.mark.parametrize("body", ["null", "42", "[]", '"x"', "{not json", "true"])
def test_misshapen_revision_file_reads_as_zero(tmp_path, body):
    """`json.loads` 对 `null`/`42`/`[]` 都返回合法 JSON，但对它们调 `.get()` 是 AttributeError。

    抛穿会一路冒出 `get_config()`（凭据访问唯一入口），表现是服务起不来、或 TTL 过期时在运行中崩。
    姊妹函数 `_load_credentials` 早就吃过这一口并装了同款守卫，`_load_revision` 是漏掉的那一半。
    """
    (tmp_path / "revision.json").write_text(body, encoding="utf-8")
    assert Config(tmp_path).connection_revision == 0


def test_a_real_revision_is_still_read(tmp_path):
    """双向：守卫不能把正常值一起吃掉（那会让连接代数永远归零，比崩更难查）。"""
    (tmp_path / "revision.json").write_text(json.dumps({"revision": 7}), encoding="utf-8")
    assert Config(tmp_path).connection_revision == 7


def test_revision_of_wrong_scalar_type_does_not_escape(tmp_path):
    (tmp_path / "revision.json").write_text(json.dumps({"revision": ["1"]}), encoding="utf-8")
    assert Config(tmp_path).connection_revision == 0


# ── F-06：`tick()` 的 for 到期分支守 enabled，且图里没这条时不炸整轮 ────────

_ON = {"id": "a1", "kind": "on",
       "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}, "for": "5s"}
_P1 = {"id": "p1", "kind": "pass"}


def _sched(enabled: bool):
    ir = {"ir_version": "0.2.1", "id": "demo", "name": "示例", "version": 1,
          "mode": "single", "enabled": enabled, "nodes": [_ON, _P1],
          "edges": [{"from": "a1", "to": "p1", "kind": "then"}]}
    rt = build_runtime(Graph([load_automation(ir)]))
    sched = rt.scheduler
    sched._still_holds = lambda pending: True  # 到期复查不是本条射程，钉成"仍成立"
    sched._pending[("demo", "a1", "binary_sensor.m")] = _PendingFor(
        automation_id="demo", node_id="a1", entity_id="binary_sensor.m", to="on",
        started_at=sched.clock.monotonic() - 999, duration=1.0, event=None,
    )
    calls: list[str] = []
    sched._try_fire = lambda *a, **k: calls.append("fired")  # type: ignore[assignment]
    return sched, calls


def test_disabled_automation_is_not_fired_by_the_for_branch():
    sched, calls = _sched(enabled=False)
    assert sched.tick() == []
    assert calls == [], "禁用的自动化被 for 到期分支触发了：handle_event／_fire_time_triggers 都守 enabled，这条路径也必须守"


def test_enabled_automation_is_still_fired_by_the_for_branch():
    sched, calls = _sched(enabled=True)
    sched.tick()
    assert calls == ["fired"], "守卫把正常路径也掐了"


def test_pending_for_a_deleted_automation_drops_instead_of_raising():
    """挂起期间自动化被从图里删掉：这里原来是 `self._by_id[id]` 直接 KeyError，会冒泡停摆整个 tick 循环。"""
    sched, calls = _sched(enabled=True)
    sched.graph = Graph([])
    assert sched.tick() == []
    assert sched._pending == {}, "到期条目必须被丢掉，不能每轮重复撞同一处"
    assert calls == []


# ── F-09：strict 静态闸必须真看得见写设备的 do 节点────────────────────────

def _ir_with(node: dict) -> dict:
    return {"automations": [{"id": "demo", "nodes": [
        {"id": "a1", "kind": "on", "trigger": {"type": "state", "entity_id": "light.x"}},
        node,
    ]}]}


def test_entity_id_form_counts_as_writing_a_device():
    assert _ir_writes_devices(_ir_with({"id": "d1", "kind": "do", "params": {"entity_id": "light.x"}}))


def test_target_form_counts_as_writing_a_device():
    for key in ("entity_id", "device_id", "area_id"):
        assert _ir_writes_devices(_ir_with({"id": "d1", "kind": "do", "params": {"target": {key: "x"}}})), key


def test_do_node_without_target_is_not_a_device_write():
    assert not _ir_writes_devices(_ir_with({"id": "d1", "kind": "do", "params": {}}))


def test_non_do_node_with_entity_is_not_a_device_write():
    assert not _ir_writes_devices(_ir_with({"id": "i1", "kind": "if", "params": {"entity_id": "light.x"}}))


def test_nodes_may_be_a_mapping_too():
    ir = {"automations": [{"id": "demo", "nodes": {"d1": {"id": "d1", "kind": "do",
                                                            "params": {"entity_id": "light.x"}}}}]}
    assert _ir_writes_devices(ir)


def test_the_two_key_lists_have_not_drifted_apart():
    """`_TARGET_PARAM_KEYS` 是手抄的，真源在 `Node.target_entities()`：漂移即红（不靠人读代码）。"""
    from autoforge.af_proposal import _TARGET_PARAM_KEYS

    src = (SRC / "af_ir" / "models.py").read_text(encoding="utf-8")
    body = src.split("def target_entities", 1)
    assert len(body) == 2, "找不到 Node.target_entities()，这条腿的对照面没了"
    segment = body[1].split("\n    def ", 1)[0]
    for key in _TARGET_PARAM_KEYS:
        assert key in segment, f"真源里不再认 {key}，但 af_proposal 还在按它判：闸的口径已漂"
    assert '"entity_id"' in segment and "params.get" in segment, segment


# ── F-13：坏文件诊断清单是环形的，不是只增不减──────────────────────────────

def _flood_bad_files(queue: InsightQueue, n: int) -> None:
    queue.pending_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (queue.pending_dir / f"{i:013d}-p{i}.json").write_text("{坏字", encoding="utf-8")


def test_unreadable_roster_is_ring_bounded(tmp_path):
    """/api/insights/pending 是周期轮询的读侧：坏文件不清理 ⇒ 每轮 append 一条、永不裁剪。"""
    q = InsightQueue(tmp_path / "q")
    _flood_bad_files(q, UNREADABLE_MAX + 20)
    q.list_pending()
    assert len(q.unreadable) == UNREADABLE_MAX
    q.list_pending()
    assert len(q.unreadable) == UNREADABLE_MAX, "第二轮轮询又把清单顶高了"


def test_unreadable_still_keeps_what_it_saw(tmp_path):
    """封顶不等于静默：清单里必须仍有可读回的诊断条目。"""
    q = InsightQueue(tmp_path / "q")
    _flood_bad_files(q, 3)
    q.list_pending()
    assert len(q.unreadable) == 3


def test_every_unreadable_append_site_trims():
    """防复发：仓内每一处 `self.unreadable.append` 所在函数体必须带裁头动作。

    同族代码有两个模块两套口径（`af_linkage_feed` 有裁、`af_insight_queue` 原来没有），
    这条把"两套口径"本身判红，而不是只钉已知的那两处。
    """
    sites = []
    for py in sorted(SRC.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            text = ast.unparse(fn)
            if "unreadable.append(" in text:
                sites.append((py.name, fn.name))
                assert "unreadable[" in text and "- UNREADABLE_MAX" in text, (py.name, fn.name)
    assert len(sites) >= 2, f"扫描只见到 {sites}：这两族至少各有一处，扫不到说明锚点写错了"


# ── F-15：读盘不得把持久化的绝对值计数器再叠一遍───────────────────────────


class _Hist:
    def history(self, entity_id: str, *, hours: int = 24):  # pragma: no cover - 载荷里没有事件
        return []


def _reload(tmp_path, payload: dict) -> dict:
    from autoforge.af_predict import MODEL_VERSION, Predictor

    payload = {"version": MODEL_VERSION, **payload}  # 版本不对会被隔离冷启动，读盘根本没发生
    first = Predictor(_Hist(), object(), tmp_path / "p")
    path = Path(first.predictions_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    p = Predictor(_Hist(), object(), tmp_path / "p")
    p._load()          # 第一次重启后读盘
    p._save()
    again = Predictor(_Hist(), object(), tmp_path / "p")
    again._load()      # 第二次重启再读一次同一份盘
    return {"once": p.stats("demo"), "twice": again.stats("demo")}


def test_load_does_not_double_count_corrupt_rows(tmp_path):
    """盘上 `dropped=100` ＋两条损坏事件：读盘只该拿到 100。

    原来三枚计数器用 `+=`，而 `_load` 内部对坏行还另外 `dropped += 1`——上一次已经计过并落盘的
    坏行这次再计一遍，坏行越多翻倍越快，`stats()` 的"丢弃率是否在恶化"因此失真。
    """
    payload = {"automations": {"demo": {
        "events": [{"nope": 1}, {"at": "不是时间"}],
        "predicted": None, "learned": 5, "dropped": 100, "pruned": 0, "skipped_predicted": 0,
    }}}
    out = _reload(tmp_path, payload)
    assert out["once"]["dropped"] == 100, out["once"]
    assert out["twice"]["dropped"] == 100, f"重启两次就从盘上叠两次：{out}"


def test_load_reads_the_persisted_counters_as_authority(tmp_path):
    """双向：等号不是把值归零——四枚计数器都得原样读回来。"""
    payload = {"automations": {"demo": {
        "events": [], "predicted": None,
        "learned": 11, "dropped": 22, "pruned": 33, "skipped_predicted": 44,
    }}}
    stats = _reload(tmp_path, payload)["once"]
    assert (stats["learned"], stats["dropped"], stats["pruned"], stats["skipped_predicted"]) == (11, 22, 33, 44)


# ── F-11：overwrite 的部分失败不许混档（整档要么全换、要么不动）────────────────

def _store_with_two_versions(tmp_path):
    from autoforge.af_store import GraphStore

    store = GraphStore(str(tmp_path / "forge"))
    ir = {"ir_version": "0.2.1", "id": "demo", "name": "示例", "version": 1,
          "mode": "single", "enabled": True, "nodes": [_ON, _P1],
          "edges": [{"from": "a1", "to": "p1", "kind": "then"}]}
    store.save(Graph([load_automation(ir)]), "demo", owner="alice")
    ir["name"] = "示例改名"
    store.save(Graph([load_automation(ir)]), "demo", owner="alice")
    return store, Graph([load_automation(ir)])


def _bundle_poisoned_at_v2(store):
    """导出后把 v2 的 graph 换成形状坏的，再重签 checksum——不重签就在 bundle 入口被整体拒了，
    走不到"逐版本校验"那一步，也就测不到混档。"""
    from autoforge.af_store import _bundle_checksum

    bundle = store.export_bundle()
    bundle["entries"][0]["versions"][1]["graph"] = {"automations": "not-a-list"}
    bundle["checksum"] = _bundle_checksum(bundle)
    return bundle


def _snapshot(store) -> dict[str, bytes]:
    return {str(p.relative_to(store.root)): p.read_bytes()
            for p in sorted(store.root.rglob("*")) if p.is_file()}


def test_overwrite_with_one_bad_version_writes_nothing(tmp_path):
    """F-11 的失败场景：已有 v1..v2 ＋ bundle 里 v2 非法 ⇒ 旧档不让位、合法版本却照写。
    修后整条跳过：`imported` 里没有它，盘上字节与 latest() 分毫不动。"""
    from autoforge.af_store import GraphStore

    store, _ = _store_with_two_versions(tmp_path)
    before, latest_before = _snapshot(store), store.latest("demo")
    report = store.import_bundle(_bundle_poisoned_at_v2(store), strategy="overwrite", owner="alice")

    assert "demo" not in report["imported"], report["imported"]
    assert any(e.get("name") == "demo" and "整档" in e.get("error", "") for e in report["errors"]), report["errors"]
    assert _snapshot(store) == before, "部分失败仍然改了盘"
    assert store.latest("demo") == latest_before
    assert GraphStore(str(tmp_path / "forge")).versions("demo") == [1, 2]


def test_overwrite_with_all_versions_valid_still_imports(tmp_path):
    """双向对照：这道闸不是把 overwrite 整体关掉——全档合法时仍要真的换掉。"""
    store, graph = _store_with_two_versions(tmp_path)
    bundle = store.export_bundle()
    store.save(graph, "demo", owner="alice")          # 让 latest() 再往前挪一格，好观察是否真被换掉
    assert store.latest("demo") == 3

    report = store.import_bundle(bundle, strategy="overwrite", owner="alice")
    assert "demo" in report["imported"], report
    assert store.latest("demo") == 2, "整档合法时 overwrite 应该把版本档换回 bundle 那一套"
