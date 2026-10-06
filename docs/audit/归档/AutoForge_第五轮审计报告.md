# AutoForge 第五轮审计报告（失败开放守卫 + 缓释答非所问检测）

- **审计对象**：`lidicn/AutoForge`（不变）
- **审计工具链**：`lidicn/ADM-auditkit`（本轮继续迭代）
- **审计焦点**：稳定性与功能性缺陷（口径同前四轮）
- **本轮轮次**：`round-005`
- **产物**：`/data/workspace/repos/adm/projects/AutoForge/round-005/`
- **报告日期**：2026-10-06

---

## 一、本轮最重要发现：F12 —— 别名撞车保护被静默绕过（fail-open）

**与 F8/F10 同源（读失败被静默降级），但后果方向相反**：F8/F10 是"清空数据"，F12 是"**放行本该拦住的操作**"。

### 机制

`af_store.py:157` `_dir_owner()` 读最新记录拿归属名，读失败返回 `None`：

```python
def _dir_owner(self, name) -> str | None:
    try:
        record = json.loads((directory / f"v{target}.json").read_text())
    except (OSError, ValueError):
        return None          # ← 读失败当作"没有主人"

def _assert_name_owns_dir(self, name):
    owner = self._dir_owner(name)
    if owner is not None and owner != name:      # ← None ⇒ 条件为假 ⇒ 放行
        raise ArchiveNameConflict(...)
```

`owner is not None` 这个判断的语义是"**取不到归属即视为无约束**"——正是 fail-open。

### 实测

`_dir()` 的消毒规则把 `.` 映射成 `_`，所以 `a.b` 与 `a_b` **映射到同一个物理目录**：

```
正常状态：_assert_name_owns_dir('a_b') → 抛 ArchiveNameConflict  ✓ 拦截成功
损坏 v1.json（不可解析）后：_dir_owner('a_b') → None
                          _assert_name_owns_dir('a_b') → 未抛异常  ✗ 保护绕过
```

**后果**：后写入者静默覆盖先写入者的整条归档，无任何提示、无冲突记录。触发条件仅为一次记录损坏。

**修复方向**：守卫的默认值必须是"不放行"。读失败要区分"目录/记录不存在"（可当无主人）与"解析失败"（必须拒绝写入）。

---

## 二、本轮第二项确证：F13 —— 偏好记录整档不可解析后历史丢失

`af_preference._load()` 经 `read_jsonl_bounded` 载入，写侧 `_rewrite_all()` 是"内存有多少写多少"的全量覆盖。

**实测**（`max_records=1000`，slack = `max(64, 1000//8)` = 125）：

```
写入 120 条历史（turn_on）
令整档不可解析 → 新实例载入 0 条
追加 130 条（> slack 触发压缩）→ 文件仅剩 130 条新记录
原 120 条历史：全部丢失
```

**触发条件比 F8 窄**：`read_jsonl_bounded` 会**逐行跳过坏行**，所以单行损坏不触发，必须**整档不可解析**（编码损坏 / 跨版本格式不兼容 / 被别的工具整档改写），且要累积到 slack 次追加才压缩。

定 medium 而非 high，纯粹因为触发条件窄；形态与 P0 的 F8/F10 完全一致。

---

## 三、工作流迭代（本轮 3 项）

### W10【分诊信号】识别"缓释标记答非所问"

第四轮末尾记下的盲区：F10 三处凭证存储**没能进分诊 Top 16**，因为 `af_auth.py` 的 BUG-20 注释（"不重读会抹掉别的进程写入的码"）被判为"已修标记"减了 80 分。

但那条注释说的是**防住了"不重读"**，缺陷是**没防住"重读失败"**——注释恰恰是最有力的证据，却被当成了缓释证据。

**修复**：新增 `RULE_CONCERN` 字典，每条规则声明自己关心什么词（DO-* 关心"损坏/截断/解析失败/读失败/读不到/格式不兼容/坏行/非法"；RSC-* 关心"深度/预算/上限/递归/depth/budget/limit"；AFS-01 关心"失败/异常/不可达/超时"）。**注释里出现"已修"标记但不含本规则关心的词 ⇒ `mitigation_mismatch=True` ⇒ 加 40 分而非减 80 分**。

**效果**（本轮命中 49 条）：

