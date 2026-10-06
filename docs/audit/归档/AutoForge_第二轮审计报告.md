# AutoForge 第二轮审计报告（工作流迭代 + 增量确证）

- **审计对象**：`lidicn/AutoForge`（不变：`src/autoforge` 主包 + `ui/` 前端 + `scripts/` 门禁 30 个）
- **审计工具链**：`lidicn/ADM-auditkit`（本轮先迭代修复，再重跑）
- **审计焦点**：稳定性与功能性缺陷（同第一轮口径）
- **本轮轮次**：`round-002`（重跑）+ `round-003`（含新台账复核）
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-003/`
- **报告日期**：2026-10-06

---

## 一、本轮做了两件事

1. **迭代工作流**：修掉 ADM-auditkit 自身的两个缺陷（都不是"分析规则不准"，而是"结果被悄悄改少/改多"），并验证修复有效
2. **增量确证**：在剩余 high 命中里继续人工确证，新增 2 项确证缺陷（F6、F7），补充假阳性登记

---

## 二、工作流迭代：修掉两个"改结果"的缺陷

### 2.1 缺陷 W1：`scripts/` 目录漏扫 → 制造 125 条"缺陷消失"假象

**现象**：第二轮跑完，`cross-round-diff` 报 `gone=125`，看起来像"代码变好了、125 个缺陷被修掉了"。实际一条都没修。

**根因**：`auditkit` CLI 在拼装 `--extra-root` 时，用的是 `root/"scripts"`，而 `root` 指向 `/data/workspace/repos/af/src` → 拼出 `/data/workspace/repos/af/src/scripts`，该路径不存在，于是整个 `scripts/` 目录（30 个门禁脚本）被跳过。第一轮我手工跑的时候用的是仓库根的 `scripts/`，所以两轮扫描范围根本不一样。

**这类缺陷的危险之处**：它不报错、不告警，只是**少扫了一块**，然后把差异包装成"修复进展"。审计工具最怕的不是漏报，是伪造出正向信号。

**修复**：候选路径改为 `[root/"scripts", repo_root/"scripts"]`，去重后取存在的那个。重跑后原始命中 **1237 条，与第一轮完全一致** —— 这反过来证明了修复是对的。

### 2.2 缺陷 W2：抑制清单匹配失效 → 12 条规则只生效 5 条

**现象**：登记了 12 条假阳性抑制规则，实际只命中 5 条，7 条形同虚设。

**根因**：分析器输出 `file` 用的是 `str(p.relative_to(root))`，即相对扫描根的路径（如 `autoforge/af_store.py`）；而我在抑制清单里写的是仓库根视角的 `src/autoforge/af_store.py`。抑制匹配是**子串匹配**，`src/autoforge/af_store.py` 不是 `autoforge/af_store.py` 的子串 → 匹配失败。

**修复**：在 `core/suppress.py` 的 `_match` 里加路径归一化 `_norm`（剥掉 `/src/` 与 `src/` 前缀后，再做相等 / 子串 / 后缀三级匹配）。修复后 **12 条全部命中，抑制 30 条**，未命中为空。

### 2.3 迭代后的工作流状态

| 环节 | 状态 |
|---|---|
| 门禁自检 | 22 个分析器，clean 样本假阳性 **0** |
| 画像探测 | python 42205 行 |
| 分析器扫描 | 22/22 ok，失败 `[]` |
| 缺陷台账 | 7 条全部 `still_open`，`fixed` 空、`unknown` 空 |
| 跨轮差异 | `prev_total=1237`、`new=0`、`gone=30`、`persistent=1128` |

**`gone=30` 恰好等于被抑制的 30 条** —— 这与 W1 那次"125 条假消失"形成对照：这次的 gone 有明确、可核对的来源（抑制清单），不是扫描范围漂移。跨轮 diff 现在可信了。

---

## 三、第二轮审计结果

| 指标 | 数值 |
|---|---|
| 原始命中 | 1237（与第一轮**完全一致**，证明 W1 已修复） |
| 抑制后 | **1207**（抑制 30 条已确证假阳性） |
| 严重度 | high 68 / medium 782 / low 312 / info 45 |
| 新增（`new`） | **0** |
| 持续存在（`persistent`） | 1128 |
| 台账 | F1–F7 全部 `still_open` |

**读法提醒**：`new=0` 不代表"这轮没新东西"。代码未变更时本就不该有新缺陷；本轮的新增来自**人工确证深度**（F6、F7），它们一直躺在第一轮那 1237 条里，只是上一轮没挖到。

### 剩余 68 条 high 的分布

| 规则 | 条数 |
|---|---|
| RSC-02-recursion-no-budget | 23 |
| AFS-01-silent-failure | 19 |
| AF-AST-MIXED-RETURN | 17 |
| CONC-08-partial-lock-coverage | 4 |
| RSC-05-limit-inconsistency | 3 |
| TX-04-delete-then-write | 1 |
| IN-05-dangerous-call | 1 |

---

## 四、本轮新增确证缺陷

### F6【中】Trigger group 嵌套全程无深度上限

**位置**：`af_ir/models.py:114`（`Trigger` 类）、`af_scanner.py:688`（`_triggers_node`）、`af_scheduler.py:255`（`_satisfied`）

**与 F2 的区别**：F2 说的是"常量定义了但遍历路径不用"。Trigger 这条路径更彻底 —— **连常量都没有**。`expr.py` 有 `MAX_EXPR_DEPTH=32` / `MAX_EXPR_NODES=256`，`condition_norm.py` 有 `MAX_CNF_CLAUSES=1024`，而 trigger 侧一个上限常量都不存在。

**实测**：

```
Trigger.from_dict(depth=100) → 构建 ok
Trigger.from_dict(depth=900) → RecursionError
```

**可达性**（两条路径，都实打实）：
- **保存路径**：`af_scanner.py:688` `_triggers_node` 递归遍历 trigger group 的 `sources`，无深度预算 —— 而 `scan()` 是 `save_graph()` 落盘前的必经步骤
- **运行期路径**：`af_scheduler.py:255` `_satisfied` 对 group 递归（`or` 递归 `_satisfied`、`and` 走 `_group_sub_satisfied` 查状态快照），**每个事件都会调用**

**影响**：IR 由 agent / 客户端提交，深嵌套 trigger 在保存时抛 `RecursionError`；即便侥幸存下，运行期每次事件评估都会再炸一次 —— 这是比 F1 更宽的面（F1 只在表达式侧）。

**修复建议**：新增 `MAX_TRIGGER_DEPTH` 常量，在 `Trigger.from_dict` 与各遍历入口强制校验。护栏要内建在函数自身，不能靠"调用方记得先校验"这种约定 —— F1 就是约定失守的已发生实例。

---

### F7【中高】canary 回滚依赖链上双层静默失败：自称 fail-closed，实为 fail-silent

**位置**：`af_adapters/ha.py:164`（`all_states`）、`af_adapters/ha.py:296`（`_capture_pre_snapshot`）

**问题**：`_capture_pre_snapshot` 的 docstring 白纸黑字写着"**fail-closed**"，但实现是两层 `except Exception: return {}`：

1. `all_states()` 请求 HA 失败 → `return {}`（`ha.py:164`，无日志）
2. `_capture_pre_snapshot` 捕获异常 → `return {}`（`ha.py:296`，**无日志**）

**后果链**（这是本轮最值得报的一条）：

```
HA 抓取失败
  → pre_snapshot 为空 {}
  → canary.rollback() 取 snap = pre_snapshot.get(e) or {"state":..., "attributes":{}}
  → 属性为空 → restore_call() 返回 None
  → logger.warning("canary 回滚跳过 …") + continue
