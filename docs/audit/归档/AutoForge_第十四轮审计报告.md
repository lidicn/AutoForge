# AutoForge 审计报告 · 第十四轮

- **审计对象**：`lidicn/AutoForge`
- **审计工具链**：`lidicn/ADM-auditkit`
- **本轮轮次**：`round-014`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-014/`
- **报告日期**：2026-10-06

---

## 一句话结论

**首次接入外部工具（bandit / detect-secrets），并发现 F15：出站白名单护栏被 4 处直连 `urlopen` 旁路。护栏本身写得很认真（`_WhitelistRedirector` 会对 3xx 的 Location 重新校验），问题是有代码绕开了它。**

---

## 一、F15：出站护栏旁路（新建，medium / P2）

### 既有护栏（写得不错）

`af_adapters/http.py` 实现了完整的出站白名单：

- `host_of(url)` 取主机名，**并检测 netloc 中的 `@` 凭证注入**（`http://evil@whitelisted.com` 返回空串）
- `_WhitelistRedirector` 继承 `HTTPRedirectHandler`，**跟随 3xx 前对 Location 重过白名单**——这条尤其正确：不重校验的话，白名单守住了首跳、守不住重定向目标

### 4 处绕过它的直连调用

| 位置 | 函数 | 写法 |
|---|---|---|
| `af_catalog.py:433` | `_default_fetch_all` | `_u.urlopen(req, timeout=...)` |
| `af_live.py:235` | `__init__` | `self._opener = opener or urllib.request.urlopen` |
| `af_metrics.py:197` | `_post` | `urllib.request.urlopen(req, timeout=10)` |
| `af_registry.py:321` | `rest_areas_fallback` | `(opener or urllib.request.urlopen)(req, ...)` |

四处都不经过 `allowed_hosts`，且用默认 opener ⇒ **默认跟随 3xx 且不对 Location 重校验**。

### 为什么定 medium 而不是 high

`base_url` 只来自配置文件 / `--ha-url` CLI / `AUTOFORGE_HA_URL` 环境变量，**外部 HTTP API 与 MCP 均不可设置**。所以这不是可直接利用的 SSRF，而是**既有防护的旁路**——护栏建好了，但这四条路没走护栏。

### bandit 只报了 2 处

| 工具 | 命中 |
|---|---|
| bandit B310（urllib urlopen） | 2 处（`af_catalog:433`、`af_metrics:197`） |
| 自建 OUTB-01 | **4 处** |

漏掉的两处是**间接写法**：`opener or urllib.request.urlopen` 和 `self._opener = opener or urllib.request.urlopen`。bandit 的 B310 匹配直接调用形态，布尔表达式作被调者、以及把函数对象赋值给实例属性这两种都漏了——所以补了自建规则。

顺带：bandit 还报了 B324（`af_ir/loop.py:246` hashlib 用不安全的 hash 算法），已判假阳性（非密码学用途）。

---

## 二、工作流迭代

### W37：外部工具接入（bandit / detect-secrets）

新增 `external_tool_defects.py`（原 `external_tools.py` 改名以匹配 `*_defects.py` 自动注册 glob），统一输出 `external-tools-findings.json`，含 `findings` 与 `tool_availability`。

**工具可用性实测：**

| 工具 | 状态 |
|---|---|
| bandit | ✅ 可用 |
| detect-secrets | ✅ 可用 |
| pip-audit | ⚠️ 已安装（2.10.1）但**被网络策略阻断**（osv.dev 与 pypi.org 均 HTTP 403） |
| semgrep | ❌ 不可用（安装期 `OSError [Errno 27] File too large`，包可 import 但无 CLI，rc=127） |

### W38：新增 OUTB-01 规则

AST 检测直连 `urlopen`，覆盖三种写法（直接调用、`opener or urlopen`、`_u.urlopen`），并做**函数级护栏符号判断**——函数内出现 `allowed_hosts` / `host_of` / `_WhitelistRedirector` / `is_allowed` 视为已走护栏。