| 位置 | 第四轮 | 第五轮 |
|---|---|---|
| `af_auth.py:539/665`（CONC-08） | 未进 Top 16 | **第 4、5 名** |
| `af_store.py:157`（FO-01，F12） | 规则不存在 | **第 6 名** |
| `af_auth.py:544/670/:413`（F10 三处） | 未进 Top 16 | 第 20–21 名 |

反转 120 分，把第四轮被压下去的真缺陷捞了回来。

### W11【分诊信号】静默失败的后果分级

同样是 `except → return {}`，**喂给覆盖写路径**和**喂给只读路径**后果天差地别。新增跨规则 join：`destructive_sites()` 收集全部 DO-* 命中的读/写位置，`AFS-01 / ERRH-02 / AFS-07` 命中若落在这些位置上 ⇒ `feeds_overwrite=True` 加 50 分；否则减 60 分。

本轮 `feeds_overwrite=15`。榜首三条（`af_catalog:557`、`af_config:57`、`af_store:347`）因此拿到 210 分——它们正是 F8/F9 的读侧。

### W12【新增规则】FO-01-failopen-guard

F12 的形态此前**没有任何规则覆盖**：`_dir_owner` 既不叫 `assert_*` 也不 `raise`，**守卫性质完全来自调用方**（`_assert_name_owns_dir` 里那个 `is not None` 判断）。只看函数自身根本看不出它是守卫。

判据因此必须做调用方数据流：

1. 函数形似守卫（`_assert*`/`check_*`/`verify_*`/`validate_*`，或 `*_owns`/`*_allowed` 后缀）**或**
2. 调用方用 `X is not None → raise` 形状放行（支持经变量中转：`X = f()` → `if X is not None ...: raise`）
3. 且该函数在 `except` 里吞异常返回 `None`/`False`/`""`/`continue`/`pass`

**调试中踩的两个坑**（都已记进规则注释）：

- **不能用"函数内有 raise"当守卫证据**——`build_app` 这类大函数会被大量误报。守卫证据必须来自**调用方**的放行形状
- **必须支持经变量中转**——`owner = self._dir_owner(name)` 之后再判断，直接匹配调用点会漏掉 F12

**实测**：命中 3 条，其中 `af_store.py:157` 是 F12 真阳性，另外两条见第四节裁决。

`auditkit round` 现在 24 个分析器，门禁 clean 假阳性 0。

---

## 四、本轮裁决（2 条）

**`af_apply.py:285` `check_store_cross_conflicts` —— 定为 `degraded`，不列为缺陷。**

读不到的归档 `continue` 跳过，产出更少的冲突。但它是**建议性预检**（逐条读归档找跨归档冲突，产出 findings 供展示），只读、无副作用、不阻断任何操作。这与 F12 的本质区别：F12 **守卫着一个操作**（放行 ⇒ 归档被覆盖），这里**不守卫任何东西**（跳过 ⇒ 少报几条）。已登记抑制（附理由）。

**`af_store.py:182` `assert_deletable` —— 保留在待办，不单独立项。**

坏记录 `continue` 跳过，与 F12 同形态。但它**检查目录里每一条记录**，只要有任意一条可读且属于别人就会抛；只有**全部记录都不可解析**时才 fail-open。而删除是不可逆操作（`rmtree` + 逐条 unlink），从严默认值应该是"拒绝"。

属 F12 同族的**边缘实例**，触发条件比 F12 更窄（F12 只需最新一条损坏，这里需全部损坏），故不单独立项，在报告中记录为 F12 修复时应一并覆盖。

---

## 五、第五轮审计结果

| 指标 | 数值 |
|---|---|
| 原始命中 | 1233 |
| 去重 | -80 |
| 抑制后待办 | **1117**（抑制 36 条） |
| 严重度 | high **60** / medium 735 / low 277 / info 45 |
| 台账 | **F1–F13 全部 still_open**，fixed 空、unknown 空 |
| 跨轮 | `new=2`、`gone=0`、`persistent=1115` |

`new=2` 即 `af_store.py:157`（F12）与 `:182`（F12 边缘实例），均由新增 FO-01 规则发现。`gone=0`。

### 台账指纹全量校验通过

对 F1–F13 的全部 pattern 逐条实测匹配，**13 条全部 ≥1 处匹配**（F12 达 2 处）。无"永不匹配"的僵尸指纹——这是第四轮修掉 2 条后建立的纪律，每轮校验。