```

**回滚被静默跳过了。** P1-10 / F7 引入的属性感知恢复（light 还原 brightness、climate 还原 temperature、cover 还原 position…）是 AutoForge canary 机制的核心安全把手 —— 它在 HA 抓取失败时整体失效，而运维能看到的只有一条"回滚跳过"的 warning，**看不到根因是抓取失败**。

**为什么这条比一般的"静默 except"严重**：

- 它不是"少记一条日志"，而是**安全护栏在失效时不发信号**
- 失败方向与直觉相反：不是"回滚做错了"，而是"**回滚根本没做，但看起来做过了**"
- 契约与实现不符（docstring 承诺 fail-closed，实现 fail-silent），后续维护者会基于错误的前提做判断

**修复建议**：抓取失败必须 error 级留痕，并区分「抓取失败」与「实体不存在」两种空；`pre_snapshot` 缺失时应当阻断下发，而不是下发后再静默回滚失败。

---

## 五、补充假阳性登记

| 规则 | 位置 | 确证结论 |
|---|---|---|
| CONC-08-partial-lock-coverage | `af_premiere.py:216`（`PremiereStore`）、`:476`（`TrialStore`） | **假阳性**：`load()` 注释写明"启动期调用"，实际调用点仅 `:128 / :287 / :551 / :555`，其中 `:551/:555` 是模块级 `_default_premiere.load()` / `_default_trial.load()`。启动期单线程加载，无并发窗口。**与 `af_auth` 的 `_load` 不同** —— 后者在 `pending_events` 遍历过程中被调用，是真竞态（F3），不应合并处理 |

这一条值得单独记：同样是"锁外裸写共享状态"，**调用时机决定了它是不是缺陷**。静态分析看不到调用时机，只能靠人工确证区分。

---

## 六、第一轮 5 项缺陷的复核结论

台账 7 条（F1–F7）在 round-003 全部 `still_open`，`fixed` 为空：

| ID | 严重度 | 位置 | 优先级 |
|---|---|---|---|
| F1 | high | `af_scanner.py:348` 校验顺序倒置 | P0 |
| F2 | medium | `af_ir/expr.py:255` 遍历缺预算 | P1 |
| **F6** | medium | `af_ir/models.py:114` trigger 无深度上限 | **P1** |
| F3 | medium | `af_auth.py:539` `_load` 锁外裸写 | P2 |
| **F7** | medium | `af_adapters/ha.py:164` 回滚链静默失败 | **P2** |
| F4 | low | `af_nl_parse.py:629` `_expr_atom` 递归无预算 | P3 |
| F5 | low | `af_flock.py:132` `held_by_other` 宽捕 | P3 |

**注意 F7 的严重度是"中"但优先级给到 P2 且排在 F3 之后 —— 按后果严重性它其实应该更高。** 它排在后面的唯一原因是修复需要改动下发流程（涉及是否阻断下发的决策），工作量与风险都大于 F3 的加锁改造。如果只按"失效后果"排序，F7 应当与 F1 同列。

---

## 七、仍未覆盖的部分（与第一轮相同，未改善）

| 工具 | 状态 | 未覆盖面 |
|---|---|---|
| semgrep | 不可用 | SAST 交叉验证 |
| detect-secrets | 不可用 | 密钥扫描（GEN-01 为内置规则） |
| pip-audit | 不可用 | **依赖 CVE / 供应链漏洞** |
| pytest | 未安装 | 测试缺口运行时探针、变异测试 |
| skill-scan / graph | 不可用 | MCP/agent 配置面、调用图可达性 |

**依赖漏洞面依旧完全未扫**。AutoForge 使用私有 `homesdk` wheel，这一块建议在有网络与工具的环境单独补扫，不要把本报告的"无发现"读成"无风险"。

另外 AFS-01 剩 19 条、AF-AST-MIXED-RETURN 剩 17 条、其余 medium 782 条只做了抽样确证，其中仍有真缺陷未浮出的可能。

---

## 附：产物路径

| 文件 | 内容 |
|---|---|
| `projects/AutoForge/round-003/report.md` | 本轮机器生成报告 |
| `projects/AutoForge/round-003/findings.json` | 抑制后 1207 条 |
| `projects/AutoForge/round-003/suppressed.json` | 被抑制的 30 条（含理由） |
| `projects/AutoForge/round-003/cross-round-diff.json` | 跨轮差异（new/gone/persistent） |
| `projects/AutoForge/baseline/bugs.json` | 缺陷台账 F1–F7 |
| `projects/AutoForge/round-002/` | 迭代过程中的上一轮产物（保留对照） |
