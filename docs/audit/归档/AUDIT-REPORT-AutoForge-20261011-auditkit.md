# AutoForge 稳定性审计报告 · auditkit 平台轮次（2026-10-11）

> 审计 agent：opencode（MIMO）· 审计平台：NAS auditkit 容器
> 审计对象：容器副本 `/vol1/1000/docker/autoforge` → 容器内 `/data/workspace/audit/src`
> 规模：python 333 文件 / 91,473 行（另有 shell 5 / ts 36 / js 70）
> 方法论：baseline → 盲区分析 → 写新规则 → selftest → 端到端轮次 → 人工 triage
>（静态候选不实测不升级为确证；命中经逐条人工核对源码）

---

## 一、结论摘要

| 类别 | 数量 | 说明 |
|---|---|---|
| ✅ 确证缺陷（静态+人工逐行核实） | **1** | D-1 更新标记固定名 tmp 并发交错 |
| ⚠ 高置信候选（增长路径已核实，泄漏取决于实例生命周期） | **6** | C-1~C-6 常驻无界容器 |
| ⚠ 低危候选（形态真实、影响小） | **3** | C-7 CLI 裸 JSON、C-8/C-9 环境变量裸解析 |
| ❌ 不采纳（语义/生命周期不成立，附理由） | **8** | 见第五节 |
| 🧹 历史缺陷复核 | 3 族 | 均已修复，新规则 0 命中验证收敛 |
| 🛠 平台侧产出 | 6 文件 | 4 规则（1 修复 + 3 新增）+ 2 共享判据修复，提案包已递交 |

**总体判断**：经前若干轮修复后，AutoForge 核心路径（原子落盘、事件总线、执行器、
鉴权）的高危缺陷已收敛；本轮剩余问题集中在**旁路端点/统计容器/诊断 CLI**，
无 P0 级崩溃-数据丢失路径。最值得修的是 D-1 与 C 类常驻容器。

---

## 二、迭代记录（6 轮）

| 轮次 | findings | 规则 | 事件 |
|---|---|---|---|
| round-001 | 547 | 18 active / **1 broken** | 基线；发现 `py.guard_call_swallowed` broken、5 条适用规则 0 命中 |
| — | — | — | 零命中自检：`af.mcp_tool_no_timeout`、`py.dry_run_no_validation`、`py.resource_rebuild_no_close`、`py.sql_limit_placeholder`、`py.dynamic_code_exec` 内置样本全过 ⇒ 0 命中=现网真无违规，非静默失效（PITFALLS N1 排除） |
| round-002 | 589 | 22 active | +4 新规则；`cross_func` 报 32 条（发现嵌套函数假阳性类） |
| round-003 | 559 | 22 | 修复 P1 下钻类 29 条假阳性；暴露 Lambda.name 崩溃 |
| round-004 | 560 | 22 | 修复 Lambda 崩溃；剩 3 条（发现兄弟嵌套函数互调假阳性类） |
| round-005 | 557 | 22 | 修复嵌套 def 名收集，`cross_func` 归零；发现 `af_bus.emitted` 有界 deque 假阳性 |
| round-006 | **553** | 22 active / **0 broken** | 修复共享判据 `is_bounded()` 注解漏判（-4 假阳性）；终版基线 |

数字持平（547→553）的真实含义：**新增覆盖的净收益被假阳性清理抵消**。
新规则净命中 = 1 条确证缺陷（D-1）+ 3 条回归护栏 0 命中（现网干净的证明）；
规则精确率显著提升（`unbounded` 35→29 生产侧、`cross_func` 32→0 全为误报类）。

---

## 三、确证缺陷

### D-1｜`af_api.py` 更新标记文件：固定名 tmp + 非原子 check-then-write（medium）

- **位置**：`src/autoforge/af_api.py:1139`（isfile 检查）、`:1152-1155`（写入）
- **形态**：`tmp_path = _UPDATE_MARKER + ".tmp"` → `open(tmp_path, "w")` →
  `os.replace(tmp_path, _UPDATE_MARKER)`。tmp 名由目标确定性派生（非 mkstemp 随机名）。
- **触发条件**：`POST /api/system/update` 是 **sync def** 端点（FastAPI 线程池执行），
  两个并发提交都通过 `:1139` 的 `isfile` 检查后写**同一个** `.tmp` 文件：
  1. 内容交错 → 最后一次 `os.replace` 装上坏 JSON → sidecar `json.load` 失败，
     **更新静默不执行**（表现为"点了更新没反应"，错误只在宿主脚本日志里）；
  2. check-then-write 之间无锁，`isfile` 判据非原子。
- **影响面**：admin 专用端点，并发概率低；后果为更新失败而非用户数据损坏 ⇒
  定级 **medium**。
