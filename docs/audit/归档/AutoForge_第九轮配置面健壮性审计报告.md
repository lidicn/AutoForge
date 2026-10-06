# AutoForge 第九轮审计报告：配置面（环境变量）健壮性

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 审计范围：`src/autoforge/` 全部环境变量读取点（24 个键 / 36 处读取）
> 本轮主题：**配置面——环境变量从"运维输入"变成"系统行为"的那一跳是否健壮**
> 判定标准：严格档 —— 配置行为须**注入环境变量实测**，不接受只读代码推断
> 报告日期：2026-10-06

---

## 一、执行摘要

前八轮审了运行时代码（一~六）、资源生命周期（七）、门禁与测试套件（八）。第九轮换到**配置面**——这是运维面的不可信输入，和 HTTP 请求同属边界，但几乎所有项目只对 HTTP 做校验，对 env 是裸 `int(os.getenv(...))`。

AutoForge 有真机下发能力（L2 不可逆动作），配置解析错一条就可能让服务起不来，或让安全闸门静默消失。

**结论：找到 2 个缺陷，其中 1 个是 fail-open（安全闸可被负值静默关闭）。**

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R9-02** | 🔴 High | **`AUTOFORGE_BLAST_RADIUS` 配负数 → 爆炸半径护栏静默失效**（与 0 等价，但文档只说"0=不限"） | `af_service.py:1082-1090` |
| **R9-01** | 🟠 Medium | **6 处裸数值转换，5 处实测配错即模块导入崩溃**；正确写法 `_env_number` 已在 af_bus 存在但未复用 | `af_service.py:1354/1357` 等 |

### 最关键的一条线索

`af_bus.py:30` 的注释写着：

> P1-7 修复：原实现直接 `int(os.getenv(...))`，运维写错（如 **"12s"、空串、超范围**）会让 EventBus 构造即抛 ValueError，**服务起不来**。

**团队已经修过这个形状**，正确写法是 `_env_number(name, default, lo=, hi=)`——带解析兜底 + 上下界钳制 + warning 日志。

但 `grep _env_number` 只命中 **af_bus.py 自己 3 处**。**其余 6 处仍是裸转换。**

这不是"没想到"，是"想到了、修了一处、没铺开"。

---

## 二、工作流迭代：这一轮改了什么

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码（安全/稳定性/递归/并发/契约/可恢复性） | 22 |
| 七 | 资源生命周期 + 单门禁有效性 | 2 |
| 八 | 全门禁 + 测试套件有效性 | 3 |
| **九** | **配置面（环境变量）健壮性** | **2** |

### 新增资产

- **`scripts/scan_round9.py`**（H01–H04）：环境变量→数值转换保护检测 · 安全开关方向 · 数值边界

### 四个新范式（已记入 lessons 第 28–31 条）

**28. 配置面是"运维面的不可信输入"，要和 HTTP 输入同样审**

三条判据：① 解析失败是否可控；② 越界值方向（fail-open 还是 fail-closed）；③ "0=不限"这类特殊语义是否把负数也算进去。

**29. 找"已修一处未铺开"最快的方法：搜 `P\d+-\d+ 修复` 注释**

本轮关键线索全靠这一招。看到形如"P1-7 修复"的注释，**立刻 grep 那个修法的函数名看复用范围**——未铺开就是现成的确认缺陷，比自己推想可靠得多。（第五轮 lessons 19、第八轮 R8-01 同族）

**30. "0 = 不限"类开关必须校验负数与极大值**

`if limit > 0 and count > limit` 这种写法，负数会走进"不限"分支。

**31. 配置类缺陷用"环境注入实测"，不要只读代码**

```
env AUTOFORGE_SESSION_MAX=abc python3 -c "import autoforge.af_service"   # → CRASH?
env AUTOFORGE_BLAST_RADIUS=-1 python3 -c "..."                          # → 护栏还在吗？
```

模块顶层常量的解析行为，只有真注入跑一次才知道。

---

## 三、确认缺陷

### 🔴 R9-02　`AUTOFORGE_BLAST_RADIUS` 配负数 → 爆炸半径护栏静默失效

**位置**：`src/autoforge/af_service.py:1082-1090`

```python
def _check_blast(count: int, what: str) -> None:
    """超过爆炸半径即拒绝——**提示「请拆分」而不是硬拒到无法工作**。**"""
    limit = blast_radius_limit()
    if limit > 0 and count > limit:          # ← 负数与 0 等价
        raise ServiceError(
            f"{what} 会影响 {count} 条自动化，超过爆炸半径上限 {limit}；"
            f"请拆分为更小的操作（或调高 AUTOFORGE_BLAST_RADIUS；置 0 表示不限）",
            status=400,
        )
```

