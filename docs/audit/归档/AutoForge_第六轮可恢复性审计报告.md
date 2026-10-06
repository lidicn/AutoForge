# AutoForge 第六轮审计报告：可恢复性链（回滚 / 撤销 / 崩溃恢复）

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 审计范围：`src/autoforge/` 内核（98 个 Python 源文件 / 77,949 行）
> 本轮主题：**可恢复性链——系统承诺的"不可逆动作有回滚保障"是否真的成立**
> 判定标准：严格档 —— 能力矩阵须**交叉实测**，承诺类断言须给出具体输入→输出反例
> 报告日期：2026-10-06

---

## 一、执行摘要

AutoForge 的核心安全主张是"**Agent 写、机器验、人审批**"三权分立 + **不可逆动作必有回滚把手**（铁律 #4/#5）。第一轮审了审批链路，但**回滚与撤销链路本身从未被系统审过**。第六轮补上这一环。

**结论：找到 2 个 High 缺陷，共同点是——"动作做了，但恢复能力没有，而系统说它有"。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R6-01** | 🟠 High | **34 个动作能检测到漂移却回滚不了，证据仍记「已自动回滚」** | `af_executor.py:556-565` |
| **R6-02** | 🟠 High | **撤销快照抓不到时动作照常下发、无回滚把手、且无告知；日志还写「不下发」** | `af_adapters/ha.py:263-270` |

### 这两个缺陷的形状

都不是"功能坏了"，而是**"恢复能力缺失被记录成恢复成功"**：

- R6-01：漂移检测到、一次都没回滚成、证据写"已自动回滚（0 次反向下发）"
- R6-02：快照没抓到、动作已下发、日志写"已忽略，不下发"

这恰恰是 AutoForge 自己在 CI 注释和 `af_canary.py:88` 里反复痛斥的那一类问题——原话是"读不到实际态不等于验过了（铁律 #5：EXEMPT ≠ VERIFIED）"。**同一条纪律，在回滚结果这一环没有贯彻。**

### 一个正面发现（重要）

`af_executor.py:222-227` 的**观察期分支已经正确处理了这个情况**：

```python
reason = (
    "canary 观察期漂移，已自动回滚"
    if rolled
    else "canary 观察期漂移，auto_rollback=false ⇒ 未回滚，留给人判断"
)
```

它有 `if rolled else` 区分。这说明团队**意识到了**这个问题，只是在两处分叉实现里**只修了一处**（详见 R6-01 的归因问题）。修复成本低——把观察期分支的严谨度同步到立即分支即可。

---

## 二、工作流迭代：这一轮改了什么

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一 | 安全 / 架构 | 5 |
| 二 | 稳定性 / 功能性（异常吞咽） | 7 |
| 三 | 递归 / 资源上限 | 3 |
| 四 | 并发 / 状态一致性 | 3 |
| 五 | 契约 / 语义一致性 | 2 |
| **六** | **可恢复性链（回滚/撤销/恢复）** | **2** |

### 四个新范式（已记入 lessons 第 16–19 条）

**1. 验证"映射类函数"必须用该域的真实属性形态**（否则制造假缺陷）

第一轮测 `restore_call(domain.x, {"state":"on","attributes":{}})`，得出 **cover / climate "无法映射"**——**这是我的误报**。补上真实属性（cover 需 `current_position`、climate 需 `temperature`+`hvac_mode`）后，两者**均可映射**。真实不可回滚域从 13 修正为 11。

> 判据：测能否映射就要喂该域判据真正读的字段。用统一最小桩会把"数据不全"误判成"能力缺失"。

**2. 能力矩阵交叉验证——把两个能力集做笛卡尔积**

不单独看"哪些动作可回滚"，而是：

| 能力 | 判定依据 | 数量 |
|---|---|---|
| A：可判漂移 | `SERVICE_STATE` 有该动作预期态 | 47 / 60 |
| B：可回滚 | `restore_call` 可映射 | 21 / 60 |
| **A 且 非 B** | **能检测到漂移却回滚不了** | **34 / 60** |

单独看任一个能力集都发现不了这 34 个。这是第三轮"声明能力 vs 实际能力"的深化版——**两个能力之间的缝隙**。

**3. 承诺-证据对照：日志/审计措辞 vs 实际执行结果**

第五轮查"代码契约"，本轮补一环：**可观测性输出是否如实**。凡输出里出现"已 X"/"未 X"的结果性断言，都拿去和执行路径对照。本轮两条缺陷都是这类。

**4. 同类功能的两个分支要互相对照**

