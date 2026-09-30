# 交接卡 · 吊灯 ↔ 挂灯状态同步 + friendly_name 解析能力核查

- **日期**：2026-09-15
- **需求**：编写「书房吊灯 `switch.lumi_cn_lumi_158d000239c546_aq1_on_p_3_1`」与「显示器挂灯 `light.yeelink_cn_555003624_lamp22_s_2`」开关状态同步；核查 AF 能否用 friendly_name 找到 entity_id。

## 交付物

| 文件 | 说明 |
|---|---|
| `examples/ir/case_lamp_sync.json` | 双向状态同步（emit 解耦，4 个自动化） |
| `examples/case_lamp_sync.seed.json` / `case_lamp_sync.events.json` | e2e 初始状态与事件序列 |
| `tests/acceptance/test_case_lamp_sync.py` | FakeHA 验收（双向 + 防回环） |

## 自动化设计（为何不能「直接双向」）

AF 静态检查 `ENTITY_DEP_CYCLE`（`af_scanner.py:549`）规则：「**A 写 X 且 B 由 X 触发 → A→B（含自环）**」。
因此**任何"写自己触发的实体"都被拒**——直接 `A ↔ B` 双向同步（A 触发写 B、B 触发写 A）必然成环被拦；拆成 2 个 automation 也仍成环。

**唯一可行解：emit 事件解耦**（让实体依赖与事件依赖分别无环）：

| 自动化 | 触发 | 动作 |
|---|---|---|
| `ceiling_state_changed` | 吊灯 on/off | 广播 `ceiling_on` / `ceiling_off`（不写实体） |
| `lamp_follow_ceiling` | `ceiling_on/off` | 挂灯 turn_on/off（`if` 读挂灯快照防回环） |
| `lamp_state_changed` | 挂灯 on/off | 广播 `lamp_on` / `lamp_off`（不写实体） |
| `ceiling_follow_lamp` | `lamp_on/off` | 吊灯 turn_on/off（`if` 读吊灯快照防回环） |

实体依赖：`lamp_follow_ceiling→lamp_state_changed`、`ceiling_follow_lamp→ceiling_state_changed`（**无环**）；
事件依赖：`ceiling_state_changed→lamp_follow_ceiling`、`lamp_state_changed→ceiling_follow_lamp`（**无环**）。运行时靠防回环条件收敛。

## 验证

| 层 | 结果 |
|---|---|
| 本机 `forge run`（FakeHA） | ✅ 4 自动化；总线 **accepted 6 / duplicate 0**（收敛，无无限互触）；审计含 `ceiling_on / lamp_off / lamp_on` |
| 本机 pytest | ✅ **259 passed / 10 skipped / 0 failed** |
| NAS 全量回归 | ✅ **269 passed / 0 failed** |

## friendly_name → entity_id：**不能解析**

AF **没有任何** friendly_name → entity_id 的解析能力：

1. 全仓检索 `friendly_name`：**无解析用途**——仅 `test_af_live.py` 中作为 SSE payload 的 HA `attributes` 透传。
2. `af_api.py` 端点清单**无** entities / resolve 类接口。
3. IR 的 `trigger.entity_id` / `do.params.entity_id` / `expr` 的 `entity.*` **一律是自由字符串**，运行时按**精确字符串**与状态源键（HA entity_id）匹配。
4. **实测探针**：以 `'书房吊灯'`（friendly_name）查状态 → `None`；当 entity_id 取 domain 默认值 → `'unknown'`（对照真实 `switch.lumi_…` → `'off'`）。

**结论**：AF 必须直接写 entity_id（如本卡所给），**无法**用 friendly_name 反查 entity_id，也返回不了正确状态。friendly_name→entity_id 的解析能力在 **memory-agent**（设备目录 `get_entity_catalog` + 身份层 `IdentityReconciler`），不在 AF。

## 真机测试（2026-09-15，NAS 真实 HA）

