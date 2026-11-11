# AF v1.29 场景对象（SceneDef）设计 —— 对话操控三分法第三类

> 状态：设计稿（本单不落码） ｜ 契约快照：b06206775b61
> ⛔ 非目标：不改 `src/autoforge/af_scene.py`（SceneManager 自动化组启停，Scene 语义保持不动）、
> 不改 `src/autoforge/af_task_order.py`（已落码任务单形态）、不设计 UI、不引新依赖。

## 0. 一句话定义

场景（SceneDef）＝ 预设的**批量设备动作清单**：一句话触发 → 按序执行 → 跑完即止。
⛔ 它不是 `src/autoforge/af_scene.py:61` 的 `Scene`（一组 automation 的开/关编排，带 active 持久态与互斥组）。

## 1. IR 形态（场景定义 schema）

新模块 `src/autoforge/af_scene_kit.py`（后续落码），导出 `SceneDef` / `SceneStep` / `SceneRunner`。

```yaml
kind: scene_def                     # ⛔ 不是 af_scene.Scene
scene_id: scene_home_001            # 唯一；构造期查重，重复即拒
name: "回家模式"                    # 展示名 ＝ 一句话触发主词
aliases: ["到家了", "我回来了"]      # 触发别名，可空
version: 1
owner: "owner"                      # 人那一侧身份；⛔ agent 不能自签
condition: null                     # 可选前置断言；null ＝ 无条件
steps:                              # 列表顺序 ＝ 执行顺序（索引即 step_no）
  - step_id: s0
    adapter: hass
    action: light.turn_on
    target: light.living_room
    params: {brightness_pct: 60}
    on_error: abort                 # abort（默认）| continue
    delay_ms_after: 0
  - step_id: s1
    adapter: hass
    action: climate.set_temperature
    target: climate.bedroom
    params: {temperature: 26}
    on_error: continue
    delay_ms_after: 500
created_via: ask                    # ask | import | api
created_at: "2026-11-11T00:00:00+08:00"
```

**构造期校验（fail-closed）**：scene_id 非空且查重；name 非空；steps 非空；
step_id 场景内唯一；on_error ∈ {abort, continue}；condition 只认声明形状；
出现未声明键即拒（与 `src/autoforge/af_grant.py:594` 同纪律）；
action 若判 L3 或落禁入域，配置期直接拒。
**不落盘**：每步风险档位不写入 IR，执行期现算（同 `src/autoforge/af_grant.py:486` 的取档方式）。

## 2. 执行路径

触发（主词/别名匹配，匹配不上即澄清）→ 载入 SceneDef → 求值 condition
→ 逐步：collect 动作三元组 → `GrantStore.check` → 过检则 `live_run` 下发
→ 汇总回执（带 grant 引用）。

判定规则（契约不变量：场景执行每步独立过 grant，DCD Q3 甲）：

- `hard_denied`（`src/autoforge/af_grant.py:219`）：**完成当前步、取消未执行步**；
  已成步不回滚（设备动作不可逆，回执记现状）。
- `needs_confirm`：`issue_confirm` 出单次放行票 → 人工确认 → `consume_confirm`
  （验票即毁，票绑 (grant, agent, 实体集合)，对不上即拒并留痕，`src/autoforge/af_grant.py:557`）。
  ⛔ 确认只放行这一次，不升级 grant。
- 判定结果只认 `check.code` 单码视图（`src/autoforge/af_grant.py:209`）；
  ⛔ 不得只看 `ok`（`src/autoforge/af_grant.py:198`），⛔ 不得自拼码。
- 批量执行入口按名字调 `live_run`（af_service 调用点 `src/autoforge/af_service.py:3092`，
  函数体不在本包契约内，不展开）；步间 `delay_ms_after` 节流。
- `uses` 计数：每次 run 对所用 grant 记一次（`bump_use` 是整盘重写，逐步计数会造成账本抖动）。

## 3. 配置期多轮 ask