调试时修的假阳性值得记：第一版做**文件级**判断，真实仓库 4 处全被误排除（这些文件里别处提到过护栏符号）；dirty 样本则因为注释里写了"不走 allowed_hosts"而 0 命中——**注释也算符号**。改成函数级 + 只认 AST 的 Name/Attribute（排除注释与文档串）后，dirty 命中 3、clean 0、真实仓库 4。

### W38a：`(opener or urlopen)(...)` 让 11 个分析器崩溃

这是本轮最隐蔽的一个坑。新增的 dirty 样本用了 `(opener or urllib.request.urlopen)(req, ...)`，结果 **3 个分析器同时抛 `local variable 'parts' referenced before assignment`**。

根因：`_chain()` 处理调用链时只认 `ast.Name` 和 `ast.Attribute`，**不认 `ast.BoolOp`**，于是 `parts` 从未赋值。

而这个写法**不是我们编造的样本**——`af_registry.py:321` 源码里就有。**真实代码触发了分析器里一个潜伏的崩溃**。

13 个分析器各自重复实现了 `_chain`（没有共用），批量修了 11 个。修的过程中又踩一次：`input_defects.py` 里的函数名是 `_call_chain`（不是 `_chain`），批量替换时把 `_chain` 调用写进了它内部，导致 `name '_chain' is not defined`。

**教训**：样本不是越"干净"越好。用真实代码里出现过的写法做样本，才能暴露分析器自身的缺口——这次就是靠照抄 `af_registry` 的写法，才发现 11 个分析器都处理不了布尔表达式作被调者。

---

## 三、正式审计结果

| 指标 | 数值 |
|---|---|
| 分析器 | **26/26 ok**（门禁 28 项） |
| 原始命中 | **1083**（上轮 1076） |
| 去重后待办 | **1052**（抑制 4 条） |
| 严重度 | high **85** / medium 682 / low 285 |
| 跨轮 diff | **new 7 / gone 0** |
| 台账 | **F1–F15 全部 still_open** |
| 递归 PoC | cyclic_crash 9 / safe 2 / unavailable 9 / inconclusive 4 |
| 状态 PoC | 9/9 data_lost |

门禁：**ok=True、analyzers=28、failures=[]、drift=0**。

**new=7 逐条可核对**：OUTB-01 ×4 + EXT-BANDIT-B310 ×2 + EXT-BANDIT-B324 ×1，**全部来自本轮新增的两个分析器**。`gone=0`——没有一条"消失"，不存在漏扫伪装成修复的情况。

---

## 四、新证据：依赖面状况

导入冒烟显示 **12 个模块因缺第三方依赖无法导入**：`fastapi`、`typer`、以及私有 `homesdk`（仓库内 `docker/homesdk/homesdk-0.3.1-py3-none-any.whl`，未上 PyPI，也未安装）。

这解释了递归 PoC 里 9 条 `unavailable` 的一部分成因——不是探针构造不出输入，而是**模块压根导入不了**。

`homesdk` 是硬依赖但不在公开索引，**十四轮都未能扫描其漏洞面**。这是"风险未知"，不是"无风险"。

---

## 五、仍未覆盖（含本轮新受阻项）

| 工具 | 未覆盖面 | 本轮状态 |
|---|---|---|
| pip-audit | 依赖 CVE / 供应链漏洞 | ❌ **网络策略阻断**（osv.dev、pypi.org 均 HTTP 403）；`ensurepip` 不可用已用 virtualenv 绕过，但 API 仍不通 |
| semgrep | SAST 交叉验证 | ❌ 安装失败（`File too large`），无 CLI |
| detect-secrets | 密钥扫描 | ✅ 已接入，dirty 样本命中 3 |
| pytest | 测试运行时探针、变异测试 | ❌ 未接入 |

F1–F7、F9、F11、F12 未纳入补丁。F8/F10/F13/F14 补丁只存在于只读副本 `/data/workspace/repos/af-patched`，**原仓库未改动**。

`af_scanner.py:688 _triggers_node` 与 `af_scheduler.py:255 _satisfied` 涉及 F6（trigger group 无深度上限），因依赖缺失仍未自动验证。
