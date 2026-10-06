# AutoForge 第十三轮审计报告：同形状横向传播扫描

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 本轮主题：**不再写新规则——把仓内已确认的正确实现当模板，反查同形状的所有调用点**
> 判定标准：严格档 —— **不实测不升级为缺陷**；每条附实测或明确标注推断
> 报告日期：2026-10-06

---

## 一、执行摘要

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R13-01** | 🟠 High | **`device_acl.json` 损坏 → Tier-0 设备保护整段跳过**（fail-open），与同仓 `revoked.json` 的处理方向相反 | `af_service.py:731` + `af_scanner.py:779` |
| **R13-02** | 🟡 Medium | **`credentials.json` / 凭据读取损坏无日志**——静默回退到 secret/env，可能误用陈旧令牌 | `af_config.py:54` · `af_service.py:2336` |

### 方法上的转向（这是本轮最重要的东西）

前十二轮的做法是"写规则 → 扫 → 分诊"。第十一轮证明这条路会漏（规则级召回 5/9），第十二轮发现**正确实现往往已经在仓里了**——`af_telemetry` 的「坏行保留（不静默丢数据）」连说明理由的注释都写好了，而 `af_metrics` 是同一形状的裸 `json.loads`。

所以第十三轮**不写新规则**，改成：

> **模板 = 仓内已确认为正确的实现；反查 = 全仓搜同形状调用点。**

比写规则快（模板已有，不用设计判据），也更准（形状来自本仓真实代码）。

### 一个必须先说的坦诚

**我自己写的传播扫描器，漏掉了本轮最重要的那条缺陷 R13-01。**

P2 模板只认**直接 `json.loads(...)`**，而 `af_service.py:731` 是 `DeviceGuardRegistry.from_file(path)`——解析藏在被调用函数里。**这正是第十一轮我批评 auditkit 的同一类假阴性。**

R13-01 是人工 grep 时发现的。已记入 lessons：**扫完命中很少时，先手工 grep 该 shape 的间接形态确认一遍。**

---

## 二、工作流迭代

| 轮次 | 做法 | 确认缺陷 |
|---|---|---|
| 八~十 | 写专题扫描器（E/F/G/H/I 族） | 7 |
| 十一 | 换第三方工具 + 召回率验证 | 2（auditkit 自身） |
| 十二 | 候选自动分诊（T1/T2/T3） | 2 |
| **十三** | **模板反查（不写新规则）** | **2** |

### 新增资产

**`scripts/scan_round13.py`** —— 同形状横向传播扫描器，四组模板全部取自仓内已确认实现：

| 模板 | 正确实现 | 已确认的反例 |
|---|---|---|
| P1 JSONL 逐行解析 | `af_telemetry.py:189`（坏行 try + 保留） | `af_metrics.py:222`（第十二轮 R12-01） |
| P2 安全策略文件加载 | `af_auth.py:240`（毒化标志 → fail-closed） | `af_service.py:731`（**本轮 R13-01**） |
| P3 坏文件隔离 | `af_predict.py:910`（`os.replace` → `.corrupt` 保留现场） | — |
| P4 数值型环境变量解析 | `af_bus._env_number`（try + 上下界 + warning） | 6 处裸转换（第九轮 R9-01） |

### 四个新范式（已记入 lessons 第 43–46 条）

**43** 不写新规则，把仓内正确实现当模板反查
**44** 模板反查必须跨函数展开——只认直接调用会漏掉被 helper 封装的实现（**我自己踩了**）
**45** 安全策略文件加载失败的方向，必须与同仓另一个策略文件对齐
**46** 变异测试要双向做：M1 杀得死 ≠ 覆盖到位，**M2（注入修复）存活才暴露真实缺口**

---

## 三、确认缺陷

### 🟠 R13-01　`device_acl.json` 损坏 → Tier-0 设备保护整段跳过（fail-open）

**位置**：`src/autoforge/af_service.py:731`（加载）+ `af_scanner.py:779`（消费）

