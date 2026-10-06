# AutoForge 第十八轮审计报告：入口对等性（汇聚点比对）

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 本轮主题：**同一个操作在 MCP / API / CLI 三个入口，是否汇聚到同一套约束**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 报告日期：2026-10-06

---

## 一、执行摘要

第十五轮发现 R15-01：`forge store save`（CLI）绕过静态扫描闸，而 `save_graph`（MCP/API/审批）过闸。当时它是用"针对单个操作的反向可达性"手工挖出来的。

第十八轮把这条路**推广成通用方法**：枚举三个入口面（MCP 31 个 `_t_*`、API 80 个 `api_*`、CLI 39 个命令，共 150 个 handler），对每个写操作找出各入口的**终态函数**，比对是否汇聚到同一处。

**结果：方法本身验证通过（成功复现 R15-01），并找到 1 个新的同源缺陷。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R18-01** | 🔴 High | **`forge store enable` 绕过爆炸半径护栏**——服务层拒绝（20 > 上限 8），CLI 直接启用 20 个归档 | `af_cli.py:869` `svc_enable_disable` |

### 方法验证：扫描器成功复现第十五轮的 R15-01

```
══ save(归档/部署)   🔥 高置信（一带闸一不带）
   MCP → ['approve_pending', 'submit_pending']
   CLI → ['GraphStore', 'save', 'save_conf']
      approve_pending  ✓带闸     af_service.py:585
      save             ❌无闸    af_store.py:154      ← R15-01，已在上上轮确认
```

这说明"汇聚点比对"是可复用的判据，不是一次性运气。

---

## 二、工作流迭代

| 轮次 | 方法 | 确认缺陷 |
|---|---|---|
| 十五 | 反向可达性 + 闸唯一性（**人工挖单个操作**） | 1 |
| 十六 | 失败方向全量枚举 + RMW 模板反查 | 2 |
| 十七 | 契约声明提取 + 声称/实现对照 | 1 |
| **十八** | **入口对等性（汇聚点比对，15 轮方法通用化）** | **1** |

### 新增资产

**`scripts/scan_round18.py`**（235 行）：

1. 收集三个入口面的 handler（150 个）
2. 抽取每个 handler 的**终态调用**（`svc.X` / `store.Y` / `GraphStore.Z`）
3. 按操作分组，比对终态函数集合
4. 对终态函数做**深度 3 传递闭包**判"是否带闸"
5. 输出：*一带闸一不带* ⇒ 高置信候选

### 两次自我修正（本轮的真正收获）

**修正 1（lesson 63）：v1 判据错了，7/10 全是假阳性。**

v1 直接跨面比对"约束以什么形式出现"（auth/gate/pending/blast），结果几乎每个操作都判"auth 仅出现在 API"——但三个面的鉴权模型本来就不同（API 用 `Depends(_write)`，MCP 用令牌 subject + scope，CLI 是本地进程）。

改成比**汇聚点**（终态函数）后，假阳性归零。R15-01 的真正形状不是"CLI 没有闸"，而是**两条路汇聚到不同的终态函数**。

**修正 2（lesson 64）：一个把整个结论搞反的低级 bug。**

`unparse()` 为了展示截断到 160 字符，而 `build_gate_index` 误用了它——`save_graph` 函数体被截短后 `StaticScanner` 根本不在被检查的文本里，**全表误判"无闸"，R15-01 被完全掩盖**（带闸和无闸都显示 ❌，差异消失）。

症状极具迷惑性：**扫描器正常输出、无报错、只是结论全错**。已加 `full_unparse()` 专门用于内容判定。

### 四个新范式（已记入 lessons 第 63–66 条）

**63** 跨入口比对要比**汇聚点**，不要比"约束以什么形式出现"
**64** 判定"函数体内是否含 X"**绝不能用截断版 unparse**（本轮把自己坑了）
**65** 闸的判定必须做**传递闭包**（闸常在被调用方，如 `approve_pending` → `save_graph`）
**66** 汇聚点不一致 ≠ 有缺陷——必须人工确认"缺失的约束是否改变安全语义"

---

## 三、确认缺陷

### 🔴 R18-01　`forge store enable` 绕过爆炸半径护栏