**实测**（注入环境变量后调 `_check_blast(9999, ...)`）：

| `AUTOFORGE_BLAST_RADIUS` | `blast_radius_limit()` | `_check_blast(9999)` |
|---|---|---|
| `"8"` | 8 | 拒绝 ✓ |
| `"0"` | 0 | **放行**（文档一致：0=不限） |
| **`"-1"`** | **-1** | **放行（护栏静默失效）** ❌ |
| **`"-5"`** | **-5** | **放行（护栏静默失效）** ❌ |
| `"1000000"` | 1000000 | 放行（形同虚设） |
| `"abc"` | 8 | 拒绝 ✓（try/except 生效） |
| `""` | 8 | 拒绝 ✓ |

**问题**：文档写"置 **0** 表示不限"，但**任何负数都等价于"不限"**，且**无任何 warning**。

运维复制粘贴时多打个负号（或想当然写 `-1` 表示"关闭限制"），**爆炸半径护栏就没了**——批量操作可以一次影响任意多条自动化，而审批链路上的这道护栏本该拦住它。

**为什么这是 fail-open（比崩溃危险）**：

- R9-01 那种"解析失败即崩溃"虽然难看，但**服务起不来，运维立刻发现**
- R9-02 是**服务正常启动、护栏悄悄消失**，只有真出事才会暴露

**对比正确范式**：`af_bus._env_number(..., lo=1, hi=1000)` 会拒绝越界值、回落默认、**并发 warning**。

**修复建议**（两行）：

```python
def blast_radius_limit() -> int:
    """当前爆炸半径上限（可用 `AUTOFORGE_BLAST_RADIUS` 覆盖；`0` = 不限）。"""
    try:
        val = int(os.getenv("AUTOFORGE_BLAST_RADIUS", str(DEFAULT_BLAST_RADIUS)))
    except (TypeError, ValueError):
        return DEFAULT_BLAST_RADIUS
    if val < 0:                                    # ← 新增
        logger.warning(                            # ← 新增
            "AUTOFORGE_BLAST_RADIUS=%s 为负数，负数与 0 都表示『不限』；"
            "若本意是收紧请给正数，回落默认 %s", val, DEFAULT_BLAST_RADIUS)
        return DEFAULT_BLAST_RADIUS
    return val
```

---

### 🟠 R9-01　6 处裸数值转换，5 处实测配错即模块导入崩溃

**实测**（`env VAR=abc python3 -c "import autoforge.<mod>"`）：

| 环境变量 | 位置 | 实测结果 |
|---|---|---|
| `AUTOFORGE_SESSION_MAX` | `af_service.py:1357` | **CRASH** `ValueError: invalid literal for int()` |
| `AUTOFORGE_SESSION_TTL_S` | `af_service.py:1354` | **CRASH** `ValueError: could not convert string to float` |
| `AUTOFORGE_UNDO_MAX_DEPLOYS` | `af_undo.py:57` | **CRASH** `ValueError: invalid literal for int()` |
| `AUTOFORGE_UNDO_WINDOW_S` | `af_undo.py:52` | **CRASH** `ValueError: could not convert string to float` |
| `AUTOFORGE_CONFIG_TTL_S` | `af_config.py:158` | **CRASH** `ValueError: could not convert string to float` |
| `AUTOFORGE_RATE_LIMIT_PER_MIN` | `af_api.py:258` | 静态推断同族（沙箱缺 fastapi 无法导入实测） |
| `AUTOFORGE_BLAST_RADIUS` | `af_service.py:1069` | ✅ OK（有 try/except） |

**5 处都在模块顶层**，配错 → 模块导入即崩 → **整个服务起不来**。

**严重度说明（避免过度渲染）**：方向是 **fail-closed**——服务起不来，运维立刻发现，不会静默跑在错误配置上。所以标 Medium 而非 High。

**但按团队自己的标准，这是缺陷**：`af_bus.py:30` 的 P1-7 注释明确把"运维写错 → 服务起不来"列为需要修的问题，并写了 `_env_number` 来兜底。

**而且其中两处很关键**：`SESSION_MAX`（`af_service.py:1357`）与 `MAX_DEPLOYS`（`af_undo.py:57`）正是**有界缓存注册表 `BOUNDED_CACHES` 登记的两条腿**（`cap`）。这两条腿的取值来自裸解析——解析失败是崩溃（fail-closed，可接受），但**没有任何上下界保护**：运维配个 `999999999` 一样能通过，有界性形同虚设。

