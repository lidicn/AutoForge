# 豆包（db）执行路线图 · 第二批（2026-10-24 晚，1.13.4 已发布后）

> **PM：Claude（本会话）· 开发：豆包（db）**
> 前置：[db_roadmap_20261024.md](db_roadmap_20261024.md) 第一批已交付并发版（1.13.4）。
> 仓库/分支同上（`db/task-01` 已 push 到 master，新一批从 v1.13.4 tag 切）。
> **动新一批前必读第一批的 §1.3.1（台账同批）、§1.7（判据腿顺序污染）、§1.8（gates.sh 绿 ≠ 单测绿）**——那三条是第一批实测用 30 failed + 三门红换来的。

---

## 0. 本批背景与切活

1.13.4 已发布（master `a2da0a2` + tag `v1.13.4`），内容 = G1/G2/G4 + AST 收账 + G5/G8/G9 + G10 判据腿 + AF-001。
剩余缺口：**G3、G6、G7、G10（实施）**。本批只派两个可隔离的，另两个 PM 自己接。

**为什么这两个可隔离**（都已现读核过，不是猜的）：

| 文件 | 三份清单锚点数 | 结论 |
|---|---|---|
| `src/autoforge/af_draft.py` | 可观测性 0 / 进程模型 0 / 信任边界 0 / 发布线 0 | 零锚点，改它不撞台账（第一批 DB-01/02/03 已实证） |
| `src/autoforge/af_vhass/fake.py` | 同上，全部 0 | 零锚点 |

| 项 | 为什么不派 |
|---|---|
| **G6** | 断点在 `af_mcp.py:411` `_t_apply(store, args)` 不接 `current`（对比 `_t_save` 有 `current` 参数），`af_mcp.py` 撞发布线台账逐锚点钉 + `_TOOLS_WITH_CONTEXT` 语义 |
| **G7** | `af_draft.py:86` 的 `_staging = StagingStore()` 是进程级全局，落盘会**首次在该文件引入写盘代码**（现读零 `write_text`/`json.dump`/`open(`）⇒ 撞原子写门 + 有界缓存门 + 可能撞仓库卫生清单。PM 接 |

---

## 1. DB-05 · G10 实施：af_draft 给带 canary.duration 的 do 节点回填 on_timeout 边

**这是本批最重要的一项**——缺陷已定位、修法 PM 已裁决、判据腿 db 自己已写好（3 条，见 `tests/unit/test_canary_duration_chain_break.py`）。

### 1.1 缺陷（PM 已逐行确认）

`af_executor.py:332` 的 `edge = auto.pick_edge(node.id, {kind, "default"})`：
```
canary_observe 计时器 → timeout() → resume("on_timeout")
→ :248 pending_canary 恢复分支：漂移检查照做（verified/failed/unmodeled 三态都落，异常双兜底）
→ :332 pick_edge(d3, {on_timeout, default})
      af_draft 给 do 节点只发 then / on_error 边（af_draft.py:337-372 现读）
→ :334 edge is None ⇒ _terminate ⇒ 链条后半段永不执行
```
**执行器侧的 canary 恢复逻辑是好的，坏的只有最后一跳选边。**

### 1.2 PM 裁决：改 `af_draft.py`，⛔ 不改 `af_executor.py`

否掉执行器方案的理由（db 不用重新论证，但要知道为什么）：
`resume()` 是 **ask 应答 / pending_confirm 授权 / canary 恢复三条共用的唤醒路径**，在里面加 `then` 兜底会改变全部三种唤醒的选边语义；且 `af_executor.py` 在进程模型清单（生命周期/原语站点）与信任边界清单里都有在册锚点。

### 1.3 ⚠️ 两个现读约束（实施必须处理，否则做错）

1. **`pass_id` 在 do_list 循环之后才生成**（`af_draft.py` 第 6 步），循环内**无法引用它**。
2. **ask 节点的既有先例是 `on_timeout → pass`**（`af_draft.py:387`），但 G10 的语义**不能**指向 pass——
   那等于把后续 do 节点全部跳过，链还是断的，只是断得"干净"。

⇒ **做法：生成下一个节点时回填上一条边**。仅当该 do 节点带 `canary.duration` 时，在创建下一个节点后补：
```python
edges.append({"from": 上一个带 duration 的 do 节点 id, "to": 新 nid, "kind": "on_timeout"})
```

