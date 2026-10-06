# AutoForge 第十五轮审计报告：安全闸绕过（反向可达性）

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 本轮主题：**反向可达性分析——从写盘海点向上追，看每条路径是否都过安全闸**
> 判定标准：严格档 —— **绕过必须走完整链路实测**（存 → 启用 → 读回确认）
> 报告日期：2026-10-06

---

## 一、执行摘要

前十四轮几乎都是"正向"审计：从入口追到结果，看到"这里有闸、那里有校验"。第十五轮换方向——**从写盘海点出发做反向可达性**，问的不是"闸有没有"，而是"**闸是不是唯一入口**"。

**找到 1 个 High 缺陷，性质与前十轮都不同：它不是某处代码写错，而是同一份数据有两条写入路径，其中一条绕过闸。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R15-01** | 🔴 High | **`forge store save` 绕过静态扫描闸**——同一份归档，service 层过闸拒绝、CLI 直存放行；完整链实测三步全通 | `af_cli.py:789` |

### 实测证据（三条路径对照）

同一个触碰 Tier-0 受保护实体 `lock.front` 的 IR：

| 路径 | 过闸 | 结果 |
|---|---|---|
| `save_graph`（MCP / API / 审批回放） | ✅ `StaticScanner` | ❌ 拒绝：`ServiceError「拒绝归档：IR 未通过静态扫描（2 个错误）」` |
| **`forge store save`（CLI）** | ❌ **无** | ✅ **归档成功 v1** |
| `forge store enable`（CLI，重存） | ❌ **无** | ✅ `enabled=True` |

完整链实测：

```
步骤1  forge store save  → 归档 v1  （无静态扫描）
步骤2  forge store enable → [{'name': 'bypass', 'version': 2}]  （无静态扫描）
步骤3  读回：automations=1
        enabled=True
        动作=lock.unlock 目标=lock.front ← Tier-0 保护区

⇒ 受保护实体 lock.front 的解锁动作已进入「已启用」归档，全程未过闸
```

---

## 二、工作流迭代

| 轮次 | 方法 | 确认缺陷 |
|---|---|---|
| 十二 | 候选自动分诊（T1/T2/T3） | 2 |
| 十三 | 模板反查（只认直接调用） | 2 |
| 十四 | 调用图展开 + 跨进程边界 | 3 |
| **十五** | **反向可达性 + 闸唯一性** | **1** |

### 新增资产

**`scripts/scan_round15.py`**（243 行）—— 多级反向可达 + 闸识别 + 绕过判据：

1. 构建正向调用图（98 文件 / 2091 函数）
2. 识别**写盘海点**（`GraphStore.save` / `atomic_write_text` / `PersistStore.save` / `record_*`）
3. 识别**闸函数**（`StaticScanner(...).scan()` / `validate_automation` / 权限校验）
4. 对每个海点做**反向可达性**，输出"无闸路径"

### 一次噪音失控与修正

首版输出 **956 条无闸路径**——因为把任意 `atomic_write_text` 都当危险海点（持久化状态、遥测、快照都是合法落盘，不需要过安全闸）。

修正做法：**不做泛化海点扫描，改为精确枚举"能写 IR 归档"的入口**（`GraphStore.save`），再逐个判闸。14 处直写点 → 9 处无闸 → 人工分诊出 1 处真绕过。

**教训**：反向可达性必须**先限定海点语义**（"写什么"），否则可达性分析会淹没在噪音里。

### 四个新范式（已记入 lessons 第 51–54 条）

**51** 找绕过要**反向做**：从海点向上追，不是从入口正向追（正向永远看到"有闸"）
**52** 闸的数量不重要，**闸是不是唯一入口**才重要
**53** 绕过必须走完整链路实测（存 → 启用 → 读回），不能只证明"能写进去"
**54** 帮助文本没提"绕过闸" = 设计意图不明确的信号

---

## 三、确认缺陷

### 🔴 R15-01　`forge store save` 绕过静态扫描闸

**位置**：`src/autoforge/af_cli.py:789-792`（`store_save`）

```python
@store_app.command("save")
def store_save(path, name="", note="", root=DEFAULT_STORE_ROOT):
    """保存一个 Graph 版本 + 置信度快照。"""
    graph = _load(path)
    store = GraphStore(root)
    key = name or path.stem
    version = store.save(graph, key, note)      # ← 直接写盘，无 StaticScanner
    store.save_conf(ConfidenceStore().seed(graph), key, note)
    typer.echo(f"· 已归档 {key} v{version}（root={root}）")
```

**对照——service 层 `save_graph`（`af_service.py:375`）**：

```python
scan = StaticScanner(graph, ...).scan()
if not scan.ok:
    raise ServiceError(f"拒绝归档：IR 未通过静态扫描（{len(scan.errors)} 个错误）", ...)
```

**闸本身写得很好**——实测它确实正确拒绝了触碰 Tier-0 的 IR（"2 个错误"）。问题不在闸，在**入口不唯一**。

