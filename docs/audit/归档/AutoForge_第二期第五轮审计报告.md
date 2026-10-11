# AutoForge 第二期第五轮审计报告

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-005
- **上一轮**：round-004（确证 AF7，新增 W139）

---

## 一句话结论

**GOD-01 ×5、TX-04 ×3 全部证伪并修判据，ERRH-02 ×2 证伪并修判据。ASM-01 从 11 条收敛到 1 条真缺陷——AF8：`pydantic` 顶层裸 import 却不在任何依赖块里，靠 fastapi 的传递依赖侥幸可用，属真实的"服务起不来"风险。**

---

## 一、确证缺陷

### AF8 · `pydantic` 未声明依赖，却顶层裸 import（**medium**）

**位置**：`af_api.py:63`

```python
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel        # ← 顶层裸 import，无 try/except
```

**证据**：

| 依赖块 | 内容 |
|---|---|
| `dependencies`（必装） | `jsonschema>=4.20`、`typer>=0.12` — **无 pydantic** |
| `api` extras | `fastapi>=0.110`、`uvicorn>=0.29` — **无 pydantic** |
| `dev` extras | pytest / httpx / websockets 等 — **无 pydantic** |

**但 pydantic 当前装了**（2.14.0），因为 **`fastapi` 自己依赖它**（实测声明 pydantic 的分发包括 `fastapi`）。

**为什么是缺陷**：

- `pydantic` 是 **fastapi 的传递依赖**，不是 AutoForge 的声明依赖
- `af_api.py` 是**顶层裸 import**，无 try/except 回退
- 一旦 fastapi 某个版本不再传递 pydantic（或用户用 `fastapi-slim` 这类精简版），`af_api` 直接 `ImportError` ⇒ **服务起不来**

**这与 doubao-butler D20 同族（依赖未声明），但更危险**：D20 报的是 `aiohttp`/`fastapi` 缺失时某功能不可用，AF8 是主 API 模块起不来。

**为什么之前没报**：ASM-01 报了 11 条，其中 5 条是 try/except 回退支的假阳性，把它们淹没了。**噪声挤掉了真信号**——与 doubao-butler 第十九轮 W89 的结论一致。

**未进补丁**：改 `pyproject.toml` 加依赖是行为变更，需确认加进 `api` extras 还是 `dependencies`。

---

## 二、证伪（8 条）

### GOD-01 护栏顺序倒置 ×5 → **0**

| 位置 | 排除依据 |
|---|---|
| `af_api.py:595`、`af_persist.py:214` | `read_text()` 与 `assert_deletable()` **分属不同函数**，跨函数比行号无意义 |
| `af_cli.py:87` | `read_text` 读的是 entities 文件，与 `load_device_guard` 护栏**对象不同** |
| `af_test.py:231` | `rmtree()` 是 `clear()` 的**目的本身**，`_ensure_dirs` 是重建步骤，不是护栏 |
| `af_orchestrator.py:2104` | 同类：遍历在前、clamp 在后，但遍历本身无害 |

**根因**：`DANGER_CALLS` 里混入了**只读遍历类**（`read_text`/`iter_strings`/`walk` 等），且判据**在同一函数体内按行号配对**，995 行的 `build_app` 里相隔 356 行的两个调用被配成一对。

### TX-04 先删后写无回滚 ×3 → **证伪**

- `_stash_archive` 是 **rename 让位**（BUG-11 的修复成果），不是删除
- `_unstash_archive` 就是**回滚操作本身**
- `_delete_archive` 确实不可逆，但**删除前已调 `assert_deletable`**

PoC 实测（`poc_af8.py`）：原仓库与补丁副本 `_stash → _unstash` 文件数均 1→1，**回滚有效**。

### ERRH-02 ×2 → **证伪**

`af_flock.py:132 held_by_other()` 与 `af_canary.py:103 has_drift()` 都在 except 里 `return True`：

| 函数 | `True` 的含义 | 方向 |
|---|---|---|
| `held_by_other()` | 锁被别人持有 ⇒ **不抢锁** | **fail-closed** |
| `has_drift()` | 有漂移 ⇒ **触发回滚** | **fail-closed** |

**规则只看"except 里 return True"，看不出 True 的语义方向。** 与 memory-agent 第十一轮 W107（`_pinned_entities` 返回 None 是中止信号）同族。

### ASM-01 ×11 → 1 条真 + 6 条降级