### 1.4 判据腿（先跑，必须红）

`tests/unit/test_canary_duration_chain_break.py` 已存在，**先跑确认它现在是绿的**（因为 PM 未修，它钉的是缺陷现状）：
```bash
PYTHONPATH=".../src" python -m pytest tests/unit/test_canary_duration_chain_break.py -q
# 期望：3 passed
```
改完后：**腿 1 会红**（`test_canary_with_duration_chain_break_d4_never_runs`）。
**按它自己的失败消息改成正向断言**（那一行已经预写了：「G10 已修？d4 现在跑了——若如此请把本腿改为正向断言」）。

⛔ **不许为了让测试绿而放宽断言。** 腿 3（canary 证据已落）和腿 2（不带 duration 时 d4 正常跑）必须继续过——它们是护栏，不是障碍。

### 1.5 配套：判据腿本身要补强

第一批书房场景的教训：当时的判据只验**终态**（AC=cool），漏判「后半段没跑」。所以本项要加：

| # | 新判据 | 期望 |
|---|---|---|
| A | 带 duration 的三节点链跑完 | `d4` 的**适配器调用真的发生**（不只是终态对）——断言 adapter.call 被调用过、且 action 是 d4 的那个 |
| B | 同图，d3 的 canary 证据 | 仍落 verified/unmodeled（不能因为改了边就让漂移检查被跳过） |
| C | 不带 duration | 行为不变（腿 2 已在） |

### 1.6 ⛔ 不许碰

`af_executor.py` / `af_canary.py` / `af_vhass/fake.py` / 一切禁区（第一批 §1.3）。
本项**只改 `af_draft.py` + `tests/`**。

### 1.7 验收

```bash
PYTHONPATH=".../src" python -m pytest tests/unit/test_canary_duration_chain_break.py -q
PYTHONPATH=".../src" python -m pytest tests/unit -q -k "draft or canary or watch"   # 顺序污染自检（§1.7）
PYTHONPATH=".../src" bash gates.sh                                                  # 贴完整输出
```

---

## 2. DB-06 · G3 方向①：action_map 补 set_state → climate 双服务映射

### 2.1 PM 先修正一处理解（db 按这个来，不要按册子里字面）

缺口册 G3 原文说「`set_state` 是 af_draft 意图侧一等动作…FakeHA 没有 `(*, set_state)` 条目」。
**现读修正**：

- **「开空调」其实已经能跑**：`af_draft.py:557` 的 action_map 已有 `"开空调": ("ha", "climate.turn_on")`，
  而 FakeHA 建模了 `("climate", "turn_on") → "cool"`（`af_vhass/fake.py:145`）。所以中文意图「开空调」
  走的是 `climate.turn_on`，**不是** `set_state`，已建模。
- **真正坏的是显式写 `set_state` 的路径**：action_map 没有 `set_state` 条目 ⇒ 落 `("ha", "set_state")`
  ⇒ FakeHA `SERVICE_STATE` 无此条目 ⇒ unmodeled ⇒ 状态不落。
- `af_shadow.py:201` 已有 `"set_state": "@params.state"` 的影子态映射——**说明影子侧认为它该有期望态**，
  仿真底座侧却未建模，这是两侧口径不一致（影子能算期望、底座不给落态）。

⇒ **本项范围收窄为：让显式 `set_state` 在 climate 域有正确的仿真落态**，不去动「开空调」那条已经对的路。

### 2.2 修法（双层，两层都做，但落点不同）

**第一层 · `af_draft.py` 的 action_map**（本项主体）：
```python
"set_state": ("ha", "climate.set_state"),   # 或按 2.3 的裁决映射到具体服务
```

**第二层 · `af_vhass/fake.py` 补建模**（`af_vhass` 三清单零锚点，可改）：
`set_state` 的新状态应由 `params.state` 决定 ⇒ 登记进 `DYNAMIC_SERVICES`
（与 `("climate", "set_temperature")` 同档，**不要**写死进 `SERVICE_STATE`）。

### 2.3 ⚠️ 一个必须 PM 裁的语义点——db 先按默认做，有异议写 blockers