### 14 处 `store.save` 直写点的分诊结果

| 位置 | 是否过闸 | 判定 |
|---|---|---|
| `af_service.py:419` `save_graph` | ✅ | 主入口，正确 |
| `af_service.py:1321/1340` `bootstrap_examples` | ✅ | 有 scan 且判 ok |
| **`af_cli.py:789` `store_save`** | ❌ | **R15-01** |
| **`af_cli.py:887` `svc_enable_disable`** | ❌ | 重存已归档图（R15-01 的第二段） |
| `af_premiere.py` 6 处 `self.save()` | — | 存的是**首演码仓库**，非 IR 归档，不适用 |
| `af_runtime.py:143` | — | 存**实例状态**，非 IR，不适用 |
| `af_shadow.py:395` | — | 存 **shadow_log**，非 IR，不适用 |

### 为什么是真绕过，不是"内部工具"

三条理由：

1. **完整链可达**：存 → 启用 → 归档处于 `enabled=True`，watch 会部署它。不是"存进去没人用"。
2. **归档目标是同一份数据**：`forge store save` 与 `save_graph` 写的是同一个 `GraphStore`，`store enable` 随后会启用它——**绕过闸产生的归档与过闸产生的归档在下游完全无法区分**。
3. **文档没有声明它绕过闸**：docstring 只有「保存一个 Graph 版本 + 置信度快照。」；而 `forge build` 有专门的 `── 安全闸（forge build）──` 输出段（`af_cli.py:74` `_gate`）。

第三条是关键：**如果这是有意设计的低级通道，文档会说"此命令绕过静态扫描，仅供调试"**。它没说——说明大概率是"内部工具忘了补闸"。

### 不要夸大：它绕过了什么、没绕过什么

| 项 | 结论 |
|---|---|
| 静态扫描闸（Tier-0 保护、僵尸触发源、ask 弃用等） | ❌ **被绕过** |
| 审批门（`submit_pending` → `approve_pending`） | ✅ 未涉及——CLI 是本地操作，本就不走审批 |
| 授权码路径（第一轮 HIGH-1） | ✅ 未涉及——`_t_save` 仍走 `approve_pending` → `save_graph`，扫描闸生效 |

**准确表述**：这是一个**本地 CLI 通道**绕过静态扫描闸。它不是远程攻击面（需要拿到 CLI 执行权限），但它让"IR 归档前必须过闸"这条约束**不再是全局不变量**——任何有 CLI 的人（包括 CI、脚本、运维习惯）都能绕开。

而第十三轮的 R13-01（`device_acl.json` 损坏 → Tier-0 保护整段跳过）与本条形成叠加：一条让"保护规则可能失效"，一条让"违规图可能绕过闸"。两者独立，但都指向同一个结论——**Tier-0 保护没有单一强制点**。

**修复建议**（三选一，按推荐度）：

```python
# A) 最推荐：store_save 复用 save_graph 的闸（保持唯一入口）
def store_save(...):
    graph = _load(path)
    st = StaticScanner(graph, ...).scan()
    if not st.ok:
        typer.echo(f"✗ 拒绝归档：IR 未通过静态扫描（{len(st.errors)} 个错误）")
        for e in st.errors: typer.echo(f"  · {e}")
        raise typer.Exit(code=1)
    ...

# B) 显式标注 + 强制开关：默认拒绝，--force 才绕过且留痕
# C) 只做 B 的留痕部分（最弱：至少让绕过可被审计发现）
```

**回归验证清单**：
1. `forge store save` 传触碰 Tier-0 的 IR → 应**拒绝**（当前：成功）
2. 传合法 IR → 仍应成功（不能把正常路径也堵死）
3. `forge build` 的闸输出不受影响（防止改坏唯一有闸的那条）

---

## 四、本轮验证通过（确认无问题）

| 项目 | 验证方式 | 结论 |
|---|---|---|
| **`StaticScanner` 闸本身** | 实测 | ✅ **正确工作**——实测拒绝触碰 Tier-0 的 IR，报"2 个错误" |
| **`save_graph` 主入口** | 读实现 + 实测 | ✅ scan → 判 ok → 拒绝，正确 |
| **`bootstrap_examples`** | 读实现 | ✅ 有 scan 且判 ok，内置样例也过闸 |
| **`_t_save` 授权码路径** | 读实现 | ✅ 走 `submit_pending` → `approve_pending` → `save_graph`，扫描闸生效 |
| **`af_premiere` / `af_runtime` / `af_shadow` 直写** | 读实现 | ✅ 写的都不是 IR 归档（首演码 / 实例状态 / shadow 日志），不适用该闸 |
| **`svc_enable_disable`** | 读实现 | ✅ 它只翻转 `enabled` 并重存——**设计上不该重复扫**；问题在上游 `store_save` |

---

## 五、已排除（rejected）