**位置**：`src/autoforge/af_cli.py:869` `svc_enable_disable`（CLI），对照 `af_service.py:313` `enable_by_tag`（服务层）

**服务层 `enable_by_tag`**（MCP `_t_enable` 与 API `/api/graphs/enable` 都走它）：

```python
def enable_by_tag(store, tag, enabled, allow_bulk=False):
    """v1.3.0：默认受**爆炸半径**约束——先把受影响自动化条数算出来再决定要不要做，
    **绝不做一半才报错**。显式传 allow_bulk=True 绕过。"""
    ...
    if not allow_bulk:
        _check_blast(total, f"按标签 {tag!r} 批量{'启用' if enabled else '禁用'}")
```

**CLI `svc_enable_disable`**：

```python
def svc_enable_disable(store, names, enabled):
    """对给定归档名批量翻转 enabled（直接落最新版本的新版本）。

    与 `af_service.enable_by_tag` 同源逻辑；CLI 这里已展开为具体名字列表，
    逐个保存新版本（API 层按标签入口走 `svc.enable_by_tag`）。"""
    for name in names:
        ...
        new_version = store.save(graph, name, note=f"v0.6.0 批量{'启用' if enabled else '禁用'}")
        # ← 全程无 _check_blast
```

**实测**（20 个归档全部打 tag=`prod`，`AUTOFORGE_BLAST_RADIUS=8`）：

```
══ 路径 A：服务层 svc.enable_by_tag（MCP / API 走这条）══
   ✅ 拒绝: ServiceError: 按标签 'prod' 批量启用 会影响 20 条自动化，
      超过爆炸半径上限 8；请拆分为更小的操作（或调高 AUTOFORGE_BLAST_RADIUS）

══ 路径 B：CLI svc_enable_disable（forge store enable 走这条）══
   ❌ 执行成功，启用 20 个归档 —— **未做爆炸半径检查**
```

### 判定它是"遗漏"而非"有意"，靠两条证据

1. **docstring 自称"同源逻辑"**——声明两者等价，但实现缺少了其中一条护栏。这是 lesson 54 的判据（声明没说"我绕过某约束"，就是遗漏）。
2. **CLI 入口确实支持 `--tag`**：`forge store enable --tag prod` → `_names_for_tag` → `svc_enable_disable`。也就是说 CLI 有一条**与服务层按标签入口完全对应**的路径，而这条路径少了爆炸半径检查。

### 严重度说明（不要夸大）

| 项 | 结论 |
|---|---|
| 后果 | 一次操作可启用/禁用任意多条自动化，不受 `AUTOFORGE_BLAST_RADIUS` 约束 |
| 是否绕过安全闸 | ❌ 否——`enabled` 翻转不重新过 `StaticScanner`，但**服务层的 `enable_by_tag` 也不过**（它只查爆炸半径）。两者在这一项上一致 |
| 是否远程可达 | ❌ 否——需要 CLI 执行权限（本地/CI/脚本） |
| 与 R15-01 的关系 | **同源同形**：同一个 CLI 通道，一处绕过静态扫描闸、一处绕过爆炸半径护栏 |

**准确表述**：爆炸半径护栏"一次别改太多"这条约束，在 CLI 通道上不成立。与 R15-01 合并看，结论是——**CLI 通道整体上不在服务层的护栏集合内**。

### 修复建议

```python
def svc_enable_disable(store, names, enabled, allow_bulk=False):
    # 与 enable_by_tag 对齐：先算清影响面再决定要不要做（绝不做一半才报错）
    planned = []
    total = 0
    for name in names:
        try:
            v = store.latest(name)
            if v is None: continue
            g = store.load(name, v)
        except (FileNotFoundError, IRValidationError, OSError):
            continue
        planned.append((name, g)); total += len(list(g))
    if not allow_bulk:
        svc._check_blast(total, f"CLI 批量{'启用' if enabled else '禁用'}")
    ...
```

**回归验证**：
1. `AUTOFORGE_BLAST_RADIUS=8` + 20 个归档 → `forge store enable --tag prod` 应**拒绝**（当前：成功）
2. 数量在上限内 → 仍应成功（不能堵死正常路径）
3. `svc.enable_by_tag` 行为不变

