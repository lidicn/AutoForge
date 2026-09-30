# -*- coding: utf-8 -*-
"""意图解析的设备目录预筛 + heuristic-first（Token 优化 ①/④）回归测试。

对应 docs/audit/AutoForge架构评估与Token优化.md 的两项主张：

* 「NEW 项」——原 ``IntentParser.parse`` 里 ``list(catalog)[:120]``
  **无排序、无界、无截断回报**：相关设备若排在第 121 位之后就被静默丢弃，
  且无论请求多简单都恒定付出 ~3984 token。
* 「4.3 ④」——``HeuristicIntentParser`` 实测能独立处理约 30% 常见语句，
  但原实现是「LLM 优先、失败才 fallback」，应改为启发式先行。

锁定的性质：
1. 相关设备一定能进入候选窗口（不再被位置截断淘汰）；
2. 候选条数有界，且 total / shown 如实回报；
3. 裁剪情况在 prompt 里透明告知 LLM；
4. 简单短句跳过 LLM（token 成本 0）；
5. **含阈值/时序等复杂语义的句子必须仍交给 LLM**——宁可不省 token 也不能丢语义。
"""
from __future__ import annotations

import json

from autoforge.af_orchestrator import (
    DEFAULT_INTENT_CATALOG_LIMIT,
    IntentParser,
    _select_intent_catalog,
)


class _FakeLLM:
    """最小 LLM 替身：只需满足 ``IntentParser`` 用到的 ``complete(prompt, system=)`` 契约。"""

    def __init__(self, reply: str | None = None) -> None:
        self.calls: list[dict] = []
        self._reply = reply or json.dumps({"intent_kind": "recommend", "area": None})

    def complete(self, prompt: str, system: str | None = None) -> str:
        self.calls.append({"prompt": prompt, "system": system})
        return self._reply


def _entity(eid: str, name: str = "", area: str = ""):
    return {"entity_id": eid, "name": name, "area": area}


def _big_catalog(size: int, target_index: int) -> list[dict]:
    """构造大目录：填充项为 switch 域且无房间，目标灯藏在 ``target_index`` 之后。"""
    items: list[dict] = []
    for i in range(size):
        if i == target_index:
            items.append(_entity("light.desk_target", "书房台灯", "书房"))
        else:
            items.append(_entity(f"switch.filler_{i}", f"填充设备{i}", ""))
    return items


# ---------------------------------------------------------------- ① 目录预筛

def test_relevant_device_survives_truncation():
    """相关设备即便排在 catalog 很后面，也必须进入候选窗口。

    原实现取前 120 条，第 301 位的目标会被静默丢弃——这是本次修复的核心。
    """
    catalog = _big_catalog(400, target_index=300)
    rows, total, shown = _select_intent_catalog(catalog, "把书房台灯打开", limit=10)

    assert total == 400
    assert shown == 10
    assert any(r["entity_id"] == "light.desk_target" for r in rows), (
        "相关设备必须进入候选窗口（原实现会因位置靠后被静默截断）"
    )


def test_catalog_is_bounded():
    """候选条数有界：无论全屋多少实体，送入 LLM 的都不超过设定上限。"""
    catalog = _big_catalog(500, target_index=499)
    rows, total, shown = _select_intent_catalog(
        catalog, "随便说句话", limit=DEFAULT_INTENT_CATALOG_LIMIT
    )

    assert (total, shown) == (500, DEFAULT_INTENT_CATALOG_LIMIT)
    assert len(rows) <= DEFAULT_INTENT_CATALOG_LIMIT


def test_empty_catalog_is_safe():
    rows, total, shown = _select_intent_catalog([], "打开灯", limit=10)
    assert (rows, total, shown) == ([], 0, 0)


def test_prompt_reports_truncation_transparently():
    """裁剪不再是静默的：prompt 里如实告知全屋总数与实际候选数。"""
    llm = _FakeLLM()
    # 关掉 heuristic-first 以隔离目录预筛行为
    parser = IntentParser(llm, catalog_limit=5, heuristic_first=False)

    parser.parse("把书房台灯打开", _big_catalog(300, target_index=299))

    assert len(llm.calls) == 1
    prompt = llm.calls[0]["prompt"]
    assert "全屋共 300 条" in prompt, "应回报全屋实体总数"
    assert "下列 5 条为候选" in prompt, "应回报实际入选条数"


# ------------------------------------------------------------ ④ heuristic-first（默认关闭，仅显式 opt-in）

def test_heuristic_first_opt_in_skips_llm():
    """显式开启时，简单短句直接走启发式，token 成本归零。"""
    llm = _FakeLLM(reply=json.dumps({"intent_kind": "build", "area": "客厅"}))
    parser = IntentParser(llm, heuristic_first=True)

    out = parser.parse("打开书房的灯", _big_catalog(50, target_index=49))

    assert llm.calls == [], "显式开启时，简单句应跳过 LLM（0 token）"
    assert out["intent_kind"] == "build"
    assert out["area"] == "书房"
    assert out["actions"], "应产出可执行动作"


def test_default_preserves_llm_first():
    """默认（关闭 heuristic_first）必须保持 LLM 优先：带目录时由 LLM 绑定真实实体，
    不能因启发式合成占位 ref 而自作主张。这正是对原有行为零回归的保证。"""
    canned = json.dumps({"intent_kind": "build", "area": "书房", "actions": [{"action": "ha.light.turn_on"}]})
    llm = _FakeLLM(reply=canned)
    parser = IntentParser(llm)  # heuristic_first 默认 False

    out = parser.parse("打开书房的灯", _big_catalog(50, target_index=49))

    assert len(llm.calls) == 1, "默认必须仍调用 LLM 以绑定真实实体"
    assert out.get("actions")


def test_threshold_sentence_still_uses_llm():
    """含阈值的句子必须交给 LLM——启发式表达不了比较条件，不能因省 token 丢语义。"""
    canned = json.dumps({"intent_kind": "build", "area": "书房", "conditions": [{"op": "gt"}]})
    llm = _FakeLLM(reply=canned)
    parser = IntentParser(llm, heuristic_first=True)

    out = parser.parse("书房温度高于27度就开空调", _big_catalog(50, target_index=49))

    assert len(llm.calls) == 1, "含阈值/数字的句子必须仍交给 LLM"
    assert out.get("conditions"), "LLM 产出的条件树不应被丢弃"


def test_heuristic_miss_falls_back_to_llm():
    """启发式拿不到 hands-on 结论（无设备类命中）时，必须回落到 LLM。"""
    canned = json.dumps({"intent_kind": "recommend", "area": None})
    llm = _FakeLLM(reply=canned)
    parser = IntentParser(llm, heuristic_first=True)

    out = parser.parse("帮我看看还能做点啥", _big_catalog(20, target_index=19))

    assert len(llm.calls) == 1
    assert out.get("intent_kind") == "recommend"


def test_llm_failure_still_falls_back():
    """LLM 抛异常时仍能用启发式兜底（保持原有容错语义不变）。"""

    class _BrokenLLM:
        def complete(self, prompt: str, system: str | None = None) -> str:
            raise RuntimeError("llm down")

    parser = IntentParser(_BrokenLLM())

    out = parser.parse("打开书房的灯", _big_catalog(20, target_index=19))

    assert out["intent_kind"] == "build"
    assert out["actions"], "LLM 失败时应回落到启发式结果"