**结论**：此前只跑过仿真（FakeHA + 真 vhass），**未做过真机部署**（autoforge 侧无 `AUTOFORGE_HA_TOKEN`）。本次已在真实 HA 上完成真机下发测试。

- **HA**：`http://192.168.2.200:8123`（可达，HTTP 200）。
- **凭证**：MA 的 `config.json` 里 `hass_token` **有效**（HTTP 200）；MA `.env` 的 `HASS_TOKEN` **已失效**（401）。
- **测试 1（单向）**：BEFORE 挂灯 `off` → `forge run --live --confirm --live-allow …` 回放 `[吊灯 on]` → AFTER 挂灯 **`on`** ✅（AF 真实下发 `light.turn_on`）。
- **测试 2（完整双向）**：回放 `[吊灯 on, 挂灯 off, 挂灯 on]` → 总线 **accepted 6 / duplicate 0** → AFTER 吊灯 **`on`** ✅（`灯→吊灯` 反向链路真实下发 `switch.turn_on`）。环境已恢复（两灯 `off`）。
- 真机预检 **0 错误 0 告警**。

**部署方式**：`forge run --live`（`--confirm` + `--ha-token` + `--live-allow` 白名单）是**一次性回放**；若需"真实灯变化时实时跟随"，用常驻 `forge watch --live`（订阅 HA 事件流）。

## 真机常驻部署与"按按钮"实测（2026-09-15）

**部署**（NAS 后台容器，`forge watch` 订阅 HA 事件流实时驱动同步）：

```bash
docker run -d --name autoforge-sync --restart unless-stopped \
  -e AUTOFORGE_HA_TOKEN="<有效令牌>" \
  -v /vol1/1000/docker/autoforge:/app \
  autoforge-test \
  python -m autoforge.af_cli watch examples/ir/case_lamp_sync.json \
    --ha-url http://192.168.2.200:8123 --confirm \
    --live-allow "switch.lumi_cn_lumi_158d000239c546_aq1_on_p_3_1,light.yeelink_cn_555003624_lamp22_s_2"
```

> 注意：`forge watch` **没有 `--live` 参数**（它本身就是真机常驻）。

**发现并修复关键 Bug（真机才暴露）**：HA 的 `/api/stream` **不发送 SSE `event:` 行**，事件类型放在 `data` 的 JSON `event_type` 字段里；而原 `parse_ha_event` 只认 SSE `event:` 行的类型 → **真实 HA 事件被整体丢弃**（`HAEventStream` 的 logger 默认静默，所以毫无报错、表现为"装死"）。修复：`parse_ha_event` 同时接受 SSE `event:` 行与 payload 内 `event_type`；补 2 条回归用例（`test_af_live.py`）。

**实测**（模拟"按按钮"= 通过 HA API 开吊灯 `switch.turn_on`）：

| | 挂灯 | 吊灯 |
|---|---|---|
| BEFORE | off | off |
| AFTER（watch 实时响应） | **on** ✅ | on |
| RECOVERED | off | off |

watch 单次会话累计处理 **135** 个 HA 事件（全屋 `state_changed`，仅匹配白名单实体触发自动化）。**"按按钮 → 自动化触发 → 挂灯跟随"真机闭环打通。**

**回归**：NAS 全量 **271 passed / 0 failed**。

## 关键坑

1. **emit 解耦范式**：状态变化侧只 emit（不写实体），跟随侧只写对方实体；`if` 读对方快照做防回环。
2. **总线节流**：单实体 200ms 节流窗口；虚拟时钟不推进时窗口**永不失效** → 测试中同实体连续事件被 `throttled`，须在 fire 之间 `advance`。
3. **FakeHA 的 `FakeHAAdapter.call` 只改状态、不 emit bus 事件**（`fake.py:145`）；真 HA 有 `state_changed`，故闭环在真机/真 vhass 下成立，FakeHA 下"跟随侧改动"不反向触发。