- **修复建议**：改用 `af_atomic.py` 标准写法（`tempfile.mkstemp` 随机名 +
  fsync + replace）；或对端点加 `FileLock`（仓内 `af_catalog`/`af_pending`
  已有现成模式）。本仓其余 6 处 tmp 写均已 mkstemp 化并有
  `# fixed-tmp: exempt` 注释豁免约定——此处是**最后一处漏网**。
- **检出方式**：新规则 `py.fixed_tmp_replace_nonatomic`（round-006 唯一命中，
  已人工逐行核实）。

---

## 四、高置信候选（C 类：常驻无界容器）

判据：`py.unbounded_container` 命中 + 人工核实**增长路径真实存在**（append/add
无对应 pop/clear/del/裁剪）。是否构成泄漏取决于实例生命周期——下列均为
**常驻单例**（进程级 Engine/Registry/Arbiter），长时间运行单调增长。
每条数据体量小，属慢性内存泄漏，定级 medium/low。

| # | 位置 | 容器 | 增长路径（已核实） | 定级 |
|---|---|---|---|---|
| C-1 | `af_health.py:463`（写入点 `:813`） | `HealthEngine._history` | `setdefault(aid, [])` 后逐次 append 健康采样行；daemon 每 tick 采样 ⇒ 每自动化每天数千行，永不裁剪 | medium |
| C-2 | `af_scheduler.py:78`（写入点 `:391`） | `Scheduler._time_fired` | `self._time_fired.add(mark)`，每次定时器触发入集，无清理 | medium |
| C-3 | `af_pretrigger.py:65` | `TriggerHistory._events` | 触发事件逐条入列，无裁剪 | medium |
| C-4 | `af_proposal.py:200` | `ProposalManager.order` | 提案 id 逐个入序，无淘汰 | low |
| C-5 | `af_conflict.py:178` | `ConflictArbiter._release_log` | 释放记录逐条入日志，无裁剪 | low |
| C-6 | `af_runtime.py:59` | `Runtime._exec_stats` | 每自动化执行统计；增长受自动化总数约束（弱有界），列出供复核 | low |

**不构成泄漏（B 类，短生命周期，列出以免下轮重复报）**：
`af_nl_parse._Builder.nodes/edges`（按次构建）、`af_evo.GraphView/EvoScanner.warnings`
（按扫描构建）、`af_fault.FaultPlan.specs`（按演练构建）、
`af_scene._groups`/`af_scheduler._queues`/`af_intervention.managed`/
`af_health.extra_ids`/`af_auth._revoked`（键空间受场景/实体/令牌数约束，弱有界）。

**测试侧（不影响生产，顺带列出）**：`af_adapters/mock.calls`、
`af_vhass/fake.*`、`af_vhass/high_fidelity.*`、`af_vhass/sse_stream._emitted_ids`、
`af_vhass/device_sm.attributes`——长测试会话内存缓慢增长。

**修复建议**：C-1/C-2/C-3 改 `deque(maxlen=N)`（仓内 `af_bus.emitted` 已是范本，
且刚被验证有界判定可识别）；C-4/C-5 按容量或 TTL 裁剪。

### 低危候选

- **C-7** `af_cli.py:1062`：`_metrics_snapshot` 对 events 文件 `json.loads`
  无保护——文件损坏时 CLI 诊断命令抛 traceback。CLI 大声失败可接受，低危；
  若追求体验可包 try 给出人话报错。
- **C-8/C-9** `af_catalog.py:260`、`af_service.py:1152`：环境变量裸
  `float(os.getenv(...))`——配错值时启动即 ValueError。仓内**已有**守卫解析器
  `af_env.py`（带日志+回落），此处绕过属不一致；建议改用 `_safe_float` 族。
  反面看：fail-fast 崩在启动期也说得过去 ⇒ 低危。

---

## 五、不采纳的候选（附理由，审计资产）

| 位置 | 规则 | 不采纳理由 |
|---|---|---|
| `af_canary.py:103` | except_return_failopen | `except UnknownEntity: return True` 位于漂移检测：True=「视为有漂移」，是**保守方向**（fail-closed），非放行。规则对 True 的通用假设在此调用点不成立 |
| `af_flock.py:132` | except_return_failopen | `except (BlockingIOError, OSError): return True` 是锁探针的**答案本身**（True=他人持锁），异常分支即语义，非吞异常 |
| `af_vhass/bridge.py:62` 等 6 处 | unbounded_container | 测试/仿真适配器（mock/fake/high_fidelity/sse_stream/device_sm），非生产路径 |
| `af_nl_parse.py:1040-1041`、`af_evo.py:289/841`、`af_fault.py:232` | unbounded_container | 对象随调用创建即弃（B 类），GC 可回收 |
| `af_bus.py:179 emitted` | unbounded_container | **规则假阳性**：实际为 `deque(maxlen=1000)`（注解赋值形态漏判）——已修共享判据（见第六节） |