`set_state` 是**状态提供者接口名**（`af_state.py:119`、`af_fault.py:176`、`af_runtime.py:131` 都在调
`states.set_state(entity_id, state, attrs)`），**不是 HA 域服务名**（HA 侧无 `climate.set_state` 服务）。

**PM 默认裁决：按字面建模，不发明映射。** 即 FakeHA 补 `(*, set_state)` 动态条目、新状态取
`params.state`，`af_draft` 保持 `("ha", "set_state")` 直通。理由：仿真底座的职责是**如实建模被调用的东西**，
不是替图作者纠正动作名——「这个动作名在生产 HA 侧可能不存在」是另一条缺口（册里已记：生产 watch 走 HA
adapter 是否支持 set_state 需另核），不该在仿真底座里偷偷改掉，否则仿真绿了而生产照坏。

**若 db 认为该按 climate.set_hvac_mode 映射**：写进 `docs/db_roadmap_blockers.md` 并给出理由，PM 裁。
⛔ 不要两种都做。

### 2.4 ⚠️ 本项最大的风险：IR 形状变化影响既有仿真期望

若修法导致**一个 do 节点展开成两个**（如书房场景当时手工拆 `set_hvac_mode` + `set_temperature`），
会改变 IR 节点数 ⇒ 所有既有仿真的期望值/节点数断言可能红。

**PM 默认裁决（按 2.3 的字面建模）不展开节点**，所以预期不触发此风险。但 db 必须自检：
```bash
PYTHONPATH=".../src" python -m pytest tests/unit -q -k "simulate or vhass or fake or shadow or expect"
```
红了就说明踩到了，**停下来写 blockers**，不要靠改断言收场。

### 2.5 判据腿（先写，必须红）

新建 `tests/unit/test_fakeha_set_state_modeling.py`：

| # | 场景 | 期望 |
|---|---|---|
| 1 | FakeHA 下调 `("climate", "set_state")`，params `{"state": "cool"}` | `service_effect` 返回 `("cool", …)`，**不再返回 None** |
| 2 | 同调，params `{"state": "off"}` | 落 `off` |
| 3 | 不带 state 参数 | **不崩**，按现有未建模/unmodeled 路径诚实处理（不许假绿） |
| 4 | `is_modeled(("climate", "set_state"))` | True（登记进 DYNAMIC_SERVICES 后） |
| 5 | 既有 `("climate", "set_temperature")` / `("climate", "turn_on")` 行为 | **不回归**（护栏腿） |
| 6 | `af_shadow` 侧 `set_state → @params.state` 与底座落态 | **两侧口径一致**（影子算得出的期望，底座真落得了） |

### 2.6 ⛔ 不许碰

`af_shadow.py`（影子态映射表已正确，动它撞信任边界台账）、`af_state.py`（生产状态源，fail-closed 门罩着）、
一切第一批 §1.3 禁区。本项**只改 `af_draft.py` + `af_vhass/fake.py` + `tests/`**。

### 2.7 验收

```bash
PYTHONPATH=".../src" python -m pytest tests/unit/test_fakeha_set_state_modeling.py -q
PYTHONPATH=".../src" python -m pytest tests/unit -q -k "simulate or vhass or fake or shadow or expect"   # §2.4 风险自检
PYTHONPATH=".../src" bash gates.sh
```

---

## 3. PM 自己接的（不派）

| 项 | 原因 |
|---|---|
| **G6** af_apply 主体透传 | 断点 `af_mcp.py:411`；`_t_save` 有 `current` 参数可参照，改 `_TOOLS_WITH_CONTEXT` 语义 |
| **G7** staging 持久化 | `af_draft.py` 首次引入写盘 ⇒ 原子写门 + 有界缓存门 + 卫生清单 |
| **G3 第二层的生产核验** | 「HA 侧是否真有 set_state 服务」要连生产容器核，不是代码活 |

---

## 4. 交付格式（同第一批，另加两条）

`docs/db_task_receipt.md` 追加，每项写：

```markdown
## DB-05 <标题>
- 判据腿：<文件>（N 条；修前红→修后绿；**改了哪几条的断言方向、为什么**）
- 改了哪些文件：<逐个列，标注是否在禁区清单里>
- 回填边的具体形状：<from/to/kind 三元素，贴在哪个位置>
- gates.sh 退出码 + 完整输出
- 新增 except-pass：0（若 >0 说明为什么）
- 顺序污染自检：<§1.7 那条命令的输出>
- 我没做的事：<明确列出>
```