```python
# af_service.py:721-732  load_device_guard()
    try:
        guard = DeviceGuardRegistry.from_file(path)
        ...
        return guard
    except (OSError, ValueError) as exc:
        logger.warning("DEVICE_ACL_LOAD_FAILED path=%s err=%s", path, exc)
        return None          # ← 保护注册表变成 None

# af_scanner.py:779-780  StaticScanner._scan_automation()
        if self._guard is None:
            return           # ← Tier-0 检查整段跳过
```

**实测**：

```
guard 加载: True | lock.front tier = 0
            | light.kit tier = None

── device_acl.json 损坏（截断）──
  LOG[WARNING] DEVICE_ACL_LOAD_FAILED path=/tmp/.../device_acl.json err=Unterminated string...
  load_device_guard → None
  ⇒ StaticScanner `if self._guard is None: return`
  ⇒ 所有实体的 Tier-0 检查整段跳过（af_scanner.py:779-780）
```

**后果**：Tier-0 是"必须人审"的保护区。策略文件一坏，**受保护设备的读写校验全部失效，自动化照常通过校验、照常可部署**。

**无第二道防线**：全仓 `match_tier` / `TIER0` 判定**只出现在 `af_scanner.py`**，保存与下发路径没有复核（已 grep 确认）。

### 最关键的证据：同仓另一个策略文件方向相反

| 文件 | 损坏时的处理 | 方向 |
|---|---|---|
| `revoked.json`（撤销黑名单）`af_auth.py:240` | 置 `_revoked_poisoned = True` → `authenticate()` 对**全部令牌**返回 None | ✅ **fail-closed** |
| `device_acl.json`（设备保护）`af_service.py:731` | 记日志 → `return None` → 保护消失 | ❌ **fail-open** |

**两个都是"安全策略文件读不出来"，方向相反。** `af_auth` 的注释甚至写明了理由：

> 撤销黑名单读不出来 ≠ 名单为空。此处若按"空名单"继续跑，被撤销过的令牌会全部复活（fail-open）。

而 `device_acl` 的 docstring 只写了"不让坏策略文件把服务打挂"——**只考虑了可用性一侧，没提"保护同时消失"的代价**。

**这条不需要额外论证**：不一致本身就是证据。（第七轮 19/29、第十轮 33、第十二轮 41 同族）

### 缓解因素（必须诚实说明）

它**不是静默**——`load_device_guard` 每次 `scan()` 都重新读盘（热更新设计），所以**每次扫描都会打一条 WARNING**。运维在日志里能看到。

但**日志不等于防护**：服务照常工作、自动化照常通过校验、Tier-0 设备照常被操作。而且 WARNING 混在大量扫描日志里，实际可发现性远低于 fail-closed。

**变异测试（双向，8 个用例）**：

| 变异 | 内容 | 结果 |
|---|---|---|
| 基线 | — | 8 passed, 1 skipped |
| **M1** | 恒返回 None（模拟当前 fail-open 行为） | **1 failed** ✓ 被杀死 |
| **M2** | 解析失败改为抛异常（fail-closed 修复） | **8 passed** ✗ 存活 |

M1 被杀死只说明"加载成功路径"有测试；**M2 存活才暴露真问题：损坏这条路径没有任何测试覆盖**。

**修复建议**（二选一）：

```python
# A) 与 af_auth 对齐（推荐）：置毒化标志，扫描对受保护实体一律报 ERROR
except (OSError, ValueError) as exc:
    logger.error("DEVICE_ACL_LOAD_FAILED path=%s err=%s —— 保护停用，"
                 "扫描将拒绝可能触碰受保护实体的自动化", path, exc)
    return _POISONED_GUARD        # match_tier() 恒返回 0

# B) 至少让"文件不存在"与"文件损坏"分道：前者 None（向后兼容），后者抛错
```

**回归验证**：
1. `device_acl.json` 截断 → 扫描应**报错**而非静默放行
2. 注入 R13-01 修复 → `test_v1_4_device_guard.py` 应有**新测试失败**（证明钉住了）

---