**好消息**：`UndoStore` 的**运行时**是健壮的——`__init__` 里做了钳制：

```python
self.window_s   = min(MAX_WINDOW_S, float(window_s if ... ))   # 钳到 300 上限 ✓
self.max_deploys = max(1, int(max_deploys))                    # 钳到最小 1 ✓
```

问题只在模块顶层常量本身。

**修复建议**：把 `af_bus._env_number` 提到公共位置（如 `af_config.py` 或新建 `af_env.py`），7 处统一改用：

```python
SESSION_MAX     = int(_env_number("AUTOFORGE_SESSION_MAX", 128, lo=1, hi=10000))
SESSION_TTL_S   = _env_number("AUTOFORGE_SESSION_TTL_S", 3600, lo=1)
MAX_DEPLOYS     = int(_env_number("AUTOFORGE_UNDO_MAX_DEPLOYS", 200, lo=1, hi=100000))
```

---

## 四、观察项（Minor，非缺陷）

### 布尔开关有三套解析口径

| 口径 | 接受的真值 | 使用处 |
|---|---|---|
| `.lower() in ("1","true","yes")` | `1` / `true` / `yes` | `af_api.py:268`(TRUST_PROXY)、`306`(AF_ALLOW_NOAUTH)、`308`(AF_REQUIRE_AUTH)、`af_runtime.py:100`、`af_service.py:1579` |
| `.strip() == "1"` | **仅 `1`** | `af_mcp.py:82`(MCP_ALLOW_NO_TOKEN) |
| `_env_flag` 的 `_TRUTHY` | `1`/`true`/`on`/`yes`/`enforce` | `af_runtime_plugins.py:30` |

**实测**：`AUTOFORGE_MCP_ALLOW_NO_TOKEN=true` → `allow_no_token()` 返回 **False**。

运维按直觉配 `true`（这个变量名几乎必然会被配成 `true`），会发现"开关没生效"。

**方向都是 fail-closed**（配错=拒绝，安全），所以**不是安全缺陷**，是运维可用性问题。建议统一到 `_env_flag` 的 `_TRUTHY` 集合。

---

## 五、本轮验证通过（确认无问题）

| 项目 | 验证方式 | 结论 |
|---|---|---|
| **鉴权开关方向** | 逐个读实现 | ✅ `AF_ALLOW_NOAUTH` / `AF_REQUIRE_AUTH` / `AUTOFORGE_MCP_ALLOW_NO_TOKEN` / `AUTOFORGE_TRUST_PROXY` / `AUTOFORGE_LIVE_ENABLED` **全部默认 fail-closed**（未配=拒绝） |
| **鉴权异常兜底** | 读 `af_api.py:324-326` | ✅ `except Exception` → 403 "鉴权校验异常（fail-closed 拒绝）"，不泄露 500 |
| **CORS** | 读 `af_api.py:390-394` | ✅ 不默认 `*`；未配置时只允许 localhost，生产须显式配 `AUTOFORGE_CORS_ORIGINS` |
| **爆破半径非法值兜底** | 实测 `"abc"` / `""` | ✅ 回落默认 8（try/except 生效，这部分是对的） |
| **`UndoStore` 运行时钳制** | 读 `af_undo.py:271-272` | ✅ `min(MAX_WINDOW_S, ...)` + `max(1, ...)` |
| **凭据脱敏** | 读 `af_config.py:28-32` + `af_cli.py:756-763` | ✅ `_mask()` 只回 `****len=N`；CLI `credentials show` 只出掩码，docstring 明写"绝不露明文 / 末 4 位" |
| **会话清理逻辑** | 读 `af_service.py:1447-1454` | ✅ `overflow > 0` 判断正确；`SESSION_MAX=0` → 全淘汰（fail-closed，不会无界） |
| **`_env_number` 本身** | 读 `af_bus.py:25-48` | ✅ 空串/解析失败/越界三档都有兜底 + warning，是正确范式 |

---

## 六、已排除（rejected）

| 候选 | 理由 |
|---|---|
| **`AUTOFORGE_CONFIG_TTL_S` 负数** | TTL 为负 → 立即过期 → 每次重读（**更保守**方向，非缺陷） |
| **`SESSION_MAX=0`** | `overflow = len(_SESSIONS) - 0` → 全淘汰，fail-closed，语义虽怪但不会无界 |
| **凭据进日志** | 全仓无 `logger.*token` 直出明文；`_mask` 覆盖完整 |
| **布尔口径不一致** | 方向全部 fail-closed，降级为观察项（第四节） |
| **扫描器 H01/H02 零命中** | 扫描器 `is_env_call` 对 `os.getenv` 判定有 bug（误判为需含"environ"）；**已改用 grep 精确定位 7 处**，不依赖扫描器结论 |

