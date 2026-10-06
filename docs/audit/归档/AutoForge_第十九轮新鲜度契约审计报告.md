# AutoForge 第十九轮审计报告：新鲜度契约（缓存失效与重载语义）

> 审计对象：`https://github.com/lidicn/AutoForge`（main 分支快照）
> 本轮主题：**"读到的是不是最新的"——缓存键充分性、写后失效、重载语义**
> 判定标准：严格档 —— **不实测不升级为缺陷**
> 报告日期：2026-10-06

---

## 一、执行摘要

第十八轮发现 CLI 与服务层"护栏不对等"，根因是**状态载体不同**。第十九轮顺着这条线往下一层：**即便载体相同，"读到的是不是最新的"也没有保证**。

| 编号 | 级别 | 一句话 | 位置 |
|---|---|---|---|
| **R19-01** | 🔴 High | **凭据损坏 → TTL 自动 `refresh()` 把内存里的有效令牌清空**，`connection_revision` 从 1 降到 0，全程无日志 | `af_config.py:139` `refresh` |
| **R19-02** | 🟠 Medium | **3 处 `(mtime, size)` 缓存键不足以唯一确定内容**，且写侧不主动失效缓存（唯一另一处清缓存是 `return` 后的死代码） | `af_catalog.py:278/714` · `af_service.py:693` |

### 两个缺陷的共同形状

它们都是**"用可能失败为空的读取结果，去替换一个已知有效的值"**：

- R19-01：`self._creds = self._load_credentials()`（失败时返回 `{}`）→ 覆盖掉内存里的有效凭据
- R19-02：`cached[0] == st_mtime and cached[1] == st_size` → 用两个弱指纹"证明"内容没变

**这是 RMW-on-silent-empty 的第五次出现**——前四次是"读空 → 改 → 写回文件"（第十轮 R10-02、第十六轮 R16-01/02），这次是**反向的变体**：覆盖内存态而非落盘态。

---

## 二、工作流迭代

| 轮次 | 方法 | 确认缺陷 |
|---|---|---|
| 十六 | 失败方向枚举 + RMW 模板反查 | 2 |
| 十七 | 契约声明提取 + 声称/实现对照 | 1 |
| 十八 | 入口对等性（汇聚点比对） | 1 |
| **十九** | **新鲜度契约（缓存键充分性 + 写后失效 + 重载语义）** | **2** |

### 本轮的三个新判据

**67** RMW 第五变体：**refresh() 用可能为空的加载器覆盖内存好状态** —— 判据是"加载失败时它把内存里的好状态换成什么"
**68** `(mtime, size)` **不是内容的充分指纹** —— 缓存键必须能唯一确定内容
**69** **写侧不主动失效缓存** = 把正确性外包给"mtime 一定会变"；顺带发现 `return` 后的不可达死代码
**70** 新鲜度类缺陷用**"等长替换 + `os.utime` 恢复 mtime"** 直接复现，不用推理

---

## 三、确认缺陷

### 🔴 R19-01　凭据损坏 → TTL 自动重载把内存里的有效令牌清空

**位置**：`src/autoforge/af_config.py:139` `refresh()`，配合 `:52` `_load_credentials` / `:58` `_load_revision`

```python
def _load_credentials(self) -> dict[str, Any]:
    try:
        return json.loads(self._creds_path().read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}                    # ← 静默返回空（已确认为 R13-02）

def refresh(self) -> None:
    """从磁盘重读凭据与代数：外部进程改了 credentials.json/revision.json
    后，本进程缓存的 Config 不再永久过期。"""
    with self._lock:
        self._creds = self._load_credentials()          # ← 坏文件时赋成 {}
        self.connection_revision = self._load_revision()  # ← 坏文件时赋成 0
```

**而 `refresh()` 是自动触发的**（`get_config` 的 TTL 缓存，默认 60s）：

```python
#: 进程内单例缓存（按 root 字符串键）… R-54：不再永久缓存——按 TTL 重读磁盘
_CONFIG_TTL_S = float(os.getenv("AUTOFORGE_CONFIG_TTL_S", "60"))

def get_config(root):
    ...
    if _CONFIG_TTL_S > 0 and now - loaded_at > _CONFIG_TTL_S:
        cfg.refresh()          # ← 每 60 秒无条件重载一次
```

**实测**：