另：round-006 其余大头（`py.silent_except_no_trace` 319、`py.except_broad_swallow`
100、TS/JS 类 44）为信号型/低严重度规则，噪音较高，本轮未逐条 triage；
其中 silent_except 与历史确证的 F 系列同族，建议下轮按"宽捕后是否接全量覆盖写"
做定向筛查（规则描述里已给方法论）。

---

## 六、平台侧产出（规则工程）

### 新增/修复规则（提案包：NAS `/vol1/1000/docker/ADM-auditkit/proposals/20261011_225712_py_autoforge_r6_pack/`）

1. **`py.guard_call_swallowed` v1.1.0（broken→active）**：原 YAML 的
   `call`+`type: [Pass]` 要求同一节点同时是 Call 和 Pass（结构性 0 命中，
   注册期 3/3 dirty miss 判 broken）。改写为 Python 插件直接分析
   Try/ExceptHandler 结构，原 7 条样本 7/7 恢复。护栏 API 集合并补回
   原 YAML 漏列的 `check_and_recover`。
2. **`py.fixed_tmp_replace_nonatomic` v1.0.0（新增）**：确定性 tmp 派生 +
   写入 + os.replace 三件事齐备才报；尊重 `# fixed-tmp: exempt` 注释豁免；
   按函数作用域隔离防跨函数假关联。现网命中 D-1。
3. **`py.cross_func_undefined_name` v1.0.2（新增）**：跨函数裸用局部名
   NameError。经 round-002→005 两轮实弹校准消灭两类系统性假阳性
   （all_walk 下钻嵌套函数 29 条；嵌套 def 名未入外层局部 3 条），
   现网 0 命中（历史 af_live logger 缺陷已修复的旁证），转为回归护栏。
4. **`py.fastapi_security_sync_call` v1.0.0（新增）**：HTTPBearer 等安全依赖
   直接调用（协程泄漏→500）；`await` 形态与 `Depends` 形态不报。
   历史 af_api pair/SSE 500 缺陷的回归护栏，现网 0 命中。
5. **`legacy/analyzers/_common.py::is_bounded` 修复**：赋值型有界模式容忍
   类型注解（`self.X: deque[T] = deque(maxlen=)` 此前漏判 → 4 条假阳性）；
   `example_rule.py` 配套新增 clean 样本 `bounded-deque-annotated` 锁住。

### 验证证据

- 单规则自检：4 规则 24/24 样本全绿；`py.unbounded_container` 3/3 全绿
- 全平台 selftest：21 分析器 ok，无 0 命中、无 clean 命中
- `tests/run_all.py`：85/85 通过
- 端到端：round-006（553 findings，broken=0）
- 提案包：`CHANGE.yaml`（verify_cmd 已修正）+ `files/`（6 文件）+
  `README_提交说明.md` + `evidence/`

---

## 七、流程备注与遗留

1. **源码副本漂移**：容器审计副本与 `E:\NAS\AutoForge` 存在版本差
   （例：`af_ir/models.py` `assert_param_budget` 行号 158 vs 161）。本报告
   行号**一律以容器副本为准**；D-1 位置两副本一致。建议对齐两副本后
   复核一次行号。
2. **仓库双拷贝**：`E:\NAS\ADM-auditkit`（本地工作副本）与
   `/vol1/1000/docker/ADM-auditkit`（容器 `/app`，活仓库）内容曾不同步；
   本轮改动已同时落两处，提案包以活仓库为准。
3. **遗留盲区**（下轮建议）：
   - TS/JS 侧 44 条（unhandled-promise 33 + any-abuse 9）未 triage；
   - `generic_text` 分析器 0 命中，需确认适用性；
   - `py.silent_except_no_trace` 319 条按"是否接全量覆盖写"定向收敛；
   - `py_outbound_call_no_timeout.py` 在活仓库有未提交改动（root 属主，
     非本轮产生），下轮确认归属。
4. **修复跟踪**：D-1 与 C-1~C-5 建议进缺陷台账（`status AutoForge` 目前
   无 bugs.json），修复后用 `py.fixed_tmp_replace_nonatomic` 与
   `py.unbounded_container` 复扫确认归零。

---

## 附录：关键命令

```bash
# 基线/轮次
ssh nas "docker exec auditkit python3 /app/auditkit round AutoForge --round N"
# 自检
ssh nas "docker exec auditkit python3 /app/auditkit selftest"
ssh nas "docker exec auditkit python3 /app/auditkit rules test <rule_id>"
# 报告/明细
ssh nas "docker exec auditkit cat /app/projects/AutoForge/round-006/report.md"
# 提案包
ssh nas "ls /vol1/1000/docker/ADM-auditkit/proposals/20261011_225712_py_autoforge_r6_pack/"
```