配置期不落半成品：任一轮取消/超时即整单放弃。挂起反问走 `asks_pending`
同形状（`src/autoforge/af_service.py:2578`）。

1. 命名：「这个场景叫什么？以后说哪句话就触发它？」→ name + aliases；撞名重问；生成 scene_id。
2. 设备清单：「要动哪些设备？我看到这些：…」→ 逐个确认 target；⛔ agent 不替用户选设备。
3. 顺序：「按什么顺序执行？我建议先开灯、再开空调、最后播报。」→ 默认＝用户叙述顺序，人拍板。
4. 参数：「每步参数（亮度/温度/音量）用多少？」→ 未给即设备缺省；⛔ 不猜数值。
5. 条件：「需要什么条件才跑吗？没有就跳过。」→ 可跳过得 null。
6. 汇总复述 → 人确认 → 落盘，created_via: ask。

## 4. 三分边界

| | 场景 SceneDef | af_scene 组启停 | af_task_order 任务单 | 自动化 |
|---|---|---|---|---|
| 操作对象 | 设备动作 | automation_id 开/关 | 任务步骤/交付物 | 条件→动作 |
| 状态 | 无激活态，跑完即止 | active 持久态＋互斥组（`src/autoforge/af_scene.py:68`） | 单据状态机＋断点续跑（`src/autoforge/af_task_order.py:283`） | 常驻监听 |
| 触发 | 一句话/别名 | 显式 activate/deactivate | claim 到期领取 | 事件/条件 |
| 失败语义 | 完成当前步、取消未执行步 | 回滚或保持 active=True 可重试（`src/autoforge/af_scene.py:225`） | 停断点，从 resumed_from 续 | 视 automation 而定 |

口诀：改设备状态＝场景；改 automation 开关＝af_scene；一次跑完＝场景/任务单；
长期挂＝自动化；有断点续跑＝任务单。

## 5. 留痕与回执

逐步条目复用 `GrantCheck.to_dict`（`src/autoforge/af_grant.py:222`）键形，⛔ 不另造判定字段；
可用 grant 清单沿用 `GrantStore.list`（`src/autoforge/af_grant.py:368`）形状（status 现算不落盘）；
场景定义归档记 owner，沿用最近一次归属主体的取值形状（`src/autoforge/af_service.py:2117`）。

```json
{"run_id": "r-8f2c…", "scene_id": "scene_home_001", "name": "回家模式",
 "agent": "tv-pilot", "code": "GRANT_OK", "started_at": "2026-11-11T19:02:03+08:00",
 "steps": [
   {"step_no": 0, "step_id": "s0", "target": "light.living_room",
    "grant_id": "g-1a2b3c4d5e6f", "code": "GRANT_OK", "done": true},
   {"step_no": 1, "step_id": "s1", "target": "climate.bedroom",
    "grant_id": "g-1a2b3c4d5e6f", "code": "GRANT_CONFIRM_REQUIRED",
    "ticket": "t-…", "done": false, "skipped_reason": "cancelled_after_hard_deny"}
 ]}
```

审计字段：run_id / scene_id / step_no / grant_id / code / ticket / agent / ts。
TVPilot 侧适配器不在本仓，契约文档另立。

## 6. 测试面

单元：构造校验（查重/空 steps/未知键/L3 与禁入域拒绝）、顺序执行器（step_no 顺序、
delay 节流、abort/continue 分支）、逐步 grant 判定（硬拒取消后续、confirm 出票验票、
票不匹配拒）、回执形状。
契约：`src/autoforge/af_scene.py:61` 与 `src/autoforge/af_task_order.py:283` 的
范围/签名哈希不变；`SceneDef` 与 `Scene` 名字空间不相交；`GrantStore` 调用点集合不变。
验收判据见随单工单「验收」节。

---

## 测试

**单元（tests/test_scene_kit.py，新增测试文件，不碰禁改文件）**