```
初始: {'ha_token': '****len=24', 'api_token': '****len=19', 'connection_revision': 1}
  get_ha_token(): HA_TOKEN_REAL_123456...

── 磁盘文件损坏（磁盘故障 / 备份恢复 / 半截写入）──
── TTL 到期后自动 refresh() ──
  describe(): {'ha_token': '', 'api_token': '', 'connection_revision': 0}
  get_ha_token(): ''
  connection_revision: 0  ← 从 1 降到 0
  ⇒ 内存里原本有效的令牌被静默清空，回退到 env（通常为空）⇒ HA 调用 401
```

**危害链**：一个正在正常运行的 watch 进程，一旦 credentials.json 损坏（磁盘故障、备份恢复、半截写入），**60 秒内**它的有效令牌会自行消失。此后所有 HA 调用 401，且日志里**没有任何线索**（`_load_credentials` 无日志）。

**为什么是 High 而非 Medium**：

1. **TTL 让它必然发生**——不是"下次启动才会"，是运行中的进程每 60 秒检查一次，损坏后必然在 60 秒内触发。
2. **`connection_revision` 从 1 降到 0 是"回退"**——代数的语义是单调递增的连接版本，降到 0 会让下游的"代数比对"逻辑（适配器 `_refresh_if_stale`）行为异常。
3. **静默**——与项目铁律 #5（EXEMPT ≠ VERIFIED）直接冲突："读不出来"被当成了"没有"。

**与 R13-02 的分工**（不要重复计）：

| | R13-02（第十三轮） | R19-01（本轮） |
|---|---|---|
| 起点 | 进程**启动时**加载，`_creds` 从一开始就是空 | 进程**运行中**已持有有效凭据 |
| 后果 | 用不了凭据（起不来/401，立刻可见） | **有效凭据在运行中凭空消失**（症状延后、归因困难） |
| 触发 | 一次性 | **TTL 周期性触发，必然发生** |

**修复建议**（约 6 行）：

```python
def refresh(self) -> None:
    """重载；**读失败保留旧值**——用空的覆盖有效凭据等于把"读不出来"当"没有"。"""
    with self._lock:
        try:
            creds = json.loads(self._creds_path().read_text(encoding="utf-8")) or {}
            if creds:
                self._creds = creds
        except (OSError, ValueError) as exc:
            logger.error("CREDENTIALS_RELOAD_FAILED path=%s err=%s —— 保留内存中现有凭据",
                         self._creds_path(), exc)
        rev = self._load_revision()
        if rev > 0 or self.connection_revision == 0:
            self.connection_revision = rev     # 代数永不回退
```

**回归验证**：
1. 有效凭据 + 损坏文件 + `refresh()` → `get_ha_token()` 应**仍返回原值**（当前：变空）
2. 同上 → `connection_revision` 应**保持 1**（当前：变 0）
3. 同上 → 应产生一条 `logger.error`

---

### 🟠 R19-02　`(mtime, size)` 缓存键不足以唯一确定内容（3 处）

**位置**：

| 文件:行 | 缓存 | 指纹 |
|---|---|---|
| `af_catalog.py:278` | `DeviceCatalog._cache`（设备目录） | `(st_mtime, st_size)` |
| `af_catalog.py:714` | `_exp_cache`（经验库计数） | `(st_mtime, st_size)` |
| `af_service.py:693` | `_ENTITY_HEALTH_CACHE`（实体健康视图） | `(st_mtime, st_size)` |

**实测**（`af_catalog._load`，等长内容替换 + `os.utime` 恢复 mtime）：

```
初始 _load(): ['light.a']
mtime 同: True | size 同: True
改后文件实际含 lock.z: True
_load() 返回: ['light.a']     ← 缓存命中旧键，新实体完全不可见
```

**必须诚实标注的触发条件**（不是任何时候都成立）：

- 文件系统 mtime 粒度为 1 秒（NFS、部分容器存储层、ext3）——同一秒内的两次写入
- 文件以**保留 mtime** 的方式恢复（备份回滚、`rsync -t`、镜像层还原）
- 内容替换后 **size 恰好相同**（实体改名、属性改写非常常见）

沙箱是 ns 精度 mtime，因此用 `os.utime` 强制复现。**这不是构造出来的假场景，而是模拟上述两类真实环境。**

#### 加重因素：写侧不主动失效缓存（lesson 69）

`af_catalog._save()`（292 行）只做 `atomic_write_text`，**不清 `self._cache`**。

