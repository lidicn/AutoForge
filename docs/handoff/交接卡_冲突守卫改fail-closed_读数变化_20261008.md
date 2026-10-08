# 交接卡：AF 冲突守卫改 fail-closed —— 给 DB/MA 的读数变化说明

| 项 | 值 |
|---|---|
| 里程碑 | 裁定 20261008 `20261008-AF两件与MA四回执-裁定.md` §二 **裁 A** + **Q2=是** 的 AF 半边 |
| 完成日期 | 2026-10-08 |
| 状态 | ✅ 已交付（AF 仓内判据已绿；§四 那条台阶对端要在统计口径里认） |
| 影响面 | 运行期行为（do 节点是否发出）+ `/api/conflicts` 台账键 + `af/status` 的 `degraded` 取值 |

---

## 1. 变了什么（一句话）

内省（把 `node.params` 里的实体挖出来）或仲裁 `request()` **自身抛异常**时，AF 不再"照常执行"，
而是**拒绝发出这个动作**并走 `on_error`/`default` 边。**内省成功但挖不出实体**那一档照旧放行
（那是只读/无实体节点的正常形状，裁定把两条放行点分开就是为了这个）。

## 2. 对端最该知道的一条：`af/automation/failed` 会出现台阶

被拒的动作经执行器的软失效路径路由。**图里没有 `on_error`/`default` 兜底边时，实例直接 `failed`**，
于是 `af/automation/failed` 会新增一条（载荷 `error` 里带 `conflict arbiter blocked: REJECT
introspect_failed:<异常名>`）。

⇒ **MA/DB 若在算失败率/告警阈值，升级 AF 当天会看到一个台阶，那不是链路坏了，是守卫开始履职。**
分档办法：`error` 前缀 `conflict arbiter blocked:` 的是 AF 侧拒发；其余才是设备/适配器失败。

## 3. `/api/conflicts` 台账新增键（判别点在这，不在 kind）

`kind == "degraded"` 的事件现在一律带布尔键 `fail_open`：

| `fail_open` | 含义 | 该做什么 |
|---|---|---|
| `false` | 守卫失明 ⇒ AF **拒发**了这次动作（`details.phase` ∈ `introspect` / `request`） | 当成生产缺陷看：修 IR 或修 AF，别当噪声 |
| `true` | 守卫自身出问题但**放行了**（记账/通知/释放等旁路站点，逐站理由已投 DCD） | 观测；其中 `phase=request` 来自仲裁器内层，见 §5 |

老版 AF 没有这个键。**三态区分**（同 `write_gate` 那次的口径）：键不存在 = 老 AF，
不要读成 `false`，也不要读成"没发生降级"。

```python
# 建议判读
for ev in payload["events"]:
    if ev["kind"] != "degraded":
        continue
    fo = ev.get("details", {}).get("fail_open")
    if fo is False: ...   # 拒发：AF 内部缺陷 / 坏 IR
    elif fo is True: ...  # 放行档：旁路降级，先记账不动作
    else: ...             # 老 AF：形状未知，按"没看过"处理
```

## 4. `af/status` 的 `degraded.reasons` 多了一枚来源

Q2=是 要求 owner 可见通知。AF 侧两条腿：监护视图常驻指示（`/api/evidence` 的 `conflict` 列，
detail 带 `reason="guard_blind"` + `phase` + `error`），出向腿是 retained `af/status`：
守卫失明拒发时 AF 会 `mark_degraded(ADM_ERR_INTERNAL)` 并重发状态快照。

⇒ 对端看到 **`degraded=true` + `reasons=["ADM_ERR_INTERNAL"]`** 时，含义是"AF 内部有守卫失明并拒发了"，
**不是** broker/链路故障。传输恢复后由 AF 自行清位（同既有 degraded 口径）。
码不手抄，取自 `homesdk.adm.errors`；契约表里 `ADM_ERR_INTERNAL` 这一格是否要细分出"冲突守卫拒发"
子码，归 DCD/DB 定，AF 不预填。

## 5. 本轮 AF **没有**做的事（别在对端等）

- **仲裁器内层仍是放行档**：`af_conflict.py` 的 `request()` 把 `_arbitrate()` 的异常吞掉后返回
  `ALLOW`（它自己的公开契约是"绝不阻塞 do 节点"，所有直接调用方都指望它不抛）。反转它 = 改契约，
  已连同 file:line 投 DCD inbox 求裁定。本轮只给它发出的 degraded 事件补了 `fail_open: True`，
  让台账两档在形状上先对齐。
- **`conf.band()` 查询异常仍缺省 `auto`**：这一档意味着 `ask` 带（须人工确认）在异常时被当成
  `auto` 直接下发，方向是 fail-open 且有授权后果 ⇒ 已作为独立一问投 DCD，AF 不自决。
- 监护视图的**每事件明细面板**（`/api/conflicts` 的 `fail_open`/`phase`）在用户侧 UI
  （`ui-user`）尚无落点；本轮只保证"看得见有一条"，没做"点开看得清"。

## 6. AF 侧当场钉住这条口径的判据

- `tests/test_af_conflict_runtime.py`：`test_introspect_exception_refuses_the_action` /
  `test_param_depth_over_budget_refuses`（裁 A①）、
  `test_introspect_success_without_entities_still_executes`（裁 A② CONTROL）、
  `test_arbiter_error_refuses_and_records_fail_closed`（改判腿）、
  `test_the_two_allow_points_are_separated_in_source`（AST 结构腿，钉"两档不许被合并写法溜走"）、
  `test_guard_blind_refusal_is_resident_evidence_in_the_monitor_view` /
  `test_guard_blind_refusal_publishes_the_outbound_degraded_snapshot`（Q2 两条腿）。
- `tests/test_af_conflict_core.py::test_internal_error_fails_open_and_audits`：内层残余面钉成
  `fail_open is True`——那一层改判时这条就该红。

复现：

```bash
GATES_PYTHON=<本机带依赖的 python> bash gates.sh
<同一枚 python> -m pytest -q tests/test_af_conflict_runtime.py tests/test_af_conflict_core.py
```

—— AutoForge 开发 · 2026-10-08