- `test_scenedef_rejects_duplicate_scene_id`：同 id 二次构造即 ValueError（对齐 `src/autoforge/af_scene.py:138` 的查重纪律）。
- `test_scenedef_rejects_empty_steps / unknown_key / bad_on_error`：空 steps、未声明键、`on_error: xyz` 各自 fail-closed。
- `test_scenedef_rejects_l3_and_banned_domain_at_config`：`action: shell.exec` 或门锁/燃气域在配置期即拒。
- `test_runner_preserves_step_order_and_delay`：3 步场景按 step_no 下发，`delay_ms_after` 生效。
- `test_hard_deny_finishes_current_cancels_rest`（DCD Q3 甲）：第 2 步硬拒 → 第 2 步不下发、第 3..n 记 `cancelled_after_hard_deny`、已成第 1 步 `done: true` 且不回滚。
- `test_needs_confirm_issues_ticket_and_consumes_once`：出票 → 验票放行一次 → 同票二次 `consume_confirm` 返回 False 并留痕（`src/autoforge/af_grant.py:557`）。
- `test_receipt_carries_grant_id_and_code`：每步回执含 `grant_id` 与 `code`，且 `code == GrantCheck.code`（`src/autoforge/af_grant.py:209`），无自拼码字段。
- `test_uses_bumped_once_per_run`：5 步场景只 `uses +1`。
- `test_ask_flow_cancel_leaves_no_partial_def`：第 3 轮取消 → 无 SceneDef 落盘。

**契约**

- 冻结断言：`src/autoforge/af_scene.py:61`、`src/autoforge/af_scene.py:115`、`src/autoforge/af_task_order.py:283` 的 range/sig 哈希与快照 b06206775b61 一致（即契约降级清单里 SceneManager/TaskOrderStore 亦不触碰）。
- `GrantCheck`/`GrantStore`/`GrantStore.list` 导出边界与调用点集合不因本单变化（`src/autoforge/af_grant.py:196`、`src/autoforge/af_grant.py:235`、`src/autoforge/af_grant.py:368`）。
- 名字空间隔离：`SceneDef.kind == "scene_def"`，绝不注册进 `SceneManager` 的 scenes 字典。

**验收判据（Given/When/Then）**

1. Given 一句「回家模式」，When 触发，Then 三步按序下发且回执 `code=GRANT_OK`、带 grant_id。
2. Given 第 2 步目标越出 grant 范围，When 触发，Then 仅第 1 步 `done: true`，第 3 步 `skipped_reason=cancelled_after_hard_deny`。
3. Given 档位超授但 ≤L2，When 触发，Then `needs_confirm`、出票、人工确认后仅本次放行。
4. Given L3 动作入清单，When 配置，Then 配置期即拒，不产生 SceneDef。
5. Given ask 走到第 5 轮被取消，When 结束，Then 无半成品落盘、审计有取消记录。

---

## 决策

- **D1 命名**：新对象叫 `SceneDef`（模块 `af_scene_kit.py`），⛔ 不叫 `Scene`——`src/autoforge/af_scene.py:61` 的 `Scene` 是"automation 组"，重名会把两套语义焊死在同一标识符上。
- **D2 每步独立过 grant**：不做"整场一次预检后放行"。授权在执行中途可能被撤销（`revoke` 立即生效，`src/autoforge/af_grant.py:383` 起），逐步判定才能让撤销在下一步就生效；这也与契约不变量"场景执行每步独立过 grant"一致。
- **D3 失败语义**：完成当前步、取消未执行步、不回滚已成步。设备动作（开灯/调温/播报）多不可逆，回滚会制造第二轮副作用；回执如实记现状比假装原子更诚实。
- **D4 uses 每 run 计一次**：`bump_use` 一次调用重写整份授权账本（`src/autoforge/af_grant.py:444` 起），逐步计数会让 8 步场景产生 8 次全盘写；计数语义取"一次执行"而非"一次下发"。
- **D5 档位不落 IR**：执行期现算，避免 IR 与 `classify_action` 双真源漂移；配置期只做"能不能入清单"的准入拒。
- **D6 condition 缺省 null**：条件是可选项，⛔ 不发明隐式条件（"回家模式"暗含时间窗会让触发行为不可解释）。
- **D7 单文件新增**：本单只落 `docs/architecture/v129_scene_object_design.md`；`af_scene_kit.py` 等实现另单落码，禁改文件仅点名。
- **D8 确认不升档**：`needs_confirm` 只走单次放行票，⛔ 不改 grant 的 `action_risk`——否则一次确认就等于永久扩权。

