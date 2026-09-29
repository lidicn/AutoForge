"""ADM 联动契约测试 · DB→AF 的 ASK 询问队列通道（DCD v4.1 §七 + 七问裁定 第一档①）。

本文件是 **跨仓接口的机器化契约**（而非普通单测）：它把 doubao-butler（DB）轮询
AutoForge（AF）时所依赖的 ASK 通道 schema 冻结下来，进 CI 防漂移。

涉及两条端点（代码事实见 `src/autoforge/af_api.py`）：
- `GET  /api/asks/pending` ：watch 进程写出的 `pending_asks.json` sidecar，DB 每 5s 轮询发现
  挂起 ask。**无 `_read` 依赖**（设计上可供 DB 免令牌发现）。证据 `af_api.py:519`。
- `POST /api/asks/answer`  ：把人类答案写入 `answer_inbox/`，由 watch ticker 注回 runtime。
  **需 `_write` 依赖**（fail-closed：无令牌即 403）。证据 `af_api.py:684`。

契约的"真相来源"是生产者 `af_live.py:_write_asks`（ask 项形状）与 `af_ir/models.py:
AskSpec.control()`（控件元数据）。若任一生产者改了 schema 而忘了同步这里，CI 会红——
这正是 DCD 要求的"接口契约测试"杠杆：把跨仓协议漂移拦在合并前，避免私改接口（路线图 §六 红线）。

注：本测试**不改动**任何跨仓接口，仅断言既有契约，属低风险加固，无需 DCD 评审。

**端到端送达前置（DCD 裁定 20260929-homesdk修正与inbox-key-裁定.md P3）**：
`POST /api/asks/answer` 仅把答案写入 `answer_inbox/` 落盘（本测试断言到此为止）。
读侧 `af_live.read_answer_inbox`（`af_live.py:410`）在 `AUTOFORGE_INBOX_KEY` **未设时拒收全部 inbox 文件**——
即 INBOX_KEY 不是"可选硬化"，而是 **ask 通道端到端送达的生效前提**：
1. AF 部署**必须**启用 `AUTOFORGE_INBOX_KEY`；
2. DB/AF **共享同一 key**（HMAC-SHA256 对称签名，`af_api.py:703` 写侧自签、`af_live.py` 读侧验签）；
3. 仅当 INBOX_KEY 已启用且双方 key 一致时，DB 写入的答案才会被 runtime 消费；否则 POST 虽返回 200，答案永不被注回。
DB 侧镜像测试（`butler/tests/contract/test_af_ask_channel.py`）应断言：无 key 时 inbox 文件不被消费、key 一致时正常消费。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoforge.af_api import build_app

# ── 生产者 `af_live.py:_write_asks` 当前写出的 ask 项形状（契约冻结点）──
# control 取自 `AskSpec.control()`：choice→select+options；其余→input。
PENDING_SIDECAR: dict = {
    "asks": [
        {
            "ask_id": "inst-001",
            "instance_id": "inst-001",
            "node_id": "q1",
            "room": "study",
            "prompt": "现在要开灯吗？",
            "control": {
                "widget": "select",
                "kind": "choice",
                "prompt": "现在要开灯吗？",
                "options": ["开", "关"],
            },
        },
        {
            "ask_id": "inst-002",
            "instance_id": "inst-002",
            "node_id": "q2",
            "room": None,
            "prompt": "说点什么？",
            "control": {"widget": "input", "kind": "text", "prompt": "说点什么？"},
        },
    ],
    "ts": 1_700_000_000.0,
}


def _build(root: str, *, noauth: bool = False, monkeypatch=None) -> TestClient:
    if noauth and monkeypatch is not None:
        monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    return TestClient(build_app(store_root=root))


# =====================================================================
# GET /api/asks/pending —— DB 发现挂起 ask（免令牌）
# =====================================================================
def test_pending_discovery_requires_no_auth(tmp_path):
    """发现端点设计上不挂 `_read`：即便不走本地逃生舱，DB 轮询也不该被 403。"""
    client = _build(str(tmp_path))  # 无 AF_ALLOW_NOAUTH、无令牌 → 若误加鉴权会 403
    r = client.get("/api/asks/pending")
    assert r.status_code == 200, f"DB 发现端点必须免令牌可达，实际 {r.status_code}"


def test_pending_route_not_shadowed_by_name(tmp_path):
    """精确路径必须先于 `/api/asks/{name}` 通配，否则 pending 会被 {name} 吃掉成 404。"""
    client = _build(str(tmp_path))
    r = client.get("/api/asks/pending")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_pending_empty_shape(tmp_path):
    """无 sidecar 文件时返回稳定的空契约：{ok:True, asks:[]}（ts 可选，不强制）。"""
    client = _build(str(tmp_path))
    r = client.get("/api/asks/pending")
    body = r.json()
    assert body["ok"] is True
    assert body["asks"] == []
    # ts 不在空响应里出现，DB 须容忍缺失
    assert "ts" not in body or isinstance(body.get("ts"), (int, float))


def test_pending_contract_fields(tmp_path):
    """ask 项字段契约：DB 渲染问题所依赖的最小字段集与类型必须稳定。"""
    (tmp_path / "pending_asks.json").write_text(
        json.dumps(PENDING_SIDECAR, ensure_ascii=False), encoding="utf-8"
    )
    client = _build(str(tmp_path))
    r = client.get("/api/asks/pending")
    body = r.json()

    assert r.status_code == 200
    assert body["ok"] is True
    assert isinstance(body["asks"], list) and len(body["asks"]) == 2
    assert isinstance(body["ts"], (int, float))

    for item in body["asks"]:
        # DB 回答回调需要 ask_id；路由/呈现需要 room、prompt、control
        assert isinstance(item["ask_id"], str) and item["ask_id"]
        assert isinstance(item["instance_id"], str)
        assert isinstance(item["node_id"], str)
        assert item["room"] is None or isinstance(item["room"], str)
        assert isinstance(item["prompt"], str)

        control = item["control"]
        assert isinstance(control, dict)
        assert isinstance(control["widget"], str)
        assert isinstance(control["kind"], str)
        assert isinstance(control["prompt"], str)
        # choice 类控件须带 options，否则 DB 无法渲染选项
        if control["kind"] == "choice":
            assert control["widget"] == "select"
            assert isinstance(control["options"], list) and control["options"]


# =====================================================================
# POST /api/asks/answer —— DB 回注人类答案（需写令牌）
# =====================================================================
def test_answer_requires_write_auth(tmp_path):
    """写端点 fail-closed：无令牌且无本地逃生舱时必须 403。"""
    client = _build(str(tmp_path))  # 无 AF_ALLOW_NOAUTH、无令牌
    r = client.post("/api/asks/answer", json={"ask_id": "x", "text": "开"})
    assert r.status_code == 403, "写端点必须 fail-closed，实际未拒"


def test_answer_contract_persists(tmp_path, monkeypatch):
    """答案契约：接受 {ask_id,text,room} 并持久化到 answer_inbox，供 watch ticker 回注。"""
    client = _build(str(tmp_path), noauth=True, monkeypatch=monkeypatch)
    payload = {"ask_id": "inst-001", "text": "开", "room": "study"}
    r = client.post("/api/asks/answer", json=payload)
    body = r.json()

    assert r.status_code == 200
    assert body["ok"] is True
    assert isinstance(body["inbox"], str) and body["inbox"]

    # 落盘契约：answer_inbox/ 下应恰好一个文件，且字段与请求一致（可回注）
    inbox = tmp_path / "answer_inbox"
    files = list(inbox.glob("*.json"))
    assert len(files) == 1, f"应恰好写一个答案文件，实际 {len(files)}"
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    assert stored["ask_id"] == "inst-001"
    assert stored["text"] == "开"
    assert stored["room"] == "study"
    assert "answer" not in stored or stored["answer"] is None


def test_answer_structured_v2_persists(tmp_path, monkeypatch):
    """v2 M3 结构化应答契约：answer 为 AskAnswer dict 时原样持久化。"""
    client = _build(str(tmp_path), noauth=True, monkeypatch=monkeypatch)
    payload = {
        "ask_id": "inst-001",
        "text": "",
        "room": "study",
        "answer": {"kind": "choice", "value": "开"},
    }
    r = client.post("/api/asks/answer", json=payload)
    assert r.status_code == 200 and r.json()["ok"] is True

    files = list((tmp_path / "answer_inbox").glob("*.json"))
    assert len(files) == 1
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    assert stored["answer"] == {"kind": "choice", "value": "开"}


def test_answer_accepts_null_ask_id_room_resolution(tmp_path, monkeypatch):
    """ask_id 为 null 时按 room 取最旧（WO-AF-002 不变式）：契约须接受该形态。"""
    client = _build(str(tmp_path), noauth=True, monkeypatch=monkeypatch)
    payload = {"ask_id": None, "text": "好的", "room": "living"}
    r = client.post("/api/asks/answer", json=payload)
    assert r.status_code == 200 and r.json()["ok"] is True

    files = list((tmp_path / "answer_inbox").glob("*.json"))
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    assert stored["ask_id"] is None
    assert stored["room"] == "living"
    assert stored["text"] == "好的"


def test_answer_tolerates_extra_keys(tmp_path, monkeypatch):
    """body 是自由 dict（非 pydantic 模型）：契约须容忍 DB 可能附带的额外字段。"""
    client = _build(str(tmp_path), noauth=True, monkeypatch=monkeypatch)
    payload = {
        "ask_id": "inst-001",
        "text": "开",
        "room": "study",
        "source": "butler",  # DB 侧自定义的溯源字段
        "trace_id": "abc-123",  # DCD 七问裁定 问题6 要求的跨仓 trace_id
    }
    r = client.post("/api/asks/answer", json=payload)
    assert r.status_code == 200 and r.json()["ok"] is True
    stored = json.loads(list((tmp_path / "answer_inbox").glob("*.json"))[0].read_text(encoding="utf-8"))
    assert stored["source"] == "butler"
    assert stored["trace_id"] == "abc-123"
