# 交接单：灯同步自动化 [C]/[D] 偶发不触发 —— 根因为总线节流误杀真实状态跳变

- 日期：2026-09-15
- 范围：AutoForge 事件总线 `EventBus` 节流逻辑 + 灯同步示例 `case_lamp_sync.json`
- 状态：🟢 已修复并 NAS 实测验收（[A][B][C][D] 全 PASS）

---

## 1. 现象（用户报告 + 实测）

灯同步示例（吊灯 ↔ 挂灯 双向镜像）在「单按/连按」场景下偶发不触发：
- `[A]` 单按开吊灯 → 挂灯跟随：时好时坏
- `[B]` 连按 10 次：末态正确、无熔断
- `[C]` 冷却后单按开吊灯 → 挂灯跟随：**常失败**
- `[D]` 反向开挂灯 → 吊灯跟随：**常失败**

最关键的迷惑点：**逐轮结果不一致**（同一脚本重跑，[A]/[C] 有时 PASS 有时 FAIL），这是典型「时序相关」特征。

## 2. 排除过程（重要，避免误判）

1. **WiFi 设备假设（证伪）**：把挂灯从 2.4G WiFi 的 Yeelight 换成 Zigbee 书房射灯（`switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1`），两端都走 Zigbee、事件流 8v8 均衡到达 AF，但 `[D]` 仍 FAIL。→ 不是设备/WiFi 上报问题。
2. **`mode: queued`/`single` 假设（证伪）**：两个跟随自动化从 `queued` 改 `single` 后行为无确定改善，且调度器 `SCHED-REJECT` 日志**一条都没有** → 不是 mode 导致拒绝。
3. **核心逻辑假设（证伪）**：本地 `forge run --events`（FakeHA 内存态，含联动级联）复现 [A]→[B]→[C]→[D]，结果 `bus: accepted 60, duplicate 0, throttled 0, breaker_open 0`，所有 `ceiling_on`/`lamp_on` 事件均 accepted、零拒绝。→ AF 调度/执行核心逻辑正确，bug 不在核心，**在生产 HA 集成/总线层**。

## 3. 根因（已定位）

`src/autoforge/af_bus.py` 的 `EventBus._pass_throttle` 原实现：

```python
def _pass_throttle(self, entity_id: str, now: float) -> bool:
    last = self._last_accepted.get(entity_id)
    if last is None:
        return True
    return (now - last) >= self.throttle_window   # 200ms
```

**不区分状态**：只要 200ms 内同一实体来第二个事件就丢弃，连「真实状态跳变（on↔off）」也一起丢。

实测（`BUS-DROP` 日志）抓到被误杀的触发事件：

```
[BUS-DROP] throttled switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1='on'  src=ha
[BUS-DROP] throttled switch.lumi_cn_lumi_158d000239c546_aq1_on_p_3_1='off' src=ha
```

灯同步依赖这些开关状态事件触发 `lamp_state_changed` / `ceiling_state_changed`。事件被节流丢弃 → 自动化不触发 → [C]/[D] 失败。
为何「偶发/逐轮不一致」：HA 的 SSE 在连按/级联时把同实体事件挤在 200ms 窗口内（如用户按开 + AF 镜像回写造成的同源二次上报），是否被吞取决于当轮时序 → 抖动。

## 4. 修复

文件：`src/autoforge/af_bus.py`

**(a) 状态感知节流**（核心修复）—— 窗口内只抑制「同状态重复上报」（抖动/双报），真实跳变一律放行：

```python
def _pass_throttle(self, entity_id: str, now: float, state: str) -> bool:
    last = self._last_accepted.get(entity_id)
    if last is None:
        return True
    if (now - last) >= self.throttle_window:
        return True
    return self._last_state.get(entity_id) != state   # 状态变了=真实跳变，必须放行

# __init__ 增加：self._last_state: dict[str, str] = {}
# publish 内 accept 分支：self._last_state[event.entity_id] = event.state
```

**(b) 诊断日志**（辅助，非必须，建议保留）：新增 `_log_drop()`，仅在 `throttled` / `breaker_open` 时 `print(..., file=sys.stderr)`；`duplicate` 属正常抑制不打扰日志。`Scheduler._reject` 同样打印原因到 stderr，便于以后定位。

**(c) 示例 `examples/ir/case_lamp_sync.json`**（顺应用户换设备请求，且绿测通过）：
- 挂灯侧由 WiFi Yeelight 改为 Zigbee 书房射灯 `switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1`，`light.turn_*` → `switch.turn_*`
- 两个跟随自动化 `mode: queued` → `single`（镜像幂等，丢弃中间态只认末态，更稳；与本次根因无关，仅风格选择）

## 5. 验证（NAS 实测全过）

回归脚本 `tmp/verify_zigbee_sync.sh`（部署到 NAS 后 `bash` 执行）：
- 起 `autoforge-test` 容器跑 `python -m autoforge.af_cli watch examples/ir/case_lamp_sync.json --live-allow <两实体>`
- 逐场景断言并统计 AF 收到的事件与 `BUS-DROP` 日志

结果（节流修复后，连跑两轮一致）：

```
[A] 单按开吊灯 → 射灯跟随      PASS
[B] 连按 10 次（末态 off/off）  PASS（无熔断）
[C] 冷却 18s 后单按 → 射灯跟随 PASS
[D] 反向开射灯 → 吊灯跟随       PASS   ← 此前恒定失败，现已修复
```

`BUS-DROP` 仅剩 `duplicate`（加湿器/按钮/播放器等同状态同 last_changed，正常抑制），书房两开关再无 `throttled` 丢弃；事件流 11v11 均衡。

本地内存复现脚本：`tmp/gen_events_abcd.py` + `tmp/events_abcd.json`（`forge run --events` 走真实 scheduler/executor/bus，可离线复现/回归）。

## 6. 已知风险 / 注意

1. **生产容器需重载**：修复在 `/vol1/1000/docker/autoforge/src/autoforge/af_bus.py`（卷挂载，对 `autoforge-api` 容器即 /app/src）。CLI watch 测试是即时加载；若 `autoforge-api` 常驻进程已 import 旧模块，**需重启该容器**才能生效（`docker restart autoforge-api` 或对应服务）。
2. **原始 WiFi 挂灯现在也能用**：本次根因与设备无关，原 `light.yeelink_cn_555003624_lamp22_s_2` 在节流修复后同样可正常同步；示例改用 Zigbee 射灯仅为本次对照测试，回归原 WiFi 设备不影响正确性（但 2.4G 偶发不上报的老问题仍存在，属 HA 侧，非 AF）。
3. **节流窗口仍为 200ms**：状态感知后只放过跳变，但极端情况下「200ms 内真有两次不同状态跳变」（继电器疯狂抖动）第二次会被放过——这是预期（防漏触发优先于防抖）。若需更强防抖，可配合 `Scheduler` 的节点级 `debounce` 字段（当前示例未用）。

## 7. 合并影响

- 改动局限在 `af_bus.py`（节流逻辑 + 诊断日志）与示例 JSON，**不破坏**去重/熔断/自定义事件 exempt 语义（自定义事件本就不进节流，见 `publish` 注释）。
- `af_scheduler.py` 仅加 stderr 诊断打印，无逻辑变更。
- 建议：把 `_log_drop` / `SCHED-REJECT` 诊断打印封装为可关闭（env 开关），避免高流量环境 stderr 噪声；当前留作排障用。
- 回归门槛：任何改 `af_bus` 的 PR 必须跑 `tmp/verify_zigbee_sync.sh`（或等价 [A]-[D] 矩阵），确保节流不再误杀开关跳变。