**新增两条硬要求**：
1. **改了既有测试断言方向的，必须单独说明**——DB-05 会把 db 自己第一批写的腿 1 从「钉缺陷」改成正向，
   这是预期的，但要写清改前改后断言各是什么，PM 要能复核这个翻转是否合理。
2. **IR 形状若变化，贴出变化前后的节点/边计数**——这是 G3 类改动的风险面。

---

## 5. 时间盒与顺序

| 顺序 | 任务 | 预估 | 依赖 |
|---|---|---|---|
| 先 | **DB-05** | 中（修法已裁、判据腿已写好，主要是实现回填 + 翻转腿 1） | 无 |
| 后 | **DB-06** | 中（要读 FakeHA 建模表 + 影子侧口径） | 无，但 DB-06 改 `af_vhass/fake.py`，DB-05 改 `af_draft.py`，**两个文件不重叠可并行** |

**额度不够就保 DB-05**——它是已定位的真缺陷修复，DB-06 是仿真保真度改进。

**卡住超 30 分钟**：写 `docs/db_roadmap_blockers.md`（文件路径 + 卡在哪一行 + 试过什么），换下一个。

---

## 6. 本批新切错的预检（PM 自我修正）

第一批我把清单列禁区是切错的（已改 §1.3.1）。本批动 `af_draft.py` 与 `af_vhass/fake.py` 前，**先跑一次
`--write` 看这两个文件在不在任何清单里**——若在，按 §1.3.1 同批带上；若不在（预期如此，已核过），
照常提交即可。**不要假设，核一遍。**

---

## 7. PM 复核记录（2026-10-24 深夜，第二批交付后）

db 交付 DB-05（`d0c0198`）+ DB-06（`032b7c2`），PM 逐项复核**代码通过**：

- **DB-05 实现符合裁决**：`pending_canary_do` 在循环内维护，下一个节点生成后回填
  `on_timeout` 边，循环结束仍挂着的指到 `pass_id`。`prev = nid` 被正确移到 if/else
  之外（wait 分支原先不更新 `prev` 是潜在隐患，顺手修对了）。执行器未动。
  断言翻转合理：修前钉 `"d4" not in trace`，修后验 `"d4" in trace + state==DONE +
  mock.actions 含 light.turn_off`——正向证据，不是放宽。新增腿 4 断言 draft 输出边形状。
- **DB-06 实现符合字面建模裁决**：`is_modeled` 对任意 domain 的 `set_state` 返 True、
  `service_effect` 取 `params.state`、无 state 时保持 current 不假绿。腿 3 断言
  `in ("cool", None)` 是诚实口径。未发明到 `set_hvac_mode` 的映射。

**但 PM 自己抓到一次漏检，必须记**：

db 两笔的 gates.sh 全绿，PM 跑全量单测是 **3 failed**——红在
`tests/unit/test_observability_gate.py` 的三个 pin（sites 201→203、coded 87→89、
codes 84→86）。根因是 **1.13.4 发版时 AF-001 加了两枚带码 WARNING，我只跑了
`check_observability.py --write` 更台账（所以门禁绿），漏了同文件的测试 pin。**

⇒ **新增纪律 §1.9：台账 regen 与测试 pin 是两套东西，`gates.sh` 只读前者。**
改了 `src/autoforge/` 下任何文件后，除了 `--write` 台账，**还要跑对应的
`test_<那个门>_gate.py`**。这条对 db 同样适用（db 本批没踩到是因为它改的两个文件
零站点增量，但下一次未必）。

**另记一处观测矛盾**：db 回执称顺序污染自检有「1 pre-existing failed
（test_watch_lifecycle 锁回收，单独跑也红）」。PM 复跑
`test_watch_lifecycle_r6_audit.py` + `test_start_watch_identity.py` 共 41 passed、
`test_af_watch*.py` 19 passed，全绿。db 看到的红 PM 在同一棵树同一文件上跑不出来。
最终全量结果也未出现该失败（3 failed 全是观测性 pin）⇒ 判定为 db 自检时树状态
与复核时不同，非真实缺陷。**但这类"我看到的红你跑不出来"必须记档**，它是顺序
污染的典型前兆。