全文件仅两处 `self._cache = None`：

- `275 行`：`path.stat()` 失败时 —— 合法
- `315 行`：**在 `area_of()` 的 `return _meta_area(meta)` 之后 ⇒ 不可达的死代码**

```python
        return _meta_area(meta)
        self._cache = None          # ← 永远不会执行
```

**净效果**：写入后能否读到新内容，**完全依赖"mtime 一定会变"**。这是 lesson 69 的定义——read-your-own-write 没有保证，只是碰巧成立。

**为什么是 Medium 而非 High**：

- 触发需要 mtime 粒度粗或保留 mtime 的恢复（真实但非普遍）
- `atomic_write_text` 走 tmp + `os.replace`，正常路径下 mtime 必变 ⇒ 常规操作不受影响
- 后果是"读到旧目录"（陈旧），不是数据损坏；下次进程重启或 mtime 变化即恢复

**修复建议**（按成本递增）：

```python
# A) 最省事：写侧主动失效（治本）
def _save(self, payload):
    atomic_write_text(path, json.dumps(payload, ...))
    self._cache = None          # ← 加这一行

# B) 指纹加强：加 mtime_ns + 内容哈希
key = (stat.st_mtime_ns, stat.st_size)     # 至少用 ns 精度

# C) 最稳：指纹改为内容哈希（metadata 大时开销换正确性）
```

顺带：删掉 `af_catalog.py:315` 那行不可达代码。

---

## 四、本轮验证通过（确认无问题）

| 项目 | 方式 | 结论 |
|---|---|---|
| **缓存对象未被调用方就地污染** | AST 扫描全部 `d = self._load()` 后是否有 `d[...] = ` 写入 | ✅ **0 处**——缓存 dict 未被外部修改，无别名污染风险 |
| **`Config.update_credentials` 写后自愈** | 读实现 | ✅ 写盘后同步 `self._creds = creds` 并 bump revision，本进程立即一致 |
| **`get_config` 的 TTL 缓存设计** | 读实现 | ✅ 用 `time.monotonic()` 计龄（不受系统时钟步进影响），`0` 表示不过期，注释说明 R-54 由来 |
| **`af_bus` 的节流/去重/熔断** | 读实现 | ✅ 全部走注入的 `clock.monotonic()`（单调时钟），注释明确"保证测试确定性" |
| **时间戳统一 tz-aware** | grep | ✅ 全仓 `datetime.now(timezone.utc)`，无 `utcnow()` 裸用 |
| **`refresh()` 的锁** | 读实现 | ✅ `with self._lock:` 保护，无并发问题 |

---

## 五、已排除（rejected）

| 候选 | 理由 |
|---|---|
| **`_ENTITY_HEALTH_CACHE` 无上界** | key 是 catalog_path，一个进程通常只有 1–2 个 store；且已有 `_ENTITY_HEALTH_LOCK`。不构成内存泄漏 |
| **`load_entity_health` 指纹弱** | 与 R19-02 同源，合并为一条（3 处）计 |
| **系统时钟回拨影响授权码锁定** | `time.time()` 回拨会让限流窗口**延长**（fail-closed 方向），不放行。非缺陷；且窗口本就是"登记在册的边界"（源码已声明） |
| **`af_pending` 用 `st_mtime` 排序** | 仅用于列出顺序，非缓存判据，不影响正确性 |
| **`Config` 单例跨 root 共享** | key 是 `str(Path(root))`，隔离正确 |

---

## 六、修复优先级

| 顺序 | 缺陷 | 工作量 | 说明 |
|---|---|---|---|
| **P1** | R19-01 `refresh()` 读失败保留旧值 | 约 6 行 | 有效凭据在运行中静默消失，TTL 必然触发 |
| **P2** | R19-02 `_save()` 清缓存 + 指纹改 `mtime_ns` | 约 3 行 | 顺带删掉 315 行死代码 |

---

## 七、十九轮审计的横向观察

| 轮次 | 主题 | 确认缺陷 |
|---|---|---|
| 一~六 | 运行时代码 | 22 |
| 七~十八 | 生命周期/门禁/测试/配置/持久化/工具/分诊/模板/跨进程/闸/失败方向/契约/入口对等 | 23 |
| 十九 | 新鲜度契约 | 2 |

### RMW-on-silent-empty：第五次