`af_executor` 里 canary 漂移处理有**两条分叉**：立即检查（`:556`）与观察期（`:222`）。后者比前者严谨（有 `if rolled else`）。**同一功能两套实现、严谨度不同 → 说明分叉后未同步**，差异处即为靶点。

---

## 三、确认缺陷

### 🟠 R6-01　34 个动作能检测到漂移却回滚不了，证据仍记「已自动回滚」

**位置**：`src/autoforge/af_executor.py:556-565`（立即检查分支）

```python
elif wrapped.has_drift():
    rolled = guard.check_and_rollback(adapter, wrapped)
    self._feed_canary_evidence(instance, node, "failed", {
        "reason": "canary 漂移，已自动回滚", "rollback_calls": len(rolled),   # ← 无条件"已自动回滚"
    })
    self.audit.add(AuditEvent(
        type=ENTITY_DRIFT,
        message=f"canary 检测到漂移 {node.action}，已自动回滚（{len(rolled)} 次反向下发）",
        ...
    ))
    return self._soft_fail(instance, node, RuntimeError(f"canary 漂移，已回滚 {node.action}"))
```

**能力矩阵实测**（60 个已知动作，真实属性形态）：

```
已知动作总数              : 60
可判漂移(有 SERVICE_STATE) : 47
不可回滚(restore_call=None): 39
★ 可判漂移 但 不可回滚     : 34   ← 检测到漂移却回滚不了
```

受影响动作（34 个）包括：

`alarm_control_panel.*`（5 个）· `automation.*`（3）· `group.*`（2）· `humidifier.*`（2）· `input_boolean.*`（2）· `media_player.*`（6）· `notify.notify` · `persistent_notification.create` · `scene.turn_on` · `script.*`（2）· `vacuum.*`（5）· `valve.*`（2）· `water_heater.*`（2）

**实测链条**：

1. `has_drift()` → True（有 `SERVICE_STATE` 预期态，实际态对不上）
2. `check_and_rollback()` → `res.rollback(adapter)` → 对 `_targets()` 逐个调 `restore_call(entity_id, snap)`
3. 该域无恢复映射 → `restore_call` 返回 `None` → `logger.warning("canary 回滚跳过 …")` 并 `continue`
4. `rolled = []` → `len(rolled) == 0`
5. **但证据记 `"canary 漂移，已自动回滚"`、审计写 `"已自动回滚（0 次反向下发）"`**

**后果**：证据层说"已自动回滚"，实际**设备处于漂移状态、一次都没回滚**。下游任何读 canary 证据的判断（自愈闭环、审计报表、UI 状态展示）都会读到假数据。这违反项目自己写在 `af_canary.py:88` 的铁律——"读不到实际态不等于验过了"。

**失败方向说明**：状态记的是 `failed`（不是 `verified`），所以**没有"把失败伪装成成功"的安全闸绕过**。问题在**恢复结果**这一环的表述失真，不在漂移判定。

**归因问题（观察期分支也有）**：`af_executor.py:222-227` 虽有 `if rolled else` 区分，但把 `rolled` 为空**单一归因**为 `auto_rollback=false`。实际上 `rolled` 为空有**两种**原因：

| 原因 | 判定 |
|---|---|
| `auto_rollback=False`（配置关闭） | `check_and_rollback` 首行短路 |
| **所有实体无法映射**（本缺陷） | `restore_call` 全返回 `None` |

观察期分支把后者错误说成前者，会让运维以为"我关了自动回滚"，实际是"回滚做不了"。

**修复建议**（对齐观察期分支已有的严谨度，并修正归因）：

```python
rolled = guard.check_and_rollback(adapter, wrapped)
targets = wrapped._targets()
unmapped = [e for e in targets if restore_call(e, wrapped.pre_snapshot.get(e) or {}) is None]
if not rolled and not guard.auto_rollback:
    reason = "canary 漂移，auto_rollback=false ⇒ 未回滚，留给人判断"
elif unmapped:
    reason = f"canary 漂移，但 {len(unmapped)} 个实体无恢复映射 ⇒ 回滚未执行（非 auto_rollback 关闭）"
else:
    reason = "canary 漂移，已自动回滚"
```

**建议顺带**：把 34 个不可回滚动作整理成"回滚能力清单"暴露给静态扫描——用户写 IR 时就该知道这个动作没有自动回滚把手（与 `unmodeled` 同级的诚实告知）。

---

### 🟠 R6-02　撤销快照抓不到 → 动作照常下发、无把手、无告知；日志还写「不下发」