### 剩余 60 条 high

| 规则 | 条数 | 归属 |
|---|---|---|
| RSC-02-recursion-no-budget | 23 | F2 / F6 族 |
| AFS-01-silent-failure | 19 | F7 / F8 族 |
| DO-01 / DO-02 / DO-03 | 4 / 2 / 2 | F8–F11（已确证） |
| RSC-05-limit-inconsistency | 3 | F2 |
| CONC-08-partial-lock-coverage | 2 | F3 |
| FO-01-failopen-guard | 2 | F12（已确证）+ 边缘实例 |
| TX-04 / IN-05 / RMW-01 | 各 1 | — |

**high 里 15 条已归属到已知缺陷族**（DO-* 8 条 + FO-01 2 条 + CONC-08 2 条 + RSC-05 3 条），剩下的散点集中在 RSC-02（23 条）与 AFS-01（19 条）两族。

---

## 六、缺陷台账（F1–F13）

| ID | 严重度 | 一句话 | 位置 | 优先级 |
|---|---|---|---|---|
| **F8** | high | 读侧返空 + 写侧覆盖 → 清空全部历史（3 处实测） | `af_store.py:343` | **P0** |
| **F10** | high | 凭证三处同形态：损坏后一次签发清空全部 | `af_auth.py:544` | **P0** |
| F1 | high | 扫描器校验顺序倒置，护栏排在遍历之后 | `af_scanner.py:348` | P0 |
| **F12** | high | 别名归属核对 fail-open，保护被静默绕过 | `af_store.py:157` | **P1** |
| F9 | medium | 别名表同形态覆盖 | `af_catalog.py:567` | P1 |
| F6 | medium | Trigger group 嵌套全程无深度上限 | `af_ir/models.py:114` | P1 |
| F2 | medium | 静态遍历不内建预算 | `af_ir/expr.py:255` | P1 |
| **F13** | medium | 偏好整档不可解析 → 压缩后历史丢失 | `af_preference.py:560` | P2 |
| F7 | medium | canary 回滚链双层静默失败 | `af_adapters/ha.py:164` | P2 |
| F3 | medium | 凭证 `_load` 锁外裸写 | `af_auth.py:539` | P2 |
| F4 | low | `_expr_atom` 递归无预算（已降级） | `af_nl_parse.py:629` | P3 |
| F5 | low | `held_by_other` 宽捕 `OSError` | `af_flock.py:132` | P3 |
| F11 | low | 遥测计数同形态覆盖 | `af_catalog.py:499` | P3 |

**按族合并看，五轮下来实际只有 5 个根因**：

1. **读失败静默降级**（F8/F9/F10/F11/F13）—— 5 组实例，9 处代码
2. **读失败静默放行**（F12 + `assert_deletable` 边缘实例）
3. **护栏位置/预算缺失**（F1/F2/F6）
4. **锁粒度不一致**（F3）
5. **有意设计与实现不符**（F7）

---

## 七、仍未覆盖（五轮均未改善）

| 工具 | 未覆盖面 |
|---|---|
| semgrep | SAST 交叉验证 |
| detect-secrets | 密钥扫描 |
| pip-audit | **依赖 CVE / 供应链漏洞**（私有 `homesdk` wheel） |
| pytest | 测试运行时探针、变异测试 |
| skill-scan / graph | MCP 配置面、调用图可达性 |

medium 735 条仍只做抽样确证。RSC-02 剩余 23 条中除 F2/F6 已知实例外，仍可能有未浮出的真缺陷。

---

## 附：本轮产物

| 文件 | 内容 |
|---|---|
| `round-005/findings.json` | 去重抑制后 1117 条 |
| `round-005/triage.md` | 分诊排序 Top 40（7 信号） |
| `round-005/suppressed.json` | 36 条抑制（含理由与 verdict） |
| `round-005/cross-round-diff.json` | new 2 / gone 0 / persistent 1115 |
| `baseline/bugs.json` | 台账 F1–F13（指纹已全量校验） |
| `core/analyzers/failopen_guard_defects.py` | 本轮新增 FO-01 规则 |
| `core/triage.py` | 新增 `mitigation_mismatch` / `feeds_overwrite` 两个信号 |