---

## 四、扫描结果全表

| 操作 | MCP | API | CLI | 判定 |
|---|---|---|---|---|
| **save** | `approve_pending` ✓ | — | `store.save` ❌ | 🔥 **R15-01（已确认）** |
| **enable/disable** | `enable_by_tag` ✓ | `enable_by_tag` ✓ | `store.save` ❌ | 🔥 **R18-01（本轮新增）** |
| **import** | `import_store` ✓ | — | `import_bundle` ✓ | ✓ 两侧都带闸，收敛 |
| **export** | `export_store` ❌ | `export_store` ❌ | `export_bundle` ❌ | ⓘ 只读操作，无需闸 |
| **undo** | — | `undo_*` 包装 | `UndoStore.revert` | ⓘ 汇聚点不同但**功能等价**（都到 `revert(confirm=)`） |
| **tags** | `set_graph_tags` | `set_graph_tags` | `store.set_tags` | ⓘ 标签是元数据，service 侧亦无安全闸 |

### 汇聚点一致 ≠ 等价、不一致 ≠ 缺陷（lesson 66）

**undo 是最好的反例**：扫描器判它"汇聚点不一致"（API 走 service 包装、CLI 直接 `UndoStore`），但人工读代码确认**两者都到 `revert(deploy_id, adapter, confirm=confirm)`**，风险域二次确认、时间窗、fail-closed 四道闸**都在 `revert` 内部**，因此功能等价——**非缺陷**。

这提醒：**扫描器只负责把不一致列出来**，"是否构成缺陷"必须人工确认。判"是"的关键标准是——**缺失的那个约束是否改变了安全语义**。

---

## 五、本轮验证通过（确认无问题）

| 项目 | 方式 | 结论 |
|---|---|---|
| **CLI `undo` 的 fail-closed 四道闸** | 读实现 + 对照服务层 | ✅ 与 API 等价——未知 id 拒、超窗拒、风险域需 `--confirm`、不可映射跳过告警；且用 `_LazyHAAdapter` 惰性构造，被拒场景不要求 HA 就绪（设计周到） |
| **CLI `store import`** | 汇聚点比对 | ✅ 走 `store.import_bundle`，与服务层 `import_store` **都带闸** |
| **`enable_by_tag` 的"绝不做一半才报错"** | 读实现 | ✅ 先算 `planned` 再 `_check_blast`，只在不满足时才拒绝，**确实不做一半** |
| **`submit_pending` / `approve_pending` 带闸** | 传递闭包 | ✅ 经 `save_graph` 间接带闸——证明审批回放路径是过闸的 |
| **export 系列无闸** | 语义判断 | ✅ 只读导出，无需安全闸 |

---

## 六、已排除（rejected）

| 候选 | 理由 |
|---|---|
| **v1 的 7 条 "auth 仅出现在 API"** | 三个面鉴权模型不同，跨面比 auth 无意义（lesson 63） |
| **undo 汇聚点不一致** | 功能等价，都到 `revert(confirm=)`（lesson 66） |
| **tags 汇聚点不一致** | 标签是元数据，服务层 `set_graph_tags` 本身也无安全闸，两侧一致 |
| **export 无闸** | 只读操作（lesson 66） |
| **`_t_build` / `api_build` / CLI `build`** | 三者都直接构造 `StaticScanner`——**本身就是闸**，无需"带闸"判定 |

---

## 七、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R18-01 `svc_enable_disable` 补爆炸半径检查 | 约 8 行 | 与 R15-01 同源，建议一并修 |
| **P1** | R15-01 `store_save` 补静态扫描闸 | 约 8 行 | 上一轮已报，本轮方法再次确认 |

**这两条其实是同一件事的两半**：CLI 通道整体不在服务层护栏集合内。建议一次性做——把 CLI 的写操作统一收敛到服务层函数（或在 CLI 层补上同样的护栏），而不是逐条补。

---

## 八、十八轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码 | 22 |
| 七~十七 | 生命周期/门禁/测试/配置/持久化/工具/分诊/模板/跨进程/闸/失败方向/契约声明 | 22 |
| 十八 | 入口对等性 | 1 |