| 候选 | 理由 |
|---|---|
| **首版 956 条"无闸路径"** | 海点定义过泛——把状态持久化 / 遥测 / 快照落盘都算了进去，这些是合法落盘不需要过安全闸 |
| **`af_premiere.py` 6 处 `self.save()`** | 存首演码仓库，非 IR 归档 |
| **`af_runtime.py:143`** | 存实例状态（PersistStore），非 IR |
| **`af_shadow.py:395`** | 存 shadow 日志 |
| **`af_cli.py:887` `svc_enable_disable`** | 它重存的是**已归档的图**——若上游都过闸，它不需要重复扫。缺陷在上游，不单列 |

---

## 六、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R15-01 `store_save` 补闸 | 约 8 行 | 让"IR 归档前必须过闸"成为全局不变量 |

**为什么只修这一条就够**：14 处直写点里，只有 `store_save` 是"能写入 IR 归档且无闸"的入口。补上它，所有写 IR 的路都过闸。

---

## 七、十五轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码 | 22 |
| 七~十四 | 生命周期/门禁/测试/配置/持久化/工具/分诊/模板/跨进程 | 18 |
| 十五 | 安全闸绕过 | 1 |

**本轮只有 1 条缺陷，但它的性质与前十轮全都不同。**

前十四轮找到的，是"某处代码做错了"——递归没设上限、fd 关了两次、异常吞掉不留痕、读失败静默返回空。这些是**局部错误**，修一处少一处。

第十五轮找到的是：**代码全对，但架构上"闸不是唯一入口"**。`StaticScanner` 写得很好，`save_graph` 挂得也对，问题出在**同一个写入目标还有第二条路**。

这类缺陷前十四轮的方法全都发现不了——因为：
- 正向审计会看到"有闸"（`save_graph` 确实有闸）✓
- 模板反查需要"仓内有对照实现"——而这里**没有**"带闸的 CLI 存档函数"可作模板
- 调用图展开只看单层委托，不看"谁还能写到同一份数据"

**只有反向可达性（从海点向上追所有入口）能看到它。**

### 与前几轮的叠加

| 轮次 | 缺陷 | 共同指向 |
|---|---|---|
| 十三 R13-01 | `device_acl.json` 损坏 → Tier-0 保护消失 | Tier-0 保护**依赖一个可被破坏的文件** |
| 十五 R15-01 | CLI 直存绕过静态扫描闸 | Tier-0 保护**依赖"只有一条写入路"这个假设** |

两条独立，但结论一致：**Tier-0 保护目前没有单一强制点**。任何"规则文件损坏"或"多一条写入路径"都会让它在某个方向上失效。

**给工程团队的一句话**：你们把闸本身做对了（实测拒绝违规 IR，这点比很多项目强），但闸的价值取决于**"是不是所有路都得过它"**。建议把"IR 归档前必须过 `StaticScanner`"提升为**架构不变量**，并用你们现成的 AST 门来钉——写个 `check_archive_gate.py`：枚举所有 `GraphStore.save` 调用点，断言每个的上游都出现 `StaticScanner` 或显式豁免登记。**你们已经有 15 个 `check_*.py` 门禁了，加第 16 个成本很低，而这一条能永久封住"新增写入路径忘记补闸"这个类别。**

---

## 八、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| **反向可达性 + 闸绕过分析器** | **`audit-env/scripts/scan_round15.py`** |
| 闸绕过明细 | `audit-env/reports/round15-guard.json` |
| 调用图展开 + 污点传播（P2/P5/P6/P7） | `audit-env/scripts/scan_round14.py` |
| 同形状传播扫描器（P1–P4） | `audit-env/scripts/scan_round13.py` |
| 候选自动分诊器（T1/T2/T3） | `audit-env/scripts/triage_round12.py` |
| 误报修正手册（50 条 + 本轮 4 条 = **54 条**） | `audit-env/scripts/lessons-round2.md` |
| ADM-auditkit（含自建 CLI） | `/data/workspace/adm-auditkit/ADM-auditkit-main/auditkit` |

### 本轮新增 lessons（51–54）

- **51** 找绕过要**反向做**：从写盘海点向上追，不是从入口正向追
- **52** 闸的数量不重要，**闸是不是唯一入口**才重要
- **53** 绕过必须走完整链路实测（存 → 启用 → 读回），不能只证明"能写进去"
- **54** 帮助文本没声明"绕过闸" = 设计意图不明（大概率是遗漏，非有意）

---

## 附：环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12（项目声明 `>=3.11`） |
| 本轮实测 | 三路径对照实测（`save_graph` 拒绝 / `store_save` 放行 / 三步完整绕过链） |
| 调用图规模 | 98 文件 / 2091 函数 |
| 噪音修正 | 首版 956 条 → 精确枚举 14 处 `store.save` 直写点 → 确认 1 条 |
| 仓库状态 | 探针与变异均已还原 |
