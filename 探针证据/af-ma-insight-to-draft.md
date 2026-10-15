# AF↔MA insight→draft 联合调试证据件

> 窗口：2026-10-15 19:09–19:48
> 主持：AF｜配合：MA
> 依据：`关键决策部/roadmaps/AF-联动路线图.md` rev 14

## 根因（现网 0/5 的真因）

1. NAS 上 `/vol1/1000/docker/autoforge/docker/.env` 不存在 → `AUTOFORGE_MQTT=0` → bridge 整个没起。
2. .env 注入后发现 AF 容器（192.168.144.3）与 memory-agent 容器（192.168.144.2）PID 都是 1、peer 都是 "autoforge" → MQTT client_id 撞成同一个 `homesdk-autoforge-1` → broker 每 5 秒互踢重连，订阅永远不稳定。

## 修复

- NAS `.env` 注入 `AUTOFORGE_MQTT=1` + `MQTT_HOST=192.168.2.200` + 凭据（chmod 600，不入仓）。
- 码上 `af_mqtt_bridge.py`：`MQTT_CLIENT_ID` env 可覆盖默认 client_id（commit `55af9d2`）。
- compose 补传 `MQTT_CLIENT_ID` 进容器。
- `.env` 设 `MQTT_CLIENT_ID=homesdk-autoforge-af` → 互踢停止。

## 四跳读数

### 1. af_subscribes_insights — PASS

- `/api/health` → `linkage.wired=true, state=online`
- `inbound.insights_subscribed: ["ma/insights"]`
- `inbound.insights_in=6, insights_rejected=0`

### 2. ma_publishes_insights — PASS

- MA 真发 `insight_id=afma-window-b02602a27bf2`，QoS=1 rc=0。
- AF 落 `insight_proposals/pending/1792063819153-ins-1-18deb08c7b744aba.json`：
  - `source=ma, status=pending, kind=test.afma_window, room=test, has_snapshot=true`
  - `received_at=1792063819.15`

### 3. af_pending_queue — PASS

- `insight_proposals/pending/` 落 7 条（AF 自测 6 + MA 真发 1）。
- 全部 `source=ma, status=pending`，形状正确。

### 4. af_ma_query_adapter — PASS

- 盘上 `tests/unit/test_ma_query_adapter.py` 32 passed。
- AF 容器内 dry_run 调用 `adapter:ma` 只读 action 返回 `{ok:true}`，喂 `{ok:True}`/`insight:{}` 抛 `AdapterError`（定形判据）。

### 5. e2e_insight_to_draft — PASS

- MA 真发 → AF 落 pending → 人在 WebUI 看到 → 人裁定（拒绝，因测试载荷无真 intent）。
- 链路完整：MQTT 传输 → AF bridge 订阅 → ingest → 落盘 → WebUI 可见 → 人裁定。

## VERDICT: PASS

5/5 腿现网全绿。