**位置**：`src/autoforge/af_adapters/ha.py:263-270`

```python
if self.undo_recorder is not None:
    try:
        pre = _capture_pre_snapshot(self.transport, params)
        if pre:                                    # ← 空则静默跳过，无日志
            self.undo_recorder(action, params, pre)
    except Exception:  # 快照捕获异常绝不阻断真实下发
        logging.getLogger("autoforge.adapter").warning(
            "undo 快照捕获异常（已忽略，不下发）", exc_info=True   # ← 措辞与实际相反
        )
return self.transport(action, params)              # ← 动作照常下发
```

**实测三种场景**（真实 `HAAdapter`，`dry_run=False`，注入 `undo_recorder`）：

| 场景 | 快照记录 | 动作下发 | 日志 |
|---|---|---|---|
| `all_states()` 返回空（实体不可见） | **0** | **1（已下发）** | **无** |
| `all_states()` 抛异常 | 0 | 1 | 有 warning，但文本错误 |
| transport 无 `all_states` | 0 | 1 | **无** |

**两个问题**：

**① 静默失效**——用户显式传 `--undo`（`af_cli.py:306-312` 注入 `UndoStore`），明确要求撤销能力。快照抓不到时该实体**没有回滚把手**，但动作**照常下发**，且场景 1/3 **完全没有日志**。用户事后 `forge undo` 会发现这条记录不存在，或 `inspect` 只列出部分实体——**从记录本身看不出有实体缺失**。

**② 日志措辞与实际行为相反**——`"已忽略，不下发"` vs 实际 `return self.transport(action, params)`（**已下发**）。运维看到这条日志会以为动作被阻止了。这是可观测性错误，会直接误导事故判断。

**修复建议**：

```python
if self.undo_recorder is not None:
    try:
        pre = _capture_pre_snapshot(self.transport, params)
    except Exception:
        pre = {}
    if pre:
        self.undo_recorder(action, params, pre)
    else:
        logging.getLogger("autoforge.adapter").warning(
            "undo 快照未捕获（实体不可见或状态源不可达）：本次下发**没有回滚把手**，"
            "动作仍会下发；entity=%s", params.get("entity_id")
        )
return self.transport(action, params)
```

**更强的选项**：给 `--undo` 加"快照抓不到即拒发"的开关（fail-closed），让用户能选择"没回滚把手就别动真机"。这符合项目已在别处贯彻的 fail-closed 纪律。

---

## 四、本轮验证通过（确认无问题）

| 契约面 | 验证方式 | 结论 |
|---|---|---|
| **双轨对拍一致性** | 17 个样例 IR 跑 `compare_dual_track`（FakeHA vs HiFi） | ✅ **全部 0 分歧**。仿真的"唯一效果真值表"设计成立，不存在双轨漂移 |
| **撤销时间窗时区语义** | UTC aware / 本地 naive / UTC+8 三种时钟判过期 | ✅ 用 float 时间戳比较，与时区无关，**无跨时区误判** |
| **崩溃恢复（af_persist）** | 第四轮已核 | ✅ SHA256 校验 + `mkstemp` 随机 tmp + 租约仲裁 `claims()` + 坏文件跳过有 warning |
| **覆盖率** | `restore_call` 真实形态实测 | ✅ cover / climate **可**回滚（第一轮误报已修正）；light/switch/fan/lock 均可 |
| **`has_drift()` 异常语义** | 读实现 | ✅ 取不到实际态抛 `UnknownEntity` → 保守判**有漂移**（铁律 #5 正确贯彻） |

### 关于双轨对拍的补充

`compare_dual_track` 的 sun 白名单在 17 个样例上均为 0——经核查是因为样例用 `entity.sun.sun` **变量**而非 `trigger.type == "sun"`，属正常。构造含 sun 触发器的 IR 可触发豁免路径，但**样例集上无分歧**，说明两轨真值源共享的设计是有效的。

---

## 五、已排除（rejected）

| 候选 | 数量 | 推翻理由 |
|---|---|---|
| **cover / climate 不可回滚** | 2 | **我方误报**。首轮用 `{"state":"on","attributes":{}}` 测试桩，缺 `current_position` / `temperature`+`hvac_mode`。补真实属性后**均可映射**（已记入 lessons 第 16 条） |
| **`inverse_action` 无逆动作的 16 个动作** | 16 | 不影响回滚——`rollback()` 走 `restore_call` + `pre_snapshot` 真实快照，**不用** `inverse_action`。`inverse_action` 只服务 `expected_state()` 漂移判定，无映射时走 `unmodeled` 档，**处理正确** |
| **`af_executor.py:222` 观察期分支** | — | 已有 `if rolled else` 区分，非缺陷（但归因有误，已并入 R6-01） |
| **`has_drift()` 抛 `UnknownEntity`** | — | 保守判有漂移，符合铁律 #5，**是有意的正确设计** |
| **`_capture_pre_snapshot` 返回空** | — | 函数本身 fail-closed 正确；缺陷在**调用方**静默跳过（R6-02） |

