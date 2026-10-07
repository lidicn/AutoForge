# AutoForge 审计报告 · 第十九轮

- **审计对象**：`lidicn/AutoForge`
- **审计工具链**：`lidicn/ADM-auditkit`
- **本轮轮次**：`round-019`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-019/`
- **报告日期**：2026-10-07

---

## 一句话结论

**递归 PoC 从 24 目标扩到 26 目标，`inconclusive` 从 4 条降到 1 条——其中最关键的一条：`af_scheduler._satisfied` 从"测不到"变成确证崩溃，F6 的运行期路径在提出十七轮后第一次被实测坐实。**

---

## 一、F6 运行期路径首次确证

F6 从第二轮提出，标题里写着"scanner/scheduler 递归遍历均无预算"。但直到本轮，**只有保存时的 scanner 路径**被实测确证（第十七轮），scheduler 那条一直是 `inconclusive`（`ModuleNotFoundError: homesdk`）。

本轮实测 `af_scheduler.py:255 _satisfied`：

```python
if trig.op == "and":
    return all(self._group_sub_satisfied(sub, event) for sub in trig.sources)
results = [self._satisfied(sub, event) for sub in trig.sources]   # ← or 分支直接递归
return any(results)
```

| 形态 | 结果 |
|---|---|
| `op="and"` | ok —— 走 `_group_sub_satisfied`，group 子项直接 return False，**不递归** |
| **`op="or"`** | **RecursionError** |

**只试一种形态就会漏判。** 这正是 W45f 的由来。

至此 F6 的两条路径均已实测确证：保存时校验（`af_scanner._triggers_node`）与运行期遍历（`af_scheduler._satisfied`）。后者暴露面更宽——**每个事件都遍历一次 trigger group**。

---

## 二、W45：递归 PoC 的六处改造

| 项 | 修复前 | 修复后 |
|---|---|---|
| **a** | 把 kw-only 形参当位置参数传 | 剔除 kw-only（`af_version.diff(v1,v2,*,store)` ⇒ `TypeError`） |
| **b** | 非目标参数一律替身 | **优先用真默认值**（`expr_nodes(expr, path=())` 的 `path+("args",i)` 被替身搞崩） |
| **c** | 只造 `{"op":"and","args":[...]}` | 增加 `{"and":[...]}` 键式（`af_draft._resolve_expr` 认的是这个） |
| **d** | 模块导入不了 ⇒ inconclusive | 注入桩模块（含 `__path__` + meta_path finder，覆盖 `homesdk.consent` 这类二级导入） |
| **e** | 实例方法的 self 喂惰性替身 | `_self_proxy`：找回类、构造实例、绑方法，让递归真能跑到被测函数 |
| **f** | 只试一种 op | 多形态试探 `VARIANTS=("and","or")`，**任一崩即判崩**，并记录崩在哪种形态 |

### 调试中踩的三个坑（都值得记）

1. **`_install_stubs()` 调用位置**：必须在 `sys.path.insert` 与 `mod_name` 赋值之后。放前面会把顶层包 `autoforge` 本身当成"缺失依赖"给桩掉。
2. **桩模块缺 `__path__`**：`import homesdk.consent` 时 Python 先取 `homesdk.__path__`，拿不到就抛 "'homesdk' is not a package"，**根本不会去问 meta_path 里的 finder**。
3. **W43 自己引入的回归（W45g）**：第十七轮加 `_exec_nested` 时删掉了"模块内类里找同名方法"这条路径 ⇒ `Scheduler._satisfied` 被 exec 成模块级函数 ⇒ `__qualname__` 只剩 `_satisfied` ⇒ `_self_proxy` 认不出类 ⇒ self 喂替身 ⇒ `any()` 一次都不递归 ⇒ **判 ok**。

第 3 条最关键：**这是假阴性，而且比 `unavailable` 更危险**——`unavailable` 明说"没测到"，这个直接说"没问题"。它是靠 W45f 的多形态试探才暴露出来的。

---

## 三、26 个目标的最终分布

```
cyclic_crash  19
safe           6    （含对照组 expr.py:270 _walk_operand、af_expect.py:85 _apply_op）
inconclusive   1    （af_nl_parse.py:1188 _build）
unavailable    0
```

**只试 "or" 才崩的**：`af_scheduler._satisfied`（唯一一条 `var=or`）。

**靠桩才跑起来的**：同上一条（`via_stub=True`）——它的模块依赖私有 `homesdk`（要求 Python≥3.11，装不上），结论强度低于真实依赖下实测，已在 `poc.json` 顶层标注，不虚增确证强度。

**`_build` 未判定的说明**：探针在该目标上会把沙箱资源打挂（4 次重试均 HTTP 502）。手工带内存限额实测为 `AttributeError`（形状不匹配），非崩溃。**记为"未判定"，不等于"没问题"。**

---

## 四、正式审计结果

| 指标 | 数值 |
|---|---|
| 分析器 | 26/26 ok（门禁 **32** 项全过） |
| 原始命中 | **1083** |
| 去重后待办 | **1052**（抑制 4 条） |
| 严重度 | high **85** / medium 682 / low 285 |
| 跨轮 diff | **new 0 / gone 0** |
| 台账 | **F1–F16 全部 still_open** |
| 递归 PoC | **26 目标：cyclic_crash 19 / safe 6 / inconclusive 1 / unavailable 0** |
| 状态 PoC | 9/9 data_lost |
| 出站 PoC | bypass_confirmed_unfixed |
| 失败开放 PoC | 2/2 fail_open_confirmed |
| 依赖审计 | homesdk 58 条（high 3 / medium 43 / low 12） |

门禁：`ok=True`、analyzers=32、failures=[]、drift=0；extras 全绿。

**new 0 / gone 0 合理**：本轮改的是 PoC 探针，未动分析器规则，静态命中面与上轮一致（1083）。

---

## 五、四类 PoC 现状

| 类别 | 规则族 | 状态 |
|---|---|---|
| 递归 | RSC-02 / GOD-02 | **26 目标：19 崩 / 6 safe / 1 未判定 / 0 测不到** |
| 状态损坏 | DO-01..04 | 9/9 data_lost |
| 出站护栏 | OUTB-01 | 1/1 确证旁路 |
| 失败开放 | FO-01 | 2/2 确证 |

---

## 六、仍未覆盖

| 项 | 状态 |
|---|---|
| pip-audit（依赖 CVE / 供应链） | ❌ osv.dev、pypi.org 均 HTTP 403；且跨 bash 调用丢失 |
| semgrep（SAST 交叉验证） | ❌ 安装失败（`File too large`），无 CLI |
| homesdk 运行时探针 | ❌ wheel 要求 Python≥3.11，环境 3.10（本轮用桩绕过导入，非真实依赖） |
| pytest（变异测试） | ❌ 未接入 |
| `af_nl_parse._build` | ⚠️ 探针导致沙箱资源耗尽，未判定 |

**依赖 CVE 面十九轮都没扫。** `homesdk` 是未上 PyPI 的私有 wheel——**风险未知，不是无风险**。

F1–F7、F9、F11、F16 未纳入补丁。F8/F10/F12/F13/F14/F15 补丁只存在于只读副本 `/data/workspace/repos/af-patched`，**原仓库未改动**。

---

## 七、一条工程约束

W45f 让每个目标的探针数翻倍（2 形态 × 5 深度），单轮耗时显著上升，本轮已多次触发沙箱超时。**后续应在 `poc.py` 中把"形态"纳入去重键，避免对同一函数重复跑全深度阶梯。**
