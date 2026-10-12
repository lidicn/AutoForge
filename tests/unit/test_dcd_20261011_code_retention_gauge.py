"""裁定 `20261011-AF过期授权码与触发历史保留策略` §4 验收 1/2/3 的判据腿（AF 任务 #132）。

那半边的落码（`af_premiere.py` 的只读 `stats()`）在 `c63fe4e` 就进去了，但**没有一条腿**——
按仓内归档判据（"已修＋判据"才算收口），本文件补的就是那三格验收：

1. **判重口径**：新码对**全量** `_by_code` 判重，即与已消费码、已过期码也不重号（裁定 §2.1 撤回过
   "只对活跃集判重"的初稿主张，A1／A2 两枚钉住那次撤回）。
2. **`_by_code` 只增不减**：任何删除／裁剪路径出现即红（B1 行为面、B2 源码面各一枚）。
3. **读数可读且零副作用**：能取到「已发码数／码空间上限／占用率」，连续读两次 `_by_code` 与落盘文件
   逐字节不变（C1 行为面、C2 源码面）。

⛔ 本批不接健康面／metrics——那要动 `af_api.py:501` 或 `af_mcp.py:143` 两个调用面之一，前者是并发在途
的登录线（AF 任务 #79）。这一格登记在 DCD 回执的「未落项」里，不在此文件里假装已落。
"""
from __future__ import annotations

import inspect
import json
import re

import pytest

from autoforge import af_premiere
from autoforge.af_premiere import PremiereStore


class _Clock:
    """可注入时间源：三态（活跃／已消费／已过期）要靠拨表造出来，不能靠真表等。"""

    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def store(tmp_path):
    clock = _Clock()
    return PremiereStore(now=clock, ttl_s=300, path=str(tmp_path / "premiere.json")), clock, tmp_path


def _three_states(st: PremiereStore, clock: _Clock) -> list[str]:
    """造出裁定 §4.1 点名的三态：已过期码、已消费码、活跃码。册面总数 4 枚。"""
    expired = st.issue("sha-expired")            # 之后被拨表越过有效期
    consumed = st.issue("sha-consumed")          # 真消费一次（consumed_at 落定）
    st.consume(consumed, "sha-consumed")
    clock.t += 10_000.0
    st.issue("sha-kept-alive", ttl_s=600)        # 第 4 枚：在册且活跃
    return [expired, consumed, st.issue("sha-another-active")]


def _free_code(st: PremiereStore) -> str:
    """取一枚确定不在册的码，作为判重行为的对照物（不猜、不写死）。"""
    for i in range(af_premiere._CODE_SPACE):
        code = f"{i:06d}"
        if code not in st._by_code:
            return code
    raise AssertionError("码空间已被占满，本腿的对照物取不出来")


# --------------------------------------------------------------------------------------
# 验收 1：判重对全量集合（已消费／已过期也不得重号）
# --------------------------------------------------------------------------------------


def test_new_code_skips_consumed_and_expired_codes(store, monkeypatch):
    st, clock, _tmp = store
    consumed_or_expired = _three_states(st, clock)
    assert all(c in st._by_code for c in consumed_or_expired), "三态里有一枚不在册面上，后面的判重就没有对照物"

    fresh = _free_code(st)
    # 生成器按顺序取到这三枚"已退出可用面"的码 ⇒ 只有对全量册面判重才会全部跳过、落到第四枚
    draws = [int(c) for c in consumed_or_expired] + [int(fresh)]
    monkeypatch.setattr(af_premiere.secrets, "randbelow", lambda _n: int(draws.pop(0)))

    assert st._gen_unique_code_locked() == fresh
    assert draws == [], "生成器提前返回了 ⇒ 它判重的不是全量册面，而是被裁过的某一档（裁定 §2.1 撤回的主张）"


def test_dedup_predicate_reads_the_whole_registry_not_an_active_subset():
    """负控：把判重改成"只对活跃集"（`consumed_at is None`／`expires_at > now`）必须被这条扫出。"""
    src = inspect.getsource(PremiereStore._gen_unique_code_locked)
    found = re.search(r"if\s+code\s+not in\s+([\w.]+):", src)
    assert found is not None, "判重那行换了形状，本腿的锚点需要按现读重钉（不是放行）"
    assert found.group(1) == "self._by_code", f"判重集合读成 {found.group(1)}——裁定 §3 明令仍须对全量 _by_code"
    for banned in ("consumed_at", "expires_at", "active"):
        assert banned not in src, f"判重里出现 {banned!r}＝把已消费／已过期码重新放回可用面"


# --------------------------------------------------------------------------------------
# 验收 2：_by_code 只增不减
# --------------------------------------------------------------------------------------