---

## 七、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R9-02 爆破半径负数校验 | 约 4 行 | fail-open，护栏静默消失，风险最高 |
| **P2** | R9-01 统一用 `_env_number` | 约 7 处改造 | 把 af_bus 的正确范式提出来复用；顺带给两条"有界缓存腿"加上界 |

**回归验证清单**：
1. `AUTOFORGE_BLAST_RADIUS=-1` → 应回落默认 8 并出 warning，`_check_blast(9999)` 应拒绝
2. `AUTOFORGE_BLAST_RADIUS=0` → 仍表示不限（文档语义不变）
3. `AUTOFORGE_SESSION_MAX=abc` → 服务应正常启动（回落默认 128）而非崩溃
4. `AUTOFORGE_SESSION_MAX=999999999` → 应被上界钳制

---

## 八、九轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码 | 22 |
| 七 | 资源生命周期 + 单门禁 | 2 |
| 八 | 全门禁 + 测试套件 | 3 |
| 九 | 配置面 | 2 |

**"已修一处、未铺开"这个模式，九轮里出现了五次：**

| 轮次 | 修对的那一处 | 没铺开的地方 |
|---|---|---|
| 三 | 表达式有 `MAX_EXPR_DEPTH` | 图遍历无深度上限 |
| 四 | `af_store`/`af_catalog` 用 FileLock | `af_pending` 只用进程内锁 |
| 六 | 漂移检测 fail-closed 分 `unmodeled` 档 | 回滚结果无条件说"已自动回滚" |
| 八 | 2 个门禁遵守 rc=2 约定 | 2 个崩溃报 rc=1 |
| **九** | **`af_bus._env_number`（P1-7）** | **其余 6 处裸转换** |

**这不是能力问题。** 每次团队都找到了正确解法，甚至写清楚了注释和理由——`_env_number` 的 docstring、"P1-7 修复"的来龙去脉、`_check_blast` 的"提示请拆分而不是硬拒"，都显示出清晰的工程判断。

**缺的是把解法变成"唯一入口"。** 建议两条：

1. **把 `_env_number` / `_env_flag` 提到公共模块**，其他处一律不许裸 `int(os.getenv(...))`——这个可以用你们现成的机制管起来（`check_undefined_names.py` 那种标准库 AST 门，8 行就能判"出现 `int(os.getenv` 字面量且不在白名单文件"）
2. **"0=不限"类开关统一走一个 `_env_limit(name, default, *, zero_means)`** helper，把负数校验写死在里面

你们已经证明了自己会写正确的东西——现在该让"正确的东西"成为**唯一能写出来的东西**。

---

## 九、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| 误报修正手册（27 条 + 本轮 4 条 = **31 条**） | `audit-env/scripts/lessons-round2.md` |
| **配置面扫描器（H01–H04）** | **`audit-env/scripts/scan_round9.py`** |
| 门禁变异测试台（10 组探针） | `audit-env/scripts/mutation_gates.py` |
| 侵入式门禁变异 | `audit-env/scripts/mutation_invasive.py` |
| 深度缺陷扫描器（E01–E13） | `audit-env/scripts/scan_round3.py` |
| 并发缺陷扫描器（F01–F05） | `audit-env/scripts/scan_round4.py` |
| 契约扫描器（G01–G03） | `audit-env/scripts/scan_round5.py` |
| homesdk 本地包 / 持久 pytest | `audit-env/homesdk-pkg/` · `audit-env/pylib/` |
| 环境自检（25 项全通过） | `audit-env/scripts/99-verify.sh` |

### 本轮新增 lessons（28–31）

- **28** 配置面是"运维面的不可信输入"，要和 HTTP 输入同样审
- **29** 找"已修一处未铺开"最快的方法：搜 `P\d+-\d+ 修复` 注释，grep 修法函数名的复用范围
- **30** "0 = 不限"类开关必须校验负数与极大值（`if limit > 0` 会把负数算进"不限"）
- **31** 配置类缺陷用"环境注入实测"，模块顶层常量的行为只有真跑才知道

---

## 附：审计环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12（项目声明 `>=3.11`） |
| 影响 | `af_api.py` 无法导入（缺 fastapi）→ `AUTOFORGE_RATE_LIMIT_PER_MIN` 标注为静态推断 |
| 绕过 | homesdk wheel 解压 + `PYTHONPATH` |
| 本轮实测次数 | 7 个环境变量 × 注入实测（崩溃 5 / 回落 2）+ 爆破半径 7 档取值实测 |