---

## 验收

| 门 | 判据 | 本单落点 |
|---|---|---|
| S1 sections | 五节齐备 | 设计/文件/测试/决策/验收 |
| S2 sentinel | 文末尾标 | `` |
| S3 citations | 只引 §1 出现的 path:line | 全部引用限 af_grant.py:196/209/219/222/235/368/383/444/458/486/557/594、af_scene.py:61/68/115/138/225、af_task_order.py:283、af_service.py:2117/2578/3092 |
| S4 file_list | 新增文件逐个成节 | `docs/architecture/v129_scene_object_design.md` 一节给完整内容 |
| S5 reasoning_ratio | < 0.7 | 推导压在决策节，正文为交付物 |
| S6 contract_cover | 契约节点全覆盖 | GrantCheck(`af_grant.py:196`)、GrantStore(`af_grant.py:235`)、GrantStore.list(`af_grant.py:368`)、Scene(`af_scene.py:61`)、SceneManager(`af_scene.py:115`)、_existing_owner(`af_service.py:2117`)、Claim(`af_task_order.py:283`) 均被引用 |
| S7 contract_fresh | 契约哈希不变 | 不改 af_scene.py / af_task_order.py，只点名 |
| S8 contract_cite strict | 引用落在契约行号区间 | 同 S3/S6 |
| S9 deliverables | 六项任务齐 | ①IR schema ②执行路径 ③配置期 ask ④三分边界 ⑤留痕回执 ⑥测试面 |

**人工验收**：① schema 能表达「开灯+开空调+播报」三步且顺序可定；② 硬拒场景下未执行步有明确取消标记；③ 回执每步带 grant_id 与单码 `code`；④ ask 五轮能收敛出完整 IR 且中途取消无残留；⑤ 文中对 af_scene / af_task_order 只点名、无改法。



<!-- tpsMetrics={"metricsVersion":4,"outputTps":271,"outputSseCount":1607,"thinkingTps":215,"thinkingSseCount":1614,"thinkingDurationSec":22.78,"thinkingTokens":4894,"thinkingCompleted":true,"thinkingTokenSource":"native_usage_reasoning_tokens","firstResponseTimeSec":1.785,"totalDurationSec":49.231,"outputDurationSec":47.446,"completionTokens":11552,"promptTokens":18463,"totalTokens":11552,"samples":[0,308,304,303,299,315,296,278,268,255,242,239,235,229,224,221,219,215,217,211,214,210,212,207,211,211,208,206,206,209,203,206,206,205,207,208,205,203,203,203,202,201,201,201,201,198,202,201,201,201,203,200,197,194,197,200,199,198,198,198,197,197,199,199,200,200,200,199,199,200,201,201,203,204,204,205,207,211,211,210,210,210,210,210,210,211,210,210,212,212,214,217,216,216,215,215,215,329,306,253,261,314,356,354,336,314,305,298,284,284,291,278,282,280,279,275,270,267,264,264,262,261,258,255,252,250,247,248,248,249,250,249,248,247,251,256,256,255,256,256,258,260,266,270,275,275,276,275,277,277,279,278,280,280,282,284,287,288,289,292,294,298,296,296,295,294,292,292,291,290,289,289,288,288,287,288,287,287,286,286,285,284,283,283,283,282,281,280,280,280,281,281,282,283,282,282,280,279,276,273],"thinkingEndIdx":96,"peakTps":356,"savedAt":1794400216034} -->
