# AutoForge 命名约定

> 版本：v1.0　日期：2026-09-14
> 状态：**生效**。本文件是命名的唯一依据，与代码/文档冲突时以本文件为准。
> 关联：`KICKOFF.md`（开工令）｜`IR_AND_RUNTIME.md`（IR v0.2.1）｜`HA_SEMANTIC_DIFF.md`

---

## 1. 四层命名（一张表说清）

| 层 | 写法 | 示例 | 说明 |
|---|---|---|---|
| **对外产品名** | `AutoForge` | "AutoForge = Agent 为中心的智能家居自动化平台" | 文档标题、README、仓库名、对外沟通 |
| **CLI 命令** | `forge` | `forge build` / `forge run` / `forge sim` | 短名，敲起来快；**不是** `autoforge` |
| **Python 包** | `autoforge` | `import autoforge`、`src/autoforge/` | 与产品名同形，便于 pip 安装 |
| **内部模块前缀** | `af_` | `af_ir` / `af_bus` / `af_time` / `af_adapters` | 模块文件名与顶层符号统一前缀 |

> **为什么 CLI 不叫 `autoforge`**：子命令每天要敲几十次，`forge build` 比 `autoforge build` 少 5 个字符且无歧义（工作区里没有第二个 forge）。产品名与命令名分离是常见做法（如 `kubectl` / Kubernetes）。

---

## 2. 生态代号

| 代号 | 项目 | 角色 |
|---|---|---|
| **AF** | AutoForge（`E:\NAS\AutoForge`） | 自动化工厂——HA 自动化的唯一写入方 |
| **MA** | memory-agent | 创造力源泉，提供带 conf 的假设 |
| **DB** | doubao-butler | 连接与执行，`ask` 的话术与对话由它承接 |
| **AutoFlow v1** | `E:\NAS\autoflow` | 前代项目，**另一条对话维护**；AutoForge 不读写其代码 |

> 历史写法 `AN`（AutoFlow Next）已废止，统一为 `AF`。

---

## 3. 术语改名对照（历史文档检索用）

| 旧写法 | 新写法 | 备注 |
|---|---|---|
| `Forge` | `AutoForge` | 产品名 |
| `Forge (AN)` | `AutoForge (AF)` | 生态代号 |
| `an_*` 前缀 | `af_*` 前缀 | 模块前缀 |
| `AN-Spec` | `AF-Spec` | agent 撰写面（G7，IR 冻结后再设计） |
| `AN Graph` | `AF Graph` | 唯一真相的图模型 |
| `E:\NAS\autoflow_next` | `E:\NAS\AutoForge` | 项目路径（`autoflow_next` 目录为空，已废弃） |

检索历史讨论时若遇到旧写法，按上表映射理解即可，**新代码与新文档一律用新写法**。

---

## 4. 代码内命名细则

- **模块**：`af_<领域>.py` 或 `af_<领域>/` 包，如 `af_ir/`、`af_bus.py`、`af_instance.py`、`af_executor.py`、`af_scheduler.py`、`af_scanner.py`、`af_nl.py`、`af_time.py`、`af_audit.py`、`af_runtime.py`、`af_cli.py`、`af_vhass/`。
- **测试**：`tests/unit/test_af_<领域>.py`、`tests/acceptance/test_case<NN>_<场景>.py`。
- **IR 标识**：`automation_id` 用蛇形小写（`study_day_light`）；节点 `node_id` 在同一 automation 内唯一，可寻址形式为 `automation_id:node_id`。
- **常量**：模块级全大写（`EDGE_PRIORITY`、`NODE_KINDS`、`L3_ACTIONS`）。
- **枚举值**：IR 里的节点/边/状态一律小写蛇形（`on` / `on_timeout` / `suspended`），与 JSON Schema 一致。

---

## 5. 改名落地清单（本次已完成）

- [x] `KICKOFF.md`：标题、冷启动路径、产品名、生态分工、命名小节、项目路径、`AN-Spec`→`AF-Spec`、版本 v1.0→v1.1
- [x] `IR_AND_RUNTIME.md`：标题、头部元信息、`AN Graph`→`AF Graph`、`AN-Spec`→`AF-Spec`、§13-3 命名条目、版本 v0.2→v0.2.1
- [x] 新增 `docs/NAMING.md`（本文件）
- [x] 新增 `docs/HA_SEMANTIC_DIFF.md`
- [x] 新增 `docs/G1_ACCEPTANCE.md`
- [ ] 代码层：全部模块以 `af_` 前缀落地（G1 实施中，随各模块创建逐步完成）
- [ ] `README.md`：产品名 AutoForge + CLI `forge` 用法

> 改名**仅涉及展示名与符号前缀**，不涉及 IR 语义——`IR_AND_RUNTIME.md` 的模型定义未在本次改名中修改。