### 🟡 R13-02　凭据文件读取损坏无日志，静默回退到 secret/env

**位置**：`af_config.py:54` `_load_credentials()`、`af_service.py:2336`

```python
def _load_credentials(self) -> dict[str, Any]:
    try:
        return json.loads(self._creds_path().read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}          # ← 无日志
```

**回退链**：`get_ha_token()` = `self._creds.get("ha_token") or load_secret(...) or env`。

**危害**：`credentials.json` 损坏时静默变空 → 回退到 secret 文件 / 环境变量。**若那里存的是旧令牌，就会静默使用陈旧凭据**，而界面显示的是 `_mask()` 出的 `****len=N`（第九轮已确认脱敏正确）——**看不出用的是哪一份**。

**严重度为什么是 Medium**：方向偏 fail-closed（令牌全无 → HA 返回 401，不会放行）。危害是"用了哪份凭据不可知"，不是安全闸绕过。`af_service.py:2336` 同形（`ha_token = ""` + `pass`，watch 起在无令牌状态）。

**修复**：各加 1 行 `logger.warning`，明确"凭据文件解析失败，已回退到 secret/env"。

---

## 四、传播扫描的四组结果

```
══ P1_jsonl_unguarded：1 条 ══
   af_metrics.py:222  逐行 JSONL｜**无 try** ✗          ← 第十二轮 R12-01，已确认

══ P2_policy_load：5 条 ══
   af_auth.py:234      毒化标志 ✓（fail-closed）        ← 模板（正确）
   af_config.py:54     return None（**无日志**）        ← R13-02
   af_service.py:2336  return None（**无日志**）×2      ← R13-02
   af_spec.py:164      return None（**无日志**）
   ⚠️ af_service.py:731（device_acl）**被漏掉**——解析藏在 from_file() 里

══ P3_quarantine_sites：3 条 ══
   af_predict.py:907 _quarantine() / :910 '.corrupt'    ← 全仓唯一隔离范式

══ P4_env_unguarded：6 条 ══
   af_api/af_config/af_service×2/af_undo×2              ← 第九轮 R9-01，已确认
```

**P1 与 P4 的结论是"干净的"**：全仓 18 处 JSONL 逐行解析，只有 R12-01 一处无 try；环境变量 7 处数值解析，6 处裸转换（已报）。**两个模板反查都只召回已确认的缺陷，没有新增——这说明模板反查的方法是可信的**（不会像新规则那样产生大量待分诊噪音）。

**P3 值得单独说**：`af_predict.py:910` 的 `.corrupt` 隔离是**全仓唯一**，其他任何地方遇到坏文件都没有"保留现场"这个动作。第九轮、第十轮、第十二轮报的四条缺陷（爆破半径负数、持久化解析、指标缓冲、待批僵尸）**都适用这个范式**。

---

## 五、本轮验证通过（确认无问题）

| 项目 | 验证方式 | 结论 |
|---|---|---|
| **`af_auth` revoked 毒化标志** | 读实现 + 第十二轮复核 | ✅ **全仓处理得最好**——fail-closed 且注释写明"读不出来 ≠ 名单为空" |
| **JSONL 逐行解析（其余 17 处）** | 传播扫描 | ✅ 全部有 try 保护 |
| **`af_predict._quarantine`** | 读实现 | ✅ 坏文件挪走保留现场，且 `except OSError` 兜底 |
| **`af_scanner` 其余守卫** | grep 确认 | ✅ 僵尸触发源闸、ask 弃用提示等均在 `_guard is None` 之前，不受影响 |
| **`device_guard` 热更新** | 读实现 | ✅ `load_device_guard` 每次 scan 重新读盘，规则改动即时生效（设计正确） |

---

## 六、已排除（rejected）

| 候选 | 理由 |
|---|---|
| **`af_cli.py:1042` `json.loads(Path(events).read_text())` 无 try** | 是**整文件解析成 list**（非 JSONL 逐行），CLI 用户显式传参，失败即 traceback——可见且不影响服务。非缺陷 |
| **`af_spec.py:164` return None 无日志** | 解析 AF-Spec 文本，属**输入解析**而非安全策略加载；失败后上层报错给用户。不适用 P2 档 |
| **P4 六处裸转换** | 第九轮 R9-01 已报，本轮不重复计入 |
| **`af_flock` / `af_catalog` 等** | 前轮已复核通过 |