def test_registry_never_shrinks_across_consume_and_expiry(store):
    st, clock, tmp = store
    gone = _three_states(st, clock)
    assert len(st._by_code) == 4, "签发四枚之后册面应当恰好四枚、一枚不少"

    clock.t += 10_000.0
    st.consume(gone[2], "sha-another-active")
    st.save()
    st.load()
    assert len(st._by_code) == 4, "消费／过期／load 往返之后册面缩了＝出现删除路径"

    # 真重启：另起一个实例从同一份盘恢复。只看上面那一对 save/load 抓不住"启动即过滤"。
    restarted = PremiereStore(now=clock, ttl_s=300, path=str(tmp / "premiere.json"))
    assert len(restarted._by_code) == 4, "重启从盘上恢复之后册面缩了＝启动即裁剪"
    assert restarted.stats()["issued_count"] == 4


def test_no_deletion_or_pruning_path_exists_in_the_module():
    """负控：任何 `del`／`pop`／`clear`／重绑，或过滤过的写盘，一出现即红。"""
    src = inspect.getsource(af_premiere)
    for banned in (
        "del self._by_code",
        "self._by_code.pop",
        "self._by_code.clear",
        "self._by_code = {",
        "self._by_code =",
    ):
        assert banned not in src, f"码面出现删除／重绑路径 {banned!r}——本裁明写不删任何东西"
    save_src = inspect.getsource(PremiereStore.save)
    assert "self._by_code.values()" in save_src, "落盘写的必须是全量册面，过滤过的写盘就是裁剪"
    load_src = inspect.getsource(PremiereStore.load)
    for banned in ("consumed_at", "expires_at"):
        assert banned not in load_src, f"load 里读 {banned!r}＝重启即按状态裁剪"


def test_ruling_forbids_a_new_pruned_code_and_none_exists():
    """裁定 §3 的 ⛔：不得新增 `PREMIERE_CODE_PRUNED` 具名码，也不得长出裁剪入口。"""
    src = inspect.getsource(af_premiere)
    for banned in ("PREMIERE_CODE_PRUNED", "def prune", "_pruned"):
        assert banned not in src, f"出现裁定明令禁止的形状 {banned!r}"
    assert not hasattr(PremiereStore, "prune")


# --------------------------------------------------------------------------------------
# 验收 3：读数可读、零副作用、码空间单一真源
# --------------------------------------------------------------------------------------


def test_stats_is_readable_and_pure(store):
    st, clock, tmp = store
    _three_states(st, clock)
    payload_path = tmp / "premiere.json"
    first = st.stats()
    before_keys = sorted(st._by_code)
    before_bytes = payload_path.read_bytes()
    second = st.stats()

    assert first == second, "同一份册面读两次应当同值——读数本身不许推动状态"
    assert sorted(st._by_code) == before_keys, "stats() 动了 _by_code"
    assert payload_path.read_bytes() == before_bytes, "stats() 写了盘"
    assert first["issued_count"] == 4
    assert first["code_space"] == af_premiere._CODE_SPACE == 10 ** 6
    assert first["usage_ratio"] == pytest.approx(4 / 10 ** 6)

    first["issued_count"] = 999
    assert st.stats()["issued_count"] == 4, "返回的字典必须是新建的，改它不得回写册面"

    st.issue("sha-after-read")
    third = st.stats()
    assert third["issued_count"] == 5, "册面加了一枚而读数不动＝读数回住在缓存里，不在读当前状态"
    assert third["usage_ratio"] == pytest.approx(5 / 10 ** 6)


def test_stats_makes_zero_write_calls(store, monkeypatch):
    """行为面负控：stats() 只要碰一次落盘就红——"写了一次内容相同的盘"在字节面上看不出来。"""
    st, clock, _tmp = store
    _three_states(st, clock)
    calls: list[str] = []
    monkeypatch.setattr(af_premiere, "atomic_write_text",
                        lambda path, *a, **k: calls.append(str(path)))
    st.stats()
    st.stats()
    assert calls == [], f"stats() 触发了 {len(calls)} 次落盘＝读数在写状态"


def test_stats_does_not_write_or_relock_the_registry():
    src = inspect.getsource(PremiereStore.stats)
    for banned in ("self.save", "self.load", "_by_code[", "_by_code.pop", "del "):
        assert banned not in src, f"stats() 里出现 {banned!r}＝读数在改状态"


def test_stats_shape_is_json_safe_for_any_face(store):
    """形态由 AF 定（裁定 §3），所以钉的是"任何面都接得住"那一格：键固定、值全为数字、可序列化。"""
    st, _clock, _tmp = store
    payload = st.stats()
    assert set(payload) == {"issued_count", "code_space", "usage_ratio"}
    assert all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in payload.values())
    assert json.loads(json.dumps(payload)) == payload