| 类别 | 数量 | 依据 |
|---|---|---|
| try/except **回退支**（`af_conflict`/`af_conflict_audit`/`af_conf`） | 3 → 0 | 主支 `from .af_conflict import` 是相对导入，三个模块**都真实存在**（ls 确认）；规则只看 except 支 |
| 可选依赖**探测**（`homeassistant`、`pytest_homeassistant_custom_component`） | 4 → low | 在 `try/except Exception` 里，注释明写"仍使用 FakeHA"，代码有回退 |
| **真缺口** `pydantic` | 1 | 见 AF8 |

---

## 三、工作流迭代

| 编号 | 内容 | 效果 |
|---|---|---|
| **W140** | `read_text`/`iter_strings`/`walk` 等只读操作移出 `DANGER_CALLS` | GOD-01 5 → 2 |
| **W140b** | `ensure_*` 判为**重建步骤**而非护栏 | → |
| **W140c** | `mkdir` 移出 `DANGER_CALLS` | → 1 |
| **W140d** | `_is_rebuild` 用 `"ensure_" in low` 而非 `startswith`（下划线前缀漏判） | → |
| **W140e** | 加 `GUARD_WINDOW=25`：护栏与危险动作须在 25 行内，否则不算配对 | → **0** |
| **W141** | 新增 `_true_is_conservative()`：`held_by_other`/`has_drift`/`is_blocked` 等名字 + 「保守/fail-closed/宁可」等注释 ⇒ `True` 是保守方向 | ERRH-02 无 high |
| **W142** | try/except 双分支加载时，若主支有对应相对导入且模块存在 ⇒ except 支是回退，不算缺失 | ASM-01 11 → 6 |
| **W142b** | try/except 保护下的 import = 可选依赖探测，降 low（不消除，仍需确认依赖声明） | → 1 high |

---

## 四、本轮最该记住的两条

### ① 噪声挤掉真信号——第二次确证

ASM-01 报 11 条时，AF8（`pydantic`）已经在里面了，但它和 5 条 try/except 回退支的假阳性混在一起，我前四轮都没看到它。

**收敛到 1 条之后它才显形。** 这与 doubao-butler 第十九轮 W89（RSC-03 的 95 条噪声）是同一结论：**排除不是为了数字好看，是为了让真缺陷能被看见。**

### ② 判据缺口的第四个变种：看不见"另一条可行路径"

- W79d：包名写死
- W79f：包名→导入名映射
- **W142：try 主支成功时，except 支不是缺失**

三者同源：**静态规则逐个节点独立判定，看不见上下文里存在另一条可行路径**。

---

## 五、数字变化

| | round-004 | round-005 |
|---|---|---|
| 总命中 | 1013 | **1008** |
| high | 43 | **38** |
| 分析器 | 28 ok | 28 ok |

**降下来的 5 条全是判据修正**（GOD-01 5），同时**新增 1 条真缺陷**（AF8 从 ASM-01 里浮出来，本身已在计数中）。

---

## 六、剩余 high 38 条核验进度

| 族 | 数量 | 状态 |
|---|---|---|
| **DO-01/02/03/05** | 10 | **已修但规则看不见**（运行时 mark/barrier 静态不可见，与 memory-agent M6 同情况） |
| TX-04 | 3 | 已证伪 |
| FO-01 | 3 | AF5 已确证，另 2 条待查 |
| AFS-01 | 3 | 待查 |
| RSC-05 | 3 | AF7 已核（不可达） |
| ASM-01 | 1 | **AF8 真缺陷** |
| AUTH-01 | 1 | 待查 |
| DO-04 | 1 | 待查 |
| RMW-01 | 1 | 待查（`af_experience.py:30`，`clear()` 不重读直接用陈旧快照覆盖） |

**GOD-01 ×5、ERRH-02 ×2 已归零。**

---

## 七、如实说明

- **AF8 未进补丁**——改 `pyproject.toml` 是行为变更，需你定夺：加进 `dependencies`（所有安装都要）还是 `api` extras（只有装 API 才要）。
- **AF8 未做"卸载 pydantic 实测起不来"**——沙箱里 jupyterlab 也依赖它，卸了会破坏环境。证据是：依赖声明里没有 + 顶层裸 import + 仅靠 fastapi 传递。
- **DO 族 10 条"已修但规则看不见"是推断**——依据是这些文件都在第一轮护栏清单内（有 `mark_poisoned`/`barrier`），但**没有逐条跑 PoC 确证**。这与 memory-agent 第十三轮 M6 的处理方式相同，但强度低于实测。
- **RMW-01 未核验**。
- 依赖 CVE 面仍未扫。
- 补丁只在只读副本，**原仓库未改动**。
- 本轮未跑门禁。