---

## 七、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R13-01 device_acl 改 fail-closed | 约 6 行 + 1 个测试 | 安全闸可因文件损坏整段消失，无第二道防线 |
| **P2** | R13-02 凭据读取加 warning | 各 1 行 | 让"用的哪份凭据"可追溯 |
| **P2** | 给 `.corrupt` 隔离范式写公共 helper | 约 15 行 | 覆盖第九/十/十二轮四条缺陷的修法 |

---

## 八、十三轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码 | 22 |
| 七~十二 | 生命周期 / 门禁 / 测试 / 配置 / 持久化 / 工具 / 分诊 | 13 |
| 十三 | 模板反查 | 2 |

**"同一形状、两处实现、方向相反"这个模式，十三轮里出现了八次。** 第十三轮这条（device_acl vs revoked）有个此前没有的特点：

> **两处都是安全策略文件，一处 fail-closed 一处 fail-open，而且 fail-closed 那处把理由写在了注释里。**

这说明团队在写 `af_auth` 时完整地想过这个问题（"读不出来 ≠ 名单为空"），**但这个想法没有传播到 `device_acl`**。

**本轮的方法转向，本质上就是为了对抗这个模式**：
- 写新规则 → 依赖"我能想到所有形态" → 会漏（第十一轮 5/9）
- 模板反查 → 依赖"仓里已有的正确实现" → 不漏形态，只漏间接调用（本轮我自己踩了一次）

**给工程团队的一句话**：`af_predict._quarantine`（隔离）、`af_bus._env_number`（配置解析）、`af_auth` 毒化标志（策略加载）、`af_telemetry` 坏行保留（JSONL）——**四个正确范式，四个文件，全仓各一处**。它们本该是公共 helper，现在是四个孤岛。第九、十、十二、十三轮报的缺陷，全部落在"这些孤岛之外的地方"。

**把这四个抽成公共模块，比再写十条规则管用。**

---

## 九、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| **同形状横向传播扫描器（P1–P4）** | **`audit-env/scripts/scan_round13.py`** |
| 传播扫描明细 | `audit-env/reports/round13-propagation.json` |
| 候选自动分诊器（T1/T2/T3） | `audit-env/scripts/triage_round12.py` |
| 误报修正手册（42 条 + 本轮 4 条 = **46 条**） | `audit-env/scripts/lessons-round2.md` |
| ADM-auditkit（含自建 CLI） | `/data/workspace/adm-auditkit/ADM-auditkit-main/auditkit` |
| 持久化 / 配置面扫描器 | `scan_round10.py` · `scan_round9.py` |
| 变异测试工具 | `mutation_gates.py` · `mutation_invasive.py` |

### 本轮新增 lessons（43–46）

- **43** 不写新规则——把仓内已确认的正确实现当模板反查同形状调用点
- **44** 模板反查必须**跨函数展开**；只认直接调用会漏掉 helper 封装的实现（**本轮自己踩到**）
- **45** 安全策略文件加载失败的**方向**必须与同仓另一个策略文件对齐；不一致即候选缺陷
- **46** 变异测试双向做：**M1 杀得死 ≠ 覆盖到位，M2（注入修复）存活才暴露真实缺口**

---

## 附：环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12（项目声明 `>=3.11`） |
| 本轮实测 | 4 组（device_acl 损坏 fail-open / 凭据静默回退 / 变异测试 M1·M2 / 传播扫描四模板） |
| 新增依赖 | `jsonschema` 装入 `audit-env/pylib` |
| 仓库状态 | `af_service.py` 已与备份校验一致（diff 通过）；无残留探针 |
| 备注 | 沙箱重置导致 `.git` 丢失，改用备份文件 diff 校验还原完整性 |