---

## 六、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R6-02 日志措辞修正 + 快照缺失告警 | 约 6 行 | 最低成本、最高收益；顺带消除"误导事故判断"的可观测性错误 |
| **P1** | R6-01 立即分支对齐观察期 + 修正归因 | 约 10 行 | 消除 34 个动作的假证据 |
| **P2** | 暴露"回滚能力清单"给静态扫描 | 中 | 让用户在写 IR 时就知道某动作无自动回滚把手（与 `unmodeled` 同级诚实告知） |

**回归验证清单**：
1. 对 34 个不可回滚动作之一（如 `notify.notify`）注入漂移 → 证据 `reason` 应含"无恢复映射"而非"已自动回滚"
2. `all_states()` 返回空 + `--undo` → 应出现"没有回滚把手"的 WARNING 日志
3. 同上场景，日志文本不应出现"不下发"
4. `restore_call("cover.x", {"state":"open","attributes":{"current_position":40}})` → 应返回 `cover.set_cover_position`

---

## 七、六轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 | 主因 |
|---|---|---|---|
| 一 | 安全 / 架构 | 5 | 审批链路身份语义不严密 |
| 二 | 稳定性 / 功能性 | 7 | 异常被吞后不留痕 |
| 三 | 递归 / 资源上限 | 3 | 声明能力与实际能力脱节 |
| 四 | 并发 / 状态一致性 | 3 | 单进程假设 vs 多进程现实 |
| 五 | 契约 / 语义一致性 | 2 | 同一算子两套类型转换策略 |
| 六 | 可恢复性链 | 2 | **恢复结果被记成恢复成功** |

**六轮下来，AutoForge 给我最深的印象是：它对"诚实"有近乎偏执的追求。**

`af_canary.py:88` 写"读不到实际态不等于验过了"、`af_executor.py:552` 写"记成 verified 就是铁律 #5 的假证据，单列 unmodeled 一档"、`af_undo.py` 写"绝不只报 restored"、CI 注释写"门永远不可能红"——**团队反复在跟"假证据"作战，而且赢了很多次**。

但本轮的两条缺陷说明：**这条战线还有一段没守住——恢复动作本身的结果**。漂移检测（能不能发现问题）已经做到了 fail-closed 且诚实地分了 `unmodeled` 档；**回滚执行（问题能不能修好）这一环却还在无条件说"已自动回滚"**。

好消息是：观察期分支（`:222`）已经证明了正确写法就在这个文件里，只是没同步到立即分支（`:556`）。**这不是能力问题，是分叉实现未同步**——修起来很快。

**给工程团队的一句话**：把"不可回滚动作清单"（34 个）做成和 `unmodeled` 一样的**一等公民告知**。你们已经教会系统说"这个我验不了"，现在该教它说"这个我发现了但修不回来"。后者比前者更需要人介入。

---

## 八、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| 误报修正手册（15 条 + 本轮 4 条 = **19 条**） | `audit-env/scripts/lessons-round2.md` |
| 深度缺陷扫描器（E01–E13） | `audit-env/scripts/scan_round3.py` |
| 并发缺陷扫描器（F01–F05） | `audit-env/scripts/scan_round4.py` |
| 契约扫描器（G01–G03） | `audit-env/scripts/scan_round5.py` |
| 稳定性审计主流程（`STEP=` 分段） | `audit-env/scripts/40-stability-audit.sh` |
| 环境自检（23 项全通过） | `audit-env/scripts/99-verify.sh` |
| homesdk 本地包（供仿真/对拍验证） | `audit-env/homesdk-pkg/` |

### 本轮新增 lessons（16–19）

- **16** 验证映射类函数必须用该域真实属性形态（否则制造假缺陷）
- **17** 能力矩阵交叉验证：找"能做 A 不能做 B"的缝隙
- **18** 承诺-证据对照：日志/审计的"已 X"/"未 X"断言要拿去和执行路径对照
- **19** 同类功能的两条分叉实现要互相比严谨度，差异处即靶点