### 本轮把第十五轮的"一次性发现"变成了"可复用判据"

第十五轮靠人工挖出 R15-01，第十八轮扫描器**自动复现**了它，并顺带找到了同形的 R18-01。这是审计工作流最重要的一次升级：**从"这次想到了"变成"以后都能找到"**。

### 但更要紧的是：两条缺陷指向同一个根因

| 缺陷 | CLI 缺的护栏 |
|---|---|
| R15-01 | 静态扫描闸（`StaticScanner`） |
| R18-01 | 爆炸半径护栏（`_check_blast`） |

**不是两个独立疏忽，是同一个架构事实**：CLI 直接用 `GraphStore` 的原生方法，而服务层函数（`save_graph` / `enable_by_tag`）才是护栏的载体。只要 CLI 绕过服务层，就自动绕过全部护栏。

### 与"孤岛"清单的关系

前几轮归纳的"正确范式孤岛"（`atomic_write_text`、`_env_number`、`af_auth` 毒化标志、`af_telemetry` 坏行保留、`af_insight_queue` 记账、`af_predict._quarantine`、`af_premiere.consume`）本轮要**新增一格**——`enable_by_tag` 的爆炸半径检查。

它写得很好（"先算清影响面（不落盘），超阈值直接拒——避免改了一半才失败"），但**只有服务层有**。CLI 那份 docstring 还自称"同源逻辑"——**声明了等价，实现却不等价**，这是第十七轮"声明 vs 实现"主题在入口维度的又一次出现。

**给工程团队的一句话**：十五轮我建议"把 IR 归档前必须过闸提升为架构不变量"。十八轮的答案更明确——**不变量应该是"所有写操作必须经由服务层"，而不是逐条列举护栏**。因为护栏会新增（今天有扫描闸和爆炸半径，明天可能有别的），只要 CLI 还能直接调 `GraphStore`，每新增一条护栏就自动多一处绕过。

具体做法：写第 16 个门禁 `check_cli_writes.py`——断言 `af_cli.py` 中不出现 `store.save(` / `store.set_tags(` / `store.import_bundle(` 等**裸 store 调用**，只允许 `svc.*`。**你们已经有 15 个 `check_*.py` 了，加这一个就能永久封住整个类别。**

---

## 九、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| **入口对等性 / 汇聚点比对扫描器** | **`audit-env/scripts/scan_round18.py`** |
| 对等性明细 | `audit-env/reports/round18-parity.json` |
| 契约声明提取器（七类 / 557 条） | `audit-env/scripts/scan_round17.py` |
| 失败方向审计扫描器（D1/D2/D3） | `audit-env/scripts/scan_round16.py` |
| 反向可达性 + 闸绕过分析器 | `audit-env/scripts/scan_round15.py` |
| 调用图展开 + 污点传播 | `audit-env/scripts/scan_round14.py` |
| 同形状传播扫描器（P1–P4） | `audit-env/scripts/scan_round13.py` |
| 候选自动分诊器（T1/T2/T3） | `audit-env/scripts/triage_round12.py` |
| 误报修正手册（62 条 + 本轮 4 条 = **66 条**） | `audit-env/scripts/lessons-round2.md` |

### 本轮新增 lessons（63–66）

- **63** 跨入口比对要比**汇聚点**，不要比"约束以什么形式出现"（v1 因此 7/10 假阳性）
- **64** 判定"函数体内是否含 X"**绝不能用截断版 unparse**——本轮因此把结论完全搞反且**无任何报错**
- **65** 闸的判定必须做**传递闭包**（`approve_pending` → `save_graph`）
- **66** 汇聚点不一致 ≠ 缺陷；判据是"缺失的约束是否改变安全语义"（undo 是反例）

---

## 附：环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12（项目声明 `>=3.11`） |
| 本轮实测 | 1 组（20 归档 / 上限 8，服务层拒 vs CLI 放行）+ 汇聚点扫描 150 handler |
| 扫描规模 | MCP 31 / API 80 / CLI 39 handler；service 层函数索引 199（传递闭包深度 3） |
| 自我修正 | 2 处（判据假阳性 / unparse 截断导致结论反转） |
| 仓库状态 | 探针与变异均已还原 |