| # | 轮次 | 形态 | 被抹掉的东西 |
|---|---|---|---|
| 1–2 | 十 | `af_store._read_tags` → `set_tags` / `_delete_archive` | 标签（落盘） |
| 3 | 十六 | `af_catalog._load_aliases` → `set_alias` | 别名（落盘） |
| 4 | 十六 | `af_catalog._load` → `refresh(收窄)` | 设备目录（落盘） |
| **5** | **十九** | **`Config._load_credentials` → `refresh()`** | **有效凭据（内存）** |

前四次是"读空 → 改 → 写回"，第五次是**"读空 → 直接覆盖内存"**。形状一样，只是载体从磁盘换成了内存——而且第五次更隐蔽，因为它**不留文件痕迹**，排查时磁盘上的文件看起来是"坏了"，而内存里的凭据是"自己没的"。

**同一条 lesson 在四个不同载体上重复兑现**，这已经是这个项目最稳定的缺陷生成模式。

### "新鲜度"这一轮的额外收获：死代码

`af_catalog.py:315` 的 `self._cache = None` 位于 `return` 之后，**永远不执行**。它很可能是当初设计"写完清缓存"时留下的意图残骸——**有人想过这个问题，但代码放错了位置**。

这与前几轮反复出现的模式完全一致：**认知到位、实现落错地方**。

### 给工程团队的一句话

R19-01 的修法只有一句话：**"重载读失败时保留旧值，而不是用空值覆盖。"**

但这句话已经在你们的代码里以别的形态出现过——`af_catalog.refresh` 的 `_preserve_known` 注释写着"★绝不把已知值清零：注册表缺项时**沿用旧值**，绝不用空串覆盖"。**同样的原则，在目录刷新里做到了，在凭据刷新里没有。**

建议把它提升为公共约定并写进第 17 个门禁：`check_reload_semantics.py`——断言所有 `refresh()` / `reload()` / `_load*()` 命名的重载函数，**不得把"加载失败返回的空值"直接赋给实例状态**（要么判空后保留旧值，要么先记日志再抛）。这样第六次就不会出现。

> 顺带一提，门禁清单现在已有 15 个；本轮与前几轮合计建议新增 3 个（`check_archive_gate`、`check_cli_writes`、`check_reload_semantics`）。这三个分别封住"闸唯一性""入口对等性""重载语义"三个类别——**它们正是十五、十八、十九轮各自的根因。**

---

## 八、已沉淀的复用资产

| 资产 | 位置 |
|---|---|
| 入口对等性 / 汇聚点比对扫描器 | `audit-env/scripts/scan_round18.py` |
| 契约声明提取器（七类 / 557 条） | `audit-env/scripts/scan_round17.py` |
| 失败方向审计扫描器（D1/D2/D3） | `audit-env/scripts/scan_round16.py` |
| 反向可达性 + 闸绕过分析器 | `audit-env/scripts/scan_round15.py` |
| 调用图展开 + 污点传播 | `audit-env/scripts/scan_round14.py` |
| 同形状传播扫描器（P1–P4） | `audit-env/scripts/scan_round13.py` |
| 候选自动分诊器（T1/T2/T3） | `audit-env/scripts/triage_round12.py` |
| 误报修正手册（66 条 + 本轮 4 条 = **70 条**） | `audit-env/scripts/lessons-round2.md` |

### 本轮新增 lessons（67–70）

- **67** RMW 第五变体：**`refresh()` 用可能为空的加载器覆盖内存好状态**；判据是"加载失败时它把内存里的好状态换成什么"
- **68** `(mtime, size)` **不是内容的充分指纹**；缓存键必须能唯一确定内容
- **69** **写侧不主动失效缓存** = 把正确性外包给"mtime 一定会变"；顺带发现 `return` 后的不可达死代码
- **70** 新鲜度缺陷用**"等长替换 + `os.utime` 恢复 mtime"** 实测复现，不用推理

---

## 附：环境说明

| 项 | 值 |
|---|---|
| 沙箱 Python | 3.10.12（项目声明 `>=3.11`） |
| 本轮实测 | 2 组（凭据 TTL 重载清空 / 等长替换 + os.utime 缓存陈旧）+ AST 扫描（缓存别名污染 0 处） |
| 沙箱 FS | mtime 为 ns 精度 ⇒ R19-02 需 `os.utime` 强制复现，报告已如实标注触发条件 |
| 仓库状态 | 探针与变异均已还原 |
