# AutoForge 第二期 · 第一轮审计报告

- **审计目标**：`lidicn/AutoForge`
- **审计重点**：影响稳定性、安全性的 bug
- **轮次**：第二期 · 第 1 轮（承接第一期已修复的 M1–M16）
- **日期**：2026-10-10

---

## 一、命中总数

| 项 | 值 |
|---|---|
| 全量机器命中 | **1022** |
| high | **52** |
| medium | 346 |
| low | 624 |
| 分析器 | 28 个，0 失败 |

**安全类命中**（本轮逐条核验的部分）：

| 规则 | 数量 | 严重度 | 结论 |
|---|---|---|---|
| AUTH-04-credential-growth | 4 | high | **全部假阳性**（已修规则 W137b） |
| OUTB-01-outbound-guard-bypass | 3 | medium | **全部假阳性**（已核实） |
| AUTH-01-route-no-auth | 1 | high | 待查（`api_auth_login` 本就是登录入口） |
| AUTH-05-lock-with-io | 12 | medium | 待核验 |
| IN-01 / IN-02 / IN-03 / IN-07 | 155 | mixed | 待核验 |
| ASM-01 | 11 | high | 待查 |

---

## 二、确证缺陷列表

### AF1 · IR 深嵌套导致 JSON Schema 校验爆栈 ⇒ HTTP 500（**medium**）

**简述**：向 `POST /api/build` 提交 `expr` 深嵌套的 IR 时，服务返回 **HTTP 500** 而非预期的 400。

**根因**：`af_ir/expr.py` 有 `_Budget`（`MAX_EXPR_DEPTH = 32`），但它在**语义编译阶段**才生效；而深嵌套 IR 会先在 **jsonschema 的 `$ref` 递归展开**里把 Python 调用栈打爆。

崩溃栈末段实测：

```
File ".../jsonschema/validators.py", line 431, in descend
File ".../jsonschema/_keywords.py", line 275, in ref
File ".../referencing/_core.py", line 271, in pointer
    if isinstance(contents, Sequence):
RecursionError: maximum recursion depth exceeded in comparison
```

**这是"护栏装在错误的阶段"——与 AutoForge F10、doubao-butler D6 同族，本项目第九次。**
预算值（32）是对的，但装在了一个调用方不走到的阶段。

**可达性**：IR 由 Agent 生成，经 `POST /api/build` 提交 ⇒ 输入可达。

**严重度定 medium 而非 high 的依据**：进程**不崩溃**，`/api/health` 仍返回 200（Starlette 兜底）。这是可用性缺陷（该请求失败 + 错误信息是 500 而非 400），不是进程崩溃或被绕过。

**实测阶梯（原仓库）**：

| expr 深度 | HTTP | ok |
|---|---|---|
| 10 | 200 | false（实体不存在，正常拒） |
| 40 | 200 | false（EXPR_INVALID，expr 预算生效） |
| 100 | 200 | false |
| **150** | **500** | — |
| **300** | **500** | — |

---

## 三、已打补丁的内容

**文件**：`af-patched/src/autoforge/af_ir/models.py`

**改法**：在 `validate_automation()` 的 **schema 校验之前**加迭代式深度预检。

- 新增 `_MAX_SCHEMA_DEPTH = 64`
- 新增 `_max_depth(obj)`——**迭代式**（用显式栈），自身不递归，避免本函数再爆栈；带 4096 早停兜底
- 超限抛 `IRValidationError`（可被 `api_build` 正确转成 400），而非让它崩成 500

**对照实测**（原仓库 vs 补丁副本，同一阶梯）：

| expr 深度 | 原仓库 | 补丁副本 |
|---|---|---|
| 10 | 200 ok=false | 200 ok=false |
| 40 | 200 ok=false | 400 |
| 100 | 200 ok=false | 400 |
| **150** | **500** | **400** ✅ |
| **300** | **500** | **400** ✅ |
| 800 | 探针侧崩溃 | 探针侧崩溃 |
| /api/health | 200 | 200 |

**150/300 从 500 变为 400 —— 补丁有效。**

---

## 四、工作流迭代（本轮）

| 编号 | 内容 |
|---|---|
| **W137** | `AUTH-04` 判据只认类内正则（`del self.X` / `.pop` / `maxlen`），看不见**裁剪委托给 helper** 的情况。补 `_has_purge_helper()`，追类内 helper |
| **W137b** | W137 修完 **4 条假阳性一条没消**——因为 `PairCodeStore._purge_expired()` 的函数体是 `return _purge_expired_codes(self._codes)`，真正的 `codes.pop()` 在**模块级函数**里。补第二层追查 + 模块级函数源码表。修完 AUTH-04 **4 → 0** |

> **这是"跨函数追查"第五次**（W54 写盘委托、W61 同名遮蔽、W62 timeout 在被调函数、W69 async 节点、W72 caller 方向）。
> 而且这次是**我自己的 helper 又漏了一层**——第一次改完没验证就以为好了，跑出来发现 4 条还在。

---

## 五、已排除的假阳性（7 条）

| 命中 | 排除依据 |
|---|---|
| OUTB-01 ×3（`af_catalog.py:433`、`af_metrics.py:197`、`af_registry.py:321`） | URL 来自 `self.ha_url` / `base_url`（配置或 `AUTOFORGE_HA_URL` 环境变量），**外部 HTTP API 与 MCP 都不能设置**（已核实 `af_api.py` / `af_mcp.py` 无引用）。不是可利用的 SSRF |
| AUTH-04 ×4（`PairCodeStore` / `AuthCodeStore` 的 `_load` 与 `create`） | `create()` 里调 `self._purge_expired()`，它委托模块级 `_purge_expired_codes()` 做 `codes.pop(...)`。**已有裁剪，且是 BUG-19 的修复成果** |

---

## 六、已核实**防护到位**的部分（不虚报）

- **`af_adapters/http.py` 出站白名单**：`is_allowed()` + `host_of()` 对 `netloc` 含 `@` 返回空串（防 `http://evil@whitelisted.com` 注入）；`_WhitelistRedirector` 对 3xx 的 `Location` **重新过白名单**
- **`af_api.py` SPA fallback**：`candidate.is_file()` + `str(candidate).startswith(str(dist))` 前缀检查，防目录穿越
- **IR 安全闸**：`POST /api/build` 对 L2 动作（门锁/窗帘/空调）正确拒绝（`L2_NEEDS_CONFIRM`）；未知 op、重复 node id、悬空边均被 schema 拦住

---

## 七、还没验证的部分（如实交代）

1. **52 条 high 里只核验了安全类**（AUTH/IN/OUTB/ASM），**其余族未核验**：`CONC-08`(5)、`GOD-01`(5)、`RSC-05`(3)、`TX-04`(3)、`DO-01/02/03/04/05`(9)、`FO-01`(3)、`AFS-01`(3)、`ERRH-02`(2)、`RMW-01`(1)
2. **AF1 的 depth=800 行为未知**——`json.dumps` 在**测试客户端侧**就 RecursionError，服务端真实行为未能观测。**这是探针局限，不是"服务端安全"**
3. **AF1 阈值 64 会抢在 `expr` 预算（32）之前触发**（expr 深度约 39+ 时），使错误信息从详细变粗略。功能正确（能防 500），但阈值需与 `MAX_EXPR_DEPTH` 再协调
4. **依赖 CVE 面未扫**（本期未尝试）
5. 第一期 M1–M16 的修复状态**本轮未复核**
6. 补丁只在 `af-patched` 副本，**原仓库未改动**
