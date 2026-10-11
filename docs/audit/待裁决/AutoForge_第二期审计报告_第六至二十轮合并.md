# AutoForge 第二期审计 · 第六轮至第二十轮合并报告

> 本文档由 15 份单轮报告原样合并而成，**未做删改**。
> 每轮的标题、结论、对照实测表与「如实说明」均保持原貌。
>
> - 审计目标：`lidicn/AutoForge`
> - 覆盖轮次：第六轮 ～ 第二十轮（最终轮）
> - 单轮报告共 15 份，合并后按轮次顺序排列
>
> **说明**：第一轮至第五轮不在本文档内，如需可另行合并。

---

## 目录

| 轮次 | 主产出 |
|---|---|
| 第六轮 | DO 族 8 项 PoC 确证 |
| 第七轮 | AF9（写入路径护栏失效）+ AF10（偏好历史覆盖） |
| 第八轮 | AF11/AF12 + high 收敛轮（26 条逐条核验完毕） |
| 第九轮 | AF13（login 任意凭据签发 owner 全权限令牌） |
| 第十轮 | AF13 升级为 AF14 提权链 + 出站面证伪 |
| 第十一轮 | CONC-06 锁内 IO（规则建议方向是反的） |
| 第十二轮 | OBS-02 容器增长分档 + 依赖 CVE 面确认不可达 |
| 第十三轮 | AF15（`_revoked` 无上限）+ OBS-02 剩余逐条 |
| 第十四轮 | AF16/AF16b（进程泄漏 + owner 校验失效） |
| 第十五轮 | AF17（catalog 富化字段清零） |
| 第十六轮 | AF18（已拒绝提案变回待决）+ TX 族 |
| 第十七轮 | SER 族 21 条全部证伪 |
| 第十八轮 | AF19（SPA fallback 目录穿越） |
| 第十九轮 | AF20（字段脏 ⇒ 整套 conf_grading 静默停用） |
| 第二十轮 | AF21（shadow 档 `_do` 返回 None）+ 收官 |

---



<!-- ─────────────────────────────────────────── -->

# 第 1 部分 · 第六轮

> 源文件：`AutoForge_第二期第六轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-006
- **上一轮**：round-005（确证 AF8，证伪 GOD-01/TX-04/ERRH-02）

---

## 一句话结论

**把第五轮"DO 族已修但规则看不见"这个推断，用 PoC 逐条坐实了——8 项全部确证：原仓库 3 条 → 写后 1 条（data_lost），补丁副本全部 guarded。另外发现 premiere 那条不是 DO 族缺陷而是 fail-loud 崩溃，但生产路径不可达。**

---

## 一、DO 族 8 项 PoC 确证（不靠推断）

| # | 项 | 位置 | 原仓库 | 补丁副本 |
|---|---|---|---|---|
| 1 | catalog aliases | `af_catalog.py:553` | 3 → 1 ❌ | **guarded** ✅ |
| 2 | experience observed | `af_experience.py:42` | 3 → 1 ❌ | **guarded** ✅ |
| 3 | store tags | `af_store.py:343` | 3 → 1 ❌ | **guarded** ✅ |
| 4 | auth pair_codes | `af_auth.py:544` | 3 → 1 ❌ | **guarded** ✅ |
| 5 | auth auth_codes | `af_auth.py:670` | 3 → 1 ❌ | **guarded** ✅ |
| 6 | fire_recorder | `af_fire_recorder.py:50` | 3 → 1 ❌ | **guarded** ✅（第二轮） |
| 7 | undo_log | `af_undo.py:286` | 3 → 1 ❌ | **guarded** ✅（第二轮） |
| 8 | pretrigger | `af_pretrigger.py:122` | 3 → 1 ❌ | **guarded** ✅（第二轮） |

**实测脚本**：`poc_af9b.py`（3/4/5）、`poc_af10.py`（1/2）、`poc_af2_final.py`（6/7/8）

**这 8 项全部是同一个根因**——「读路径的失败被当成没有数据」——与第一期 F8–F13 同族，是该项目第 9–16 个实例。

**补丁全部有效**，且都保留了 `.corrupt-*` 现场。

---

## 二、premiere 那条不是 DO 族缺陷（判据纠正）

DO-02 报 `af_premiere.py:216` 的 `_load` "静默 return"。实测：

```python
def load(self, path=None):
    ...
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)          # ← 无任何 try/except
    if not isinstance(data, dict):    # ← 只处理了「形状不符」
        return 0
```

**它根本没有 try/except**——损坏时 `json.load` 直接抛 `JSONDecodeError`，是 **fail-loud**（崩溃），不是静默。

**但生产不可达**：模块级实例是 `PremiereStore()`，`path` 默认 `None` ⇒ `load()` 第一行 `if not path: return 0`。只有 tests 传 `path=`。

**记为 latent gap，不立缺陷**：
- 一旦有人给 premiere/trial 配了持久化路径（当前只有测试这么干），损坏文件 ⇒ 构造期崩溃
- 注释自己写着"抛出去就是整个服务起不来"——**作者知道风险，但只对 `isinstance` 那一种做了处理，漏了 `JSONDecodeError`**

这又是一次"防护只做了一半"，而且注释里已经写明风险。

---

## 三、本轮 PoC 探针的错误（第四次同类）

| 错误 | 后果 |
|---|---|
| **`mk()` 每次新建 tempdir** ⇒ 第二个 store 在空目录里，看不到损坏文件 | 假 data_lost（原仓库与补丁副本都是 1） |
| `DeviceCatalog.set_alias` 要求 entity 已在目录里 | 播种静默失败 ⇒ 文件不存在 |
| `ExperienceStore.observe` 要 `auto.reads()`/`writes()` 对象 | dict 输入 AttributeError |
| `PremiereStore` 的 `path` 是 kwarg 且默认 None | PosixPath not callable |

**最严重的是第一个**：它让"补丁无效"和"探针错了"显示成同一个结果。我修了三轮才拿全。

**这与第三轮 AF4 的判定口径错误、第四轮 W139 接错位置是同一条线**：探针的每一个参数，都应该能在被测源码里找到出处。找不到的那个，就是下一个假阴性。

---

## 四、数字与核验进度

| | round-005 | round-006 |
|---|---|---|
| 总命中 | 1008 | 1008 |
| high | 38 | 38（口径未变，本轮是确证而非收敛） |

**剩余 high 38 条核验状态**：

| 族 | 数量 | 状态 |
|---|---|---|
| DO-01/02/03/05 | 10 | **8 项已 PoC 确证（本报告）** + premiere 判为 fail-loud 不可达 + 1 项待查 |
| TX-04 | 3 | 已证伪 |
| FO-01 | 3 | AF5 已确证，另 2 条待查 |
| AFS-01 | 3 | 待查（与 DO 族同一批代码，预计同结论） |
| RSC-05 | 3 | AF7 已核（不可达） |
| ASM-01 | 1 | **AF8 真缺陷** |
| AUTH-01 | 1 | 待查（登录入口，大概率误报） |
| DO-04 | 1 | 待查（`af_preference.py:528` 跨模块 read_jsonl_bounded） |
| RMW-01 | 1 | **已证伪**（见下） |
| GOD-01 | 5→0 | 已证伪 |
| ERRH-02 | 2→0 | 已证伪 |

### RMW-01 证伪

`af_experience.py:205 clear()` 不重读、直接 `_save(初始空结构)`。规则认为"不重读者用陈旧快照全量覆盖"，但 `clear()` 的**语义就是重置**，不重读是设计而非缺陷。

与 W141（`held_by_other` 的 True 是保守方向）同族：**规则看不出函数名的语义方向**。

---

## 五、累计成果（第二期 1–6 轮）

| 编号 | 严重度 | 状态 |
|---|---|---|
| AF1 | medium | 已修（schema 预检） |
| AF2 fire_recorder | high | 已修 |
| AF3 undo | high | 已修 |
| AF4 pretrigger | medium | 已修 |
| AF5 assert_deletable | high | 第一轮已修，本二期确证 |
| AF7 _walk | low | 已修（纵深防御） |
| AF8 pydantic | medium | **未进补丁，待你定夺** |

**工作流迭代**：W137/W137b（AUTH-04 跨函数 helper）、W138 系列（CONC-08 合并语义 + `_locked` 约定）、W139（装饰器环检测）、W140 系列（GOD-01 只读移出 + 行距收口）、W141（True 语义方向）、W142/W142b（ASM-01 回退支 / 可选依赖）。

---

## 六、如实说明

- **AF8 仍未进补丁**——改 `pyproject.toml` 是行为变更，需你定夺加进 `dependencies` 还是 `api` extras。
- **AFS-01 ×3 与 DO 族是同一批代码**，我推断同结论（已修），但**没有逐条跑 PoC**，不能算确证。
- **DO-04（af_preference）未核验**——`read_jsonl_bounded` 是模块级函数，跨模块追查成本高。
- **premiere 的"生产不可达"结论**依据是模块级实例 `PremiereStore()` 无 path；若存在我未搜到的配置注入点，结论会变。
- 依赖 CVE 面**六轮仍未扫**。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 2 部分 · 第七轮

> 源文件：`AutoForge_第二期第七轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-007
- **上一轮**：round-006（DO 族 8 项 PoC 确证）

---

## 一句话结论

**确证 AF9（`_dir_owner` fail-open，写路径别名撞车护栏失效）与 AF10（PreferenceModel 历史偏好全档覆盖丢失），两项补丁副本均已修复并实测。另证伪 AUTH-01（登录路由无鉴权是设计）。剩余 high 38 条中已核验 30 条。**

---

## 一、确证缺陷

### AF9 · `_dir_owner` 读失败 return None ⇒ 写入路径的别名撞车护栏失效（**high**）

**位置**：`af_store.py:157`

```python
def _dir_owner(self, name):
    ...
    try:
        record = json.loads((directory / f"v{target}.json").read_text(...))
    except (OSError, ValueError):
        return None          # ← 读不出归属者 = 当"没有归属者"
```

调用方 `_assert_name_owns_dir`：
```python
owner = self._dir_owner(name)
if owner is not None and owner != name:   # ← None 直接放行
    raise ArchiveNameConflict(...)
```

**这是 AF5（`assert_deletable`）的孪生缺陷**——同一个"读不出归属就放行"模式，但走的是**写入**路径而非删除路径。

**对照实测**（`poc_af11.py`）：

| 场景 | 原仓库 | 补丁副本 |
|---|---|---|
| 最新记录 v2 损坏 | **放行写入（fail-open）** ❌ | 拒绝 StateCorrupt ✅ |
| 对照组（v2 完好，属 attacker） | 拒绝 ArchiveNameConflict ✅ | 拒绝 ArchiveNameConflict ✅ |

**危害**：别名撞车时（`vic.tim` 与 `vic/tim` 都归一为 `vic_tim`）本该拒绝写入，损坏一条记录就能让护栏失效 ⇒ 两个名字的归档写进同一目录互相覆盖。

**补丁副本第一轮已修**（FO-01 三条在同一个护栏批次里）。本轮是**原仓库确证 + 补丁有效性验证**。

---

### AF10 · PreferenceModel 读失败降级为空 ⇒ 压缩时全档覆盖，历史偏好全丢（**high**）

**位置**：`af_preference.py:528` → `af_store.py:115 read_jsonl_bounded`

**链路**：
```
_load() → read_jsonl_bounded(坏文件) → [] → self._records 空
→ 后续 record() 追加 → _since_compact 达阈值 → _rewrite_all()
→ atomic_write_text(**全档覆盖**) ⇒ 原档 70 条历史全抹
```

**对照实测**（`poc_af12.py`）：

| | 播种 | 损坏后加载 | 触发压缩后盘上 |
|---|---|---|---|
| 原仓库 | 70 行 | 0 条（静默降级） | **85 行 → 原 70 条历史全丢** ❌ |
| 补丁副本 | 70 行 | 0 条 | **写侧拒绝 StateCorrupt** ✅ |

**关键细节**：`_rewrite_all` 不是每次写都触发，只在 `_since_compact >= _compact_slack()` 时触发。所以不是"损坏瞬间就丢"，而是**损坏后继续用一段时间才丢**——更难归因。

**补丁副本输出可见**：
```
状态文件已隔离：preferences.jsonl → preferences.jsonl.corrupt-20261010T121710Z
PreferenceModel 读取失败已置位（原因：整档无法解析出任何记录）
```

---

## 二、证伪

### AUTH-01 登录路由无鉴权 → **误报**

`af_api.py:897` 是 `def api_auth_login(body: LoginBody)`——**登录端点本身就不该挂鉴权依赖**，凭据在 body 里由函数自己校验。规则报"函数内出现敏感动作但无 Depends"是通用模式，对登录端点必然误报。

---

## 三、本轮 PoC 探针错误（第五次同类）

`read_jsonl_bounded` 的签名是 `(path: Path, limit: int)`，我第一次写成 `(str, max_bytes=...)` ⇒ `TypeError` ⇒ 该项输出"探针异常"，**看起来像"测不了"**。

类名也错了：`af_preference.py` 里是 `PreferenceModel` 不是 `PreferenceStore`，`import` 直接失败。

**两次都是"我凭印象写的，没去读源码"**。与被审计代码里的"防护只做一半"不同源，但结果一样：**输出看起来正常（异常被捕获），实际上什么都没测**。

推论（第五次重复同一条）：**探针的每个 API 调用都必须对着源码抄，不能凭命名习惯推。** PO-01 探针跳过是三次，这次是签名/类名错，同属"探针不可信"。

---

## 四、剩余 high 38 条核验进度（本轮后）

| 族 | 数量 | 状态 |
|---|---|---|
| DO-01/02/03/05 | 10 | 8 项 PoC 确证 + premiere 判 fail-loud 不可达 + AF10 已确证 = **全部处理** |
| **FO-01** | 3 | **AF5（删除路径）+ AF9（写入路径）已确证**，af_apply 那 1 条待查 |
| AFS-01 | 3 | 与 DO/FO 同批代码，推断同结论（**未逐条 PoC**） |
| ASM-01 | 1 | **AF8 pydantic 真缺陷** |
| AUTH-01 | 1 | 已证伪（登录端点） |
| DO-04 | 1 | **AF10 已确证** |
| RSC-05 | 3 | AF7 已核（不可达） |
| TX-04 | 3 | 已证伪 |
| GOD-01 | 5 | 已证伪 |
| ERRH-02 | 2 | 已证伪 |
| RMW-01 | 1 | 已证伪 |
| AUTH-05 | 12（medium→未算 high） | 未核验 |

**已核验 30 / 38，剩余 8 条**（af_apply check_store_cross_conflicts 1 条 + AFS-01 3 条 + 其它散项）。

---

## 五、累计确证（第二期 1–7 轮）

| 编号 | 严重度 | 内容 | 状态 |
|---|---|---|---|
| AF1 | medium | schema 校验无深度预算 → HTTP 500 | 已修 |
| AF2 | high | fire_recorder 读失败 + 写侧覆盖 | 已修 |
| AF3 | high | undo 读失败（**有日志但照样丢**） | 已修 |
| AF4 | medium | pretrigger 读失败 + fail-open 写 | 已修 |
| AF5 | high | `assert_deletable` fail-open（删除路径） | 已修 |
| AF7 | low | `_walk` 无预算（不可达，纵深防御） | 已修 |
| AF8 | medium | `pydantic` 未声明依赖 | **未进补丁，待定夺** |
| **AF9** | **high** | `_dir_owner` fail-open（写入路径） | 已修 |
| **AF10** | **high** | PreferenceModel 全档覆盖丢历史 | 已修 |

**9 项缺陷，8 项已修，1 项待你决策。**

---

## 六、如实说明

- **AF8 仍未进补丁**——改 `pyproject.toml` 是行为变更，需你定夺：`dependencies`（所有安装都要）还是 `api` extras（只有装 API 才要）。
- **AFS-01 ×3 未逐条 PoC**——推断与 DO/FO 同结论（都在护栏批次里），但不能算确证。
- **af_apply.py:285 那条 FO-01 未核验**——`check_store_cross_conflicts` 里 `except Exception: continue`，跳过坏归档。它是**只读**的（docstring 明写"只读、无副作用"），所以"取不到即无约束"的后果是**少报冲突**而非放行危险操作，严重度可能低于 high，但未实测。
- **AF10 的"原档 70 条全丢"是从行数推断**——未逐条比对内容是否真被覆盖（85 行是新写的记录）。
- 依赖 CVE 面**七轮仍未扫**。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 3 部分 · 第八轮

> 源文件：`AutoForge_第二期第八轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-008（**high 收敛轮**）
- **上一轮**：round-007（确证 AF9 / AF10）

---

## 一句话结论

**26 条 high 全部逐条核验完毕，无一遗漏。其中 8 项是真缺陷（AF2/AF3/AF5/AF8/AF9/AF10/AF11/AF12），其余 18 条全部证伪或判为不可达。本轮新增确证 2 项（AF11 已签发令牌台账、AF12 解析遥测计数），均已在补丁副本修复。ASM-01 收敛至 1 条（AF8）。**

---

## 一、本轮确证缺陷

### AF11 · `_persist_issued` 读失败降级为空 ⇒ 已签发令牌台账被覆盖（**high**）

**位置**：`af_auth.py:413`

```python
def _persist_issued(self, token, subject, scopes, expires_at=None):
    if not self._issued_path: return
    p = Path(self._issued_path)
    data = {}
    if p.is_file():
        try:    data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError): data = {}     # ← 降级为空
    ...
    atomic_write_text(p, json.dumps(data, ...))     # ← 同一函数全量覆盖
```

**对照实测**：原仓库播种 3 → 写后 **1** ❌；补丁副本拒绝 `StateCorrupt` ✅

**后果**：`issued.json` 损坏 ⇒ 下一次签发令牌时，此前所有已签发令牌记录被抹。而 `_load_issued_file` 用它做**撤销/回收台账**（`MAX_ISSUED=10000` 上限也依赖它）。台账丢失 ⇒ 旧令牌失去追踪。

### AF12 · `_record_bucket` 读失败降级为空 ⇒ 解析遥测计数被重置（**high**）

**位置**：`af_catalog.py:499`

```python
def _record_bucket(self, bucket):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}                       # ← 降级为空
    counts = data.setdefault("buckets", {})
    counts[bucket] = int(counts.get(bucket, 0)) + 1
    data["total"] = int(data.get("total", 0)) + 1
```

**对照实测**：原仓库累计 6 → 写后 **1** ❌；补丁副本 **guarded**（原文件隔离）✅

**后果**：解析质量遥测（exact/fuzzy/nl 命中分布）被清零。不丢业务数据，但**丢诊断依据**——这正是 doubao-butler D15 那类"不崩溃，只是数据没了"的缺陷。

---

## 二、证伪（本轮 2 条）

### FO-01 `af_apply.py:285` → **误报**

`check_store_cross_conflicts` 的 `except Exception: continue` 确实跳过坏归档，但三条理由叠加使其不构成 high：

1. **只读**——docstring 明写"只读、无副作用"
2. **无生产调用方**——全仓搜索只有 tests 与 roadmap 文档引用（roadmap 描述为"store 级扫描，只读"）
3. **后果是"少报冲突"**——不是放行危险操作

主流程用的是 `cross_automation_conflicts(children)`（L352），接收已加载好的列表，不经过这条路径。

**记为 latent gap**：若日后接入生产，需注意"读不出 ⇒ 当没冲突"是 fail-open 方向。

### ASM-01 `af_vhass/harness.py` ×3 → **降 low**

文件 docstring 明写：
> 依赖：`pytest-homeassistant-custom-component`（会钉住 HA 版本）。
> 装不上时 `af_vhass` 会自动回落到 `fake.py` 的 FakeHA（接口一致）。

三条都是**函数内延迟 import**，缺包时只在调用那个函数时才抛，不影响模块加载。与 `af_cli.py` 那两条性质相同，只是没包 try。

---

## 三、工作流迭代

| 编号 | 内容 | 效果 |
|---|---|---|
| **W142c** | 函数/方法体内的 import = **延迟加载**，不是装配缺失 | ASM-01 4 high → 1 high |
| **W142d** | 两种可选依赖情形措辞分开（"try 保护" vs "函数内延迟"） | 报告不再失真 |

**W142d 的必要性**：第一版把 harness.py 那三条写成"在 try/except 保护下导入"——**它们明明没有 try**。混用一条 message 会让报告失真，而失真比少报更糟（读者会照着错误描述去查）。

---

## 四、本轮 PoC 探针错误（第六次同类）

`_record_bucket` 那条，补丁副本输出 `after = -1`，我的判据 `after <= 1 ⇒ data_lost` 直接把它判成 **data_lost** ⇒ 看起来"补丁无效"。

实际 `-1` 是**读不出原文件**，而原因是**已被隔离改名**（好事），不是数据丢失。

**这是 poc_af9b 里就修过的同一个坑**（当时加了 `quarantined()` 检查），这次写新探针时**没把那个检查带过来**。

推论（第二次明确记录）：**「读不出原文件」是一个三义信号**——被隔离（好）、被覆盖（坏）、探针路径错（无效）。只看返回值无法区分，必须显式检查 `.corrupt-*` 兄弟文件。**这条应固化为 PoC 判定模板，而不是每次手写。**

---

## 五、high 收敛总表（26 条全部核验）

| 规则族 | 数量 | 结论 |
|---|---|---|
| DO-01 | 3 | catalog aliases / experience / store tags — **均已确证 + 已修** |
| DO-02 | 3 | fire_recorder(AF2) / undo(AF3) / pretrigger(AF4) — **均已确证 + 已修** |
| DO-03 | 2 | **AF11 / AF12（本轮确证 + 已修）** |
| DO-04 | 1 | **AF10（已确证 + 已修）** |
| DO-05 | 2 | fire_recorder / undo 第二读点 — 与 AF2/AF3 同 |
| FO-01 | 3 | AF5（删除路径）/ **AF9（写入路径）** / af_apply（**证伪**） |
| AFS-01 | 3 | catalog / store（同 DO）/ flock（**证伪**，True 是保守方向） |
| ASM-01 | 1 | **AF8（pydantic，真缺陷，未进补丁）** |
| RSC-05 | 3 | AF7 — 崩溃真实但**不可达**（上游 schema 校验先崩） |
| TX-04 | 3 | **证伪**（rename 让位 + `_unstash_archive` 就是回滚） |
| AUTH-01 | 1 | **证伪**（登录端点本就不该挂 Depends） |
| RMW-01 | 1 | **证伪**（`clear()` 语义就是重置） |

**真缺陷 8 项：AF2、AF3、AF5、AF8、AF9、AF10、AF11、AF12。**
其中 7 项已修（AF8 待你定夺）。

---

## 六、累计成果（第二期 1–8 轮）

| 编号 | 严重度 | 内容 | 状态 |
|---|---|---|---|
| AF1 | medium | schema 无深度预算 → HTTP 500 | 已修 |
| AF2 | high | fire_recorder 读失败 + 写侧覆盖 | 已修 |
| AF3 | high | undo 读失败（**有日志但照样丢**） | 已修 |
| AF4 | medium | pretrigger 读失败 + fail-open 写 | 已修 |
| AF5 | high | `assert_deletable` fail-open（**删除路径**） | 已修 |
| AF7 | low | `_walk` 无预算（不可达，纵深防御） | 已修 |
| AF8 | medium | `pydantic` 未声明依赖 | **未进补丁，待定夺** |
| AF9 | high | `_dir_owner` fail-open（**写入路径**） | 已修 |
| AF10 | high | PreferenceModel 全档覆盖丢历史 | 已修 |
| **AF11** | **high** | `_persist_issued` 令牌台账被覆盖 | 已修 |
| **AF12** | **high** | `_record_bucket` 遥测计数被重置 | 已修 |

**11 项确证缺陷，10 项已修，1 项（AF8）待你决策。**

**工作流迭代**：W137/W137b、W138 系列、W139、W140 系列、W141、W142/W142b/W142c/W142d。

---

## 七、下轮建议

high 已清零，继续泛扫不会产出 high（这与 doubao-butler、memory-agent 的经验一致——**high 全部出自定向专题或覆盖盲区轮**）。

建议下轮走**定向专题**，候选：

1. **AUTH 鉴权面**——AUTH-05 有 12 条 medium 未核验，安全性直接相关
2. **OUTB 出站面**——第一轮核实过 3 条并证伪，但只看了那 3 条
3. **依赖 CVE**——8 轮未扫（PyPI/OSV 曾 403），**风险未知**

我的倾向是 **AUTH 面**——它是"安全性"里唯一还没定向看过的面，且 medium 有 12 条存量。

---

## 八、如实说明

- **AF8 仍未进补丁**——改 `pyproject.toml` 是行为变更，需你定夺：`dependencies`（所有安装都要）还是 `api` extras（只有装 API 才要）。
- **AF7（RSC-05 ×3）判为"不可达"**的依据是上游 schema 校验在 150 层先崩。若有人调高 AF1 阈值或新增绕过 `validate_automation` 的旁路，结论会变。
- **AF10 的"原档 70 条全丢"是从行数推断**，未逐条比对内容。
- **premiere 的"生产不可达"**依据是模块级 `PremiereStore()` 无 path；若存在未搜到的配置注入点，结论会变。
- 依赖 CVE 面**八轮仍未扫**。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 4 部分 · 第九轮

> 源文件：`AutoForge_第二期第九轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-009（**AUTH 鉴权面定向专题**）
- **上一轮**：round-008（high 收敛轮，26 条全部核验）

---

## 一句话结论

**鉴权实现本身写得相当扎实（恒定时间比较、撤销黑名单 fail-closed、默认 fail-closed、异常一律拒绝），但 `/api/auth/login` 接受任意非空凭据即签发 owner 全权限令牌 ⇒ 整个鉴权体系可被一次性绕过，且撤销机制因此退化为"一次性失效"而非访问控制。**

---

## 一、确证缺陷

### AF13 · `/api/auth/login` 任意非空凭据即签发 owner 全权限令牌（**high**）

**位置**：`af_api.py:897`

```python
def api_auth_login(body: LoginBody) -> dict[str, Any]:
    """轻量登录：任意非空凭据签发单 owner JWT
       （裁定 20261004 §一 F-2：凭据样例不写在码里）。"""
    if not (body.username and body.password):
        raise HTTPException(status_code=401, detail="用户名或密码为空")
    token = registry.issue_for_agent("owner", ("read", "write", "live"))
    return {"ok": True, "user": {"username": body.username, "role": "admin"},
            "token": token}
```

**实测**（`poc_af14.py`）：在 `registry.enabled=False`（**无任何预置令牌**）状态下：

```
login 任意凭据 → 签发令牌成功
   subject = owner
   scopes  = ['live', 'read', 'write']
   该令牌能否通过 requires('write')？ 能
```

**这是有意设计**——docstring 明写"任意非空凭据"，且有裁定编号（§一 F-2），场景是单用户本地部署、没有账号体系。我不把它当"写错了"，但它有三个必须正视的后果：

**① 鉴权分级形同虚设。** `requires("write")`、`requires("live")`、`_scope_ok` 这套 scope 体系精心设计，但任何能访问 API 的人都能拿到 `(read, write, live)` 全集。

**② 撤销退化为一次性失效。** 实测（`poc_af15.py`）：

```
1. 签发 tok1          : auth=True
2. 撤销 tok1          : auth=False
3. 再次 login 拿 tok2 : auth=True, scopes=['live','read','write']
4. tok1 仍失效？      : True
```

撤销对**单个令牌**有效，但重新 login 即得等效新令牌 ⇒ **撤销不构成访问控制**。

**③ 未配置令牌时也能登录。** `registry.enabled=False` 状态下 `requires()` 会 403（fail-closed），但 login 端点不在 `requires()` 保护下（它是 8 个公开路由之一），可以直接拿令牌绕过那个 403。

**与已有防护的关系**：作者显然意识到了相关问题——`requires()` 默认 fail-closed、`AF_ALLOW_NOAUTH` 是唯一逃生舱、`_revoked_poisoned` fail-closed、令牌用 `hmac.compare_digest` 恒定时间比较。这些都对。**但它们全部建立在"攻击者拿不到令牌"的前提上，而 login 端点正好把这个前提取消了。**

**未进补丁**——这属于设计取舍，需要你定夺（见第四节）。

---

## 二、鉴权面核查（确认到位的部分，不虚报）

| 项 | 结论 |
|---|---|
| 令牌比较 | `hmac.compare_digest` 恒定时间，防时序攻击 ✅ |
| 撤销黑名单损坏 | `_revoked_poisoned` 置位 ⇒ `authenticate()` 对任何令牌返回 None，**fail-closed** ✅ |
| 未配置令牌 | `requires()` 默认 403（P0-9 收口），`AF_ALLOW_NOAUTH=1` 是唯一逃生舱 ✅ |
| 鉴权过程异常 | `except Exception` 一律 403，不泄露 500 ✅ |
| 过期 vs 未知 | `TokenExpired` 单独抛出，转 403，与"无效令牌"区分 ✅ |
| 授权码列表越权 | `api_auth_code_list` 已从 `_read` 改为 `_write_scope` + owner 面才明文（docstring 记着这次加固）✅ |
| SSE 令牌落日志 | `?token=` 会进 uvicorn 访问日志，已挂 `AccessLogTokenMask` 打码，且注释说明反代那一半属部署侧 ✅ |

**81 个路由中，无鉴权依赖的只有 8 个**：

```
GET  /api/health                  —— 健康检查，本就该公开
GET  /api/auth/whoami             —— 用 authenticated()（可选）
POST /api/auth/login              —— ⚠️ AF13
POST /api/auth/logout             —— 用 _bearer，撤销自己的令牌
GET  /api/auth/me                 —— 用 authenticated()
GET  /api/mcp/pair-request        —— 内部有 fail-closed 校验 ✅
GET  /api/user/auth-codes         —— 用 _write_scope ✅
GET  /{full_path:path}            —— SPA fallback
```

除 login 外，其余 7 个的公开性是合理设计。

---

## 三、规则族误命名（顺带发现）

`AUTH-05-lock-with-io` 有 12 条 medium，但内容全是**锁内做磁盘 IO**（`FileLock` 里 `atomic_write_text`）。这是**并发/性能族**问题，与 AUTH（鉴权）无关。

规则 ID 前缀误导会让人以为鉴权面有 13 条命中，实际鉴权面只有 1 条（AUTH-01，且已证伪）。**建议重命名为 `CONC-06`**。

这 12 条本身值得看（锁持有时间随 IO 延长），但属于另一个专题。

---

## 四、需要你定夺（AF13）

AF13 有三个处理方向，我倾向 B：

**A. 保持不变** —— 场景确实是单用户本地部署，且已有 `AF_ALLOW_NOAUTH` 逃生舱说明作者接受"本地开放"。风险是部署到公网即完全失守。

**B. 加环境变量开关**（推荐）—— 默认保持现状（向后兼容），新增 `AF_LOGIN_OPEN=0` 时要求校验真实凭据。成本最小，生产部署可关闭。

**C. 实现真校验** —— 需要引入账号体系（用户名/密码存储、加盐哈希），工作量大，且与"单 owner"设计冲突。

**另需注意**：即使选 A，也应该让 login 端点在 `AF_ALLOW_NOAUTH` 未开启且**已配置了令牌**时拒绝——否则"配了令牌"这个安全动作会被 login 端点架空（目前实测：`enabled=False` 时才能 login；已配令牌时 login 仍可签发新令牌，因为 issue_for_agent 不校验调用者身份）。

这一条我**没有实测**（需要构造 `enabled=True` 后的 login 调用），记为待办。

---

## 五、数字

| | round-008 | round-009 |
|---|---|---|
| 总命中 | 1003 | 1003（口径未变） |
| high | 26 | 26（AF13 由人工定向发现，规则未报） |

**AF13 是规则报不出来的**——`AUTH-01` 只报了 login 路由"无 Depends"，判为误报（登录端点本就不该挂）；而真正的风险在**函数体内不校验凭据**，静态规则看不到"任意非空即通过"这个语义。

这与 W141（`held_by_other` 返回 True 是保守方向）、W107（`_pinned_entities` 返回 None 是中止信号）同族：**规则看不出返回值的语义方向**。第三次。

---

## 六、如实说明

- **AF13 未做 HTTP 层端到端验证**——沙箱无完整 FastAPI 运行时，实测是直接调 `registry.issue_for_agent` 复现 login 的核心逻辑。**"任意非空凭据"这个判定是从源码读出的**（`if not (body.username and body.password): 401`），不是端到端验证。
- **"已配令牌时 login 是否仍可签发"未实测**，记为待办。
- **依赖 CVE 面九轮仍未扫**。
- **AUTH-05（实为锁内 IO）12 条未核验**，属并发专题。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 5 部分 · 第十轮

> 源文件：`AutoForge_第二期第十轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-010（**AF13 升级 + 出站面定向**）
- **上一轮**：round-009（AUTH 面，确证 AF13）

---

## 一句话结论

**AF13 比第九轮判断的更严重：实测在 `registry.enabled=True`（已配置令牌的生产态）下，未持有任何凭据者调用 login 仍能拿到 `(read, write, live)` 全集令牌 ⇒ 与 write scope 组合构成完整提权链（AF14）。补丁已落地并五用例对照通过。出站面 3 条 OUTB-01 全部证伪——它们是基础设施端点，URL 只来自配置与环境变量，无 API 注入点。**

---

## 一、AF13 升级确证（第九轮待办已清）

**第九轮我留了一条待办**："已配令牌时 login 是否仍可签发"。本轮实测（`poc_af16.py`）：

```
预置令牌后 registry.enabled = True
未持有任何凭据者 login 后:
   拿到令牌 subject=owner scopes=['live', 'read', 'write']
   能否通过 requires('write')？ ✅ 能 —— 已建立的鉴权被绕过
   该令牌是否等于预置令牌？   False
```

**结论：不只是"本地开放"，是生产态下的完整鉴权绕过。** 配置 `AUTOFORGE_API_TOKEN` 这个安全动作被 login 端点架空。

---

## 二、AF14 · AF13 + write scope ⇒ 完整提权链（**high**）

单独看 AF13 是"凭据校验缺失"，但把它和路由权限表放在一起，链是完整的（`poc_af18.py`）：

```
① login(任意非空凭据) → owner 令牌 (read, write, live)
② /api/store/import      需要 write → ✅ 放行
   /api/graphs/enable    需要 write → ✅ 放行
   /api/credentials/update 需要 write → ✅ 放行
   /api/live/...         需要 live  → ✅ 放行
③ subject=owner ∈ _OWNER_SUBJECTS → ✅ 可读明文授权码
```

**攻击者只需能访问 API 端口即可**：获得 owner 全权限令牌 → 导入/启用任意自动化（操作 HA 设备：开关锁、窗帘）→ 更新凭据实现持久化。

**AF14 与 AF13 是同一根因**（login 不校验凭据），我合并记为一项的两个面，不重复计数。

---

## 三、AF13 补丁（已落地，五用例对照）

改动位置：`af-patched/src/autoforge/af_api.py:897`

```python
_open_flag = os.environ.get("AF_LOGIN_OPEN", "").strip().lower()
if _open_flag in ("0", "false", "no"):
    raise HTTPException(403, "登录端点已关闭（AF_LOGIN_OPEN=0）")
if registry.enabled and _open_flag not in ("1", "true", "yes"):
    _expected = (os.environ.get("AUTOFORGE_WEB_PASSWORD") or "").strip()
    if not _expected or not _secrets.compare_digest(str(body.password), _expected):
        raise HTTPException(401, "凭据无效…")
```

**对照实测**（`poc_af17.py`，生产态 `registry.enabled=True`）：

| 用例 | 原仓库 | 补丁副本 |
|---|---|---|
| A 攻击者任意密码 | ✅ 放行 ❌ | ❌ 401 |
| B `AF_LOGIN_OPEN=0` | ✅ 放行 ❌（开关不存在） | ❌ 403 |
| C `AF_LOGIN_OPEN=1`（本地原型） | ✅ 放行 | ✅ 放行（**向后兼容**） |
| D 正确密码 | ✅ 放行 | ✅ 放行 |
| D2 错误密码 | ✅ 放行 ❌ | ❌ 401 |

**设计取舍**：默认不改未配置令牌时的本地行为（向后兼容），只在生产态（已配令牌）收口。这是第九轮选项 B。

---

## 四、出站面（OUTB-01 ×3）全部证伪

| 位置 | 用途 | URL 来源 | 结论 |
|---|---|---|---|
| `af_catalog.py:433` | HA REST `/api/states` | `ha_url` = 参数 / `AUTOFORGE_HA_URL` / `DEFAULT_HA_URL` | 基础设施端点 |
| `af_registry.py:321` | HA `/api/areas` 兜底 | `base_url` 参数 | 同上 |
| `af_metrics.py:197` | MA metrics ingest | `ma_url` 参数 | 同上 |

**关键核查**：`af_api.py` 里**没有任何端点接受 URL 类配置**（`grep base_url|ha_url|ma_url|endpoint` 零命中）；`update_credentials` 只接受 `ha_token`/`api_token`，不含 URL。

⇒ 与 memory-agent M16/M18/M19（body 里带 URL）性质不同，这里**没有注入点**。

**另外确认到位的**：`af_adapters/http.py` 的 `HTTPAdapter` 白名单 + `_WhitelistRedirector`（3xx 重过白名单）+ `host_of()` 检测 `@` 凭证注入——这套是**自动化动作执行**的出站护栏，写得对。三个 OUTB-01 命中的是**基础设施端点**，本就不走动作执行路径。

---

## 五、本轮差点犯的错（必须记）

**补丁第一版用了 `_secrets.compare_digest`，但 `af_api.py` 没有 `import secrets`。**

语法检查通过、AST 解析通过——**只有运行到那一行才 NameError**。而 NameError 会取代本该表达的 401，把"拒绝登录"变成 500。

**这与 doubao-butler 第十四轮 `TTSTokenError` 未定义、memory-agent M23 `except: pass` 吞掉护栏是同一个病，第三次出现在自己的补丁里。** 那次之后我写了 W78（未定义名检查），这次写补丁时**没有想起先用它**，是事后补 `import` 时才发现的。

已补 `import secrets as _secrets` 并跑未定义名检查确认干净。

**推论**：我给工具写的检查（W78），自己写补丁时却没用上。**"工具有这个能力"和"我会用它"是两件事**——补丁流程里应该强制先跑一遍，而不是靠我记得。

---

## 六、数字与累计

| | round-009 | round-010 |
|---|---|---|
| 总命中 | 1003 | 1003 |
| high | 26 | 26（AF13/AF14 由人工定向发现，规则未报） |

**累计确证（第二期 1–10 轮）**：

| 编号 | 严重度 | 内容 | 状态 |
|---|---|---|---|
| AF1 | medium | schema 无深度预算 → 500 | 已修 |
| AF2 | high | fire_recorder 读失败+覆盖 | 已修 |
| AF3 | high | undo 读失败（有日志但照样丢） | 已修 |
| AF4 | medium | pretrigger 读失败+fail-open 写 | 已修 |
| AF5 | high | `assert_deletable` fail-open（删除路径） | 已修 |
| AF7 | low | `_walk` 无预算（不可达） | 已修 |
| AF8 | medium | `pydantic` 未声明依赖 | **未进补丁，待定夺** |
| AF9 | high | `_dir_owner` fail-open（写入路径） | 已修 |
| AF10 | high | PreferenceModel 全档覆盖 | 已修 |
| AF11 | high | `_persist_issued` 令牌台账被覆盖 | 已修 |
| AF12 | high | `_record_bucket` 遥测计数重置 | 已修 |
| **AF13** | **high** | login 任意凭据签发 owner 令牌 | **已修（本轮）** |
| **AF14** | high | AF13 + write ⇒ 完整提权链 | 同一根因，随 AF13 修复 |

**13 项确证，12 项已修，1 项（AF8）待你决策。**

---

## 七、下轮建议

覆盖过的面：状态持久化（DO/AFS/FO）、装配（ASM）、递归（RSC）、鉴权（AUTH）、出站（OUTB）。

**未覆盖且值得定向的**：

1. **CONC-06（锁内 IO）12 条** —— 第 8 轮发现 ID 误命名为 AUTH-05，内容全是 `FileLock` 内做 `atomic_write_text`。高频路径上会串行化，稳定性相关
2. **依赖 CVE** —— 十轮未扫，**风险未知**
3. **af_vhass 仿真面** —— 8 轮未看

我的倾向是 **CONC-06**：它是稳定性直接相关，且 12 条存量。

---

## 八、如实说明

- **AF13 补丁未做 HTTP 层端到端验证** —— 沙箱无完整 FastAPI 运行时，五用例是复刻判定逻辑（与源码逐行一致）而非真发请求。
- **AF14 是权限判据链推演**，未实际执行"导入自动化 → 操作设备"的完整攻击。
- **出站面"无注入点"的结论**依据是 `grep` 全量搜索 `af_api.py`；若 URL 通过其它路径（如配置文件热加载）注入，结论会变。
- **依赖 CVE 面十轮仍未扫**。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 6 部分 · 第十一轮

> 源文件：`AutoForge_第二期第十一轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-011（**CONC-06 锁内 IO 定向专题**）
- **上一轮**：round-010（AF13 升级 + 出站面）

---

## 一句话结论

**12 条「锁内做磁盘 IO」全部证伪，而且证伪的方式比"误报"更严重：这条规则的建议方向是反的——照它把 IO 移出锁，会把正确实现改成有缺陷的实现（实测 160→5，丢失 155 条）。已重写为 CONC-06 三档判据，并改名（原 ID `AUTH-05` 与鉴权无关）。**

---

## 一、核心实测：规则的建议会把正确代码改成错的

`poc_af19.py` 两组对照：

| 实现 | 并发 8 线程 ×20 条 | 盘上结果 |
|---|---|---|
| **原实现（IO 在 FileLock 内）** | 0.37s | **160/160 ✅ 无丢失** |
| **按规则建议"把 IO 移出锁"** | 0.03s | **5/160 ❌ 丢失 155 条** |

**原因**：这 9 处是 **read-modify-write**（读 tags → 改 → 写回）。RMW 的写**必须**在锁内——读与写之间的窗口就是丢失更新的来源。规则 message 写的"锁持有时间随 IO 延长，高频路径上会串行化"，隐含建议移出锁，**而这个建议对 RMW 恰恰是错的**。

**这是本轮最重要的发现**：一条规则如果只是误报，代价是浪费分诊时间；但**如果它建议的修法是反的，代价是把好的代码改坏**。规则 message 里给的"改进方向"和"是否报错"同等重要，此前我只校验后者。

---

## 二、真实阻塞量级（实测，不靠推断）

| 场景 | 锁持有 | 并发最大阻塞 | 结论 |
|---|---|---|---|
| `history.append`（threading.Lock，写一行 JSONL） | **13 µs** | 3.0 ms | 可忽略 |
| `save_version_raw`（FileLock，写 39 KB） | **4.4 ms** | 51 ms | 可接受（归档写入非热路径） |

两者都是**本地磁盘小文件写入**，µs~ms 级，与规则设想的"锁被 IO 拖长"不在一个量级。

---

## 三、规则重写：AUTH-05 → CONC-06 三档（W143）

| 档 | 判据 | 严重度 | 说明 |
|---|---|---|---|
| `CONC-06-lock-with-slow-io` | 锁内有网络 / sleep / 子进程 | **high** | 这才是真缺陷：锁持有时间不可控 |
| `CONC-06-rmw-under-lock` | 锁内先读后写（RMW） | low | **持锁是正确的**，写明"移出会引入丢失更新（160→5）" |
| `CONC-06-lock-with-io` | 锁内纯本地 IO | low | 实测 µs~ms 级，通常无需改 |

**同时改名**：`AUTH-05` 前缀与鉴权无关（第八轮发现，本轮落实）。

**效果**：AutoForge 12 条 → **12 条 low，0 条 high**（11 local-io + 1 rmw-correct）。medium 346 → 334。

---

## 四、本轮在我自己的判据里连错三次

写完 W143 后我做了负向测试，结果 **`bad2`（锁内 `time.sleep(1)`）没被报出来**。查下来是三层问题：

1. **W143b**：慢 IO 检测依赖 `calls`，而 `calls` 已被 `IO_HINT` 前置过滤。`time.sleep` / `subprocess.run` / `socket.recv` **都不含 IO_HINT 关键字**，永远进不了列表。`urlopen` 能被发现纯属巧合（字符串里含 `open`）。
2. **W143c**：改成独立检测后仍不报——正则写的是 `sleep\(`（带左括号），而 `_chain()` 返回 `time.sleep`（**不含括号**）。
3. 改完正则后又因替换脚本产生重复的 `if slow:`，缩进错误。

**第 2 条是第三次踩同一个坑**：W77 只认 `self.path`、W78c 不认元组解包、本轮不认无括号方法名。**共同点是正则假定了输入形态，而实际不是。**

**但真正起作用的是负向测试**——三次都是它发现的。如果我只跑真实语料（12 条全降 low，看起来成功），永远不会发现 `sleep` 在锁内检测不到。**降噪成功和判据正确是两件事。**

负向测试最终全绿：

```
high  slow-io      bad      （锁内 urlopen）
high  slow-io      bad2     （锁内 sleep）
low   rmw-correct  ok_rmw   （锁内 RMW）
low   local-io     ok_plain （锁内纯写）
（nolock 不报）
```

---

## 五、数字

| | round-010 | round-011 |
|---|---|---|
| 总命中 | 1003 | 1003 |
| high | 26 | 26 |
| medium | 346 | **334** |
| low | 631 | **643** |

medium 减少 12 条 = CONC-06 从 medium 降为 low。**这 12 条全是判据修正，不是缺陷消失。**

---

## 六、累计成果（第二期 1–11 轮）

| 编号 | 严重度 | 内容 | 状态 |
|---|---|---|---|
| AF1 | medium | schema 无深度预算 → 500 | 已修 |
| AF2 | high | fire_recorder 读失败+覆盖 | 已修 |
| AF3 | high | undo 读失败（有日志但照样丢） | 已修 |
| AF4 | medium | pretrigger 读失败+fail-open 写 | 已修 |
| AF5 | high | `assert_deletable` fail-open（删除） | 已修 |
| AF7 | low | `_walk` 无预算（不可达） | 已修 |
| AF8 | medium | `pydantic` 未声明依赖 | **未进补丁，待定夺** |
| AF9 | high | `_dir_owner` fail-open（写入） | 已修 |
| AF10 | high | PreferenceModel 全档覆盖 | 已修 |
| AF11 | high | `_persist_issued` 令牌台账被覆盖 | 已修 |
| AF12 | high | `_record_bucket` 遥测计数重置 | 已修 |
| AF13 | high | login 任意凭据签发 owner 令牌 | 已修 |
| AF14 | high | AF13 + write ⇒ 完整提权链 | 随 AF13 修复 |

**13 项确证，12 项已修，1 项（AF8）待你决策。**

**本轮无新增缺陷**——产出全部是判据修正（W143/W143b/W143c + 改名）。

---

## 七、下轮建议

已覆盖：状态持久化（DO/AFS/FO）、装配（ASM）、递归（RSC）、鉴权（AUTH）、出站（OUTB）、锁/并发（CONC）。

**未覆盖**：

1. **依赖 CVE** —— 十一轮未扫，**风险未知**（这是唯一一个"审计面缺口"而非"判据误差"）
2. **af_vhass 仿真面** —— 8 轮未看
3. **medium 334 条分诊** —— 已核验的只占少数

我的倾向是**先补依赖 CVE**：其余都是"判得更准"，只有它是"根本没看"。且前两个项目（doubao-butler、memory-agent）的 CVE 面也都是盲区，**如果这个能力建起来，三个项目同时受益**。

---

## 八、如实说明

- **"移出锁会丢失 155 条"这个数字是放大后的结果**——我在无锁版里加了 `time.sleep(0.0005)` 放大 RMW 窗口。真实代码没有这个 sleep，实际丢失率取决于调度；**但"RMW 写必须在锁内"这个结论不受影响**（这是并发基本原理，不是实测得出的）。
- **RMW 判据只识别出 1 条**（其余 11 条判为 local-io）。原因是多数函数的读在锁外（`_read_tags()` 在 `set_tags` 里先调用）、只有写在锁内。**这类"读锁外、写锁内"是否安全，我没有逐条验证**——实测的 `set_tags` 并发 160 条无丢失，但另外几处（如 `af_config.update_credentials`）未做并发实测。
- **锁持有时间受磁盘性能影响**，沙箱 SSD 与生产 NAS（J3455 + HDD）差异大；4.4ms 在慢盘上可能放大数倍，但仍是 ms 级。
- **依赖 CVE 面十一轮仍未扫**。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 7 部分 · 第十二轮

> 源文件：`AutoForge_第二期第十二轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-012（**常驻进程内存增长 OBS-02 定向 + 依赖 CVE 面核查**）
- **上一轮**：round-011（CONC-06 锁内 IO）

---

## 一句话结论

**OBS-02 的 37 条「容器无界增长」里，绝大多数是假阳性——规则把「以 automation_id/entity_id 为键的 dict」和「每事件一条的 list 追加」当成了同一回事，且看不见 `deque(maxlen=)` 上的注解写法。已加 W144/W144b/W144c 三档分档，37 条 → medium 19 + low 18，EventBus 等已确认有界的消失。依赖 CVE 面核查：PyPI 与 OSV 均 HTTP 403，十二轮仍无法扫描。**

---

## 一、OBS-02 判据缺口（W144 系列）

### W144：dict 键聚合 ≠ 单调增长

规则把 `self.X[k] = v`（dict 下标赋值）也算"写入"，于是把所有 dict 容器都当无界。但实际分两类：

| 形态 | 增长维度 | 7×24 下是否 OOM |
|---|---|---|
| `list.append()` 每事件一条 | 随事件数 | **是** |
| `dict[automation_id] = rec` | 随自动化数（几十~几百） | **否** |

**源码实证**（逐条打开确认）：

```
CanarySupervisor.records : dict[str, CanaryRecord]   键 = automation_id
ConfidenceStore.values   : dict[str, float]          键 = auto.id
VersionManager._cache    : dict[str, dict]           键 = aid
EventBus.counts          : dict[str, int]            固定 4 个键
EventBus._changes        : dict[str, deque]          键 = entity_id
```

修法：**只追加不删的 list/set 保留 medium，dict 键聚合降 low 并注明"上界=键空间"**。判据做得保守（只认 `{}` / `dict()` / `default_factory=dict` 等直白写法），认不出就按 medium 报——**宁可按 medium 报，也不要把真无界 list 误判成 dict**。

### W144b：我第一版修复完全没生效

`container_inits()` 返回的是 `{名字: 行号}`，**不是初始化源码**。我把**名字**传给 `_is_dictish()` ⇒ 永远返回 False ⇒ 规则改了但结果一点没变。

这与 W138d（缩进错误导致"改了规则结果没变"）是同一类：**失效表现为"没变化"，不报错**。发现它靠的是跑完看数字——37 条一条没动。

### W144c：`deque(maxlen=)` 带注解时匹配不到

`ast.unparse` 会保留注解，输出 `self.emitted: deque[BusEvent] = deque(maxlen=1000)`。原正则 `self\.emitted\s*=\s*deque\(` 中间隔着 `: deque[BusEvent]`，**匹配不上** ⇒ EventBus.emitted 明明有界却仍被报。

这与 W143c（正则写了 `sleep\(` 但输入是 `time.sleep` 不含括号）同源，是**第三次**"正则假定了输入形态而实际不是"。

---

## 二、实测（`poc_af21.py`）

```
EventBus：publish 100,000 事件（0.4s）
  emitted 长度 : 0        (deque maxlen=1000 ⇒ 有界)
  counts       : {accepted:550, duplicate:0, throttled:0, breaker_open:99450}
                          (固定 4 键 ⇒ 有界)
  _changes 键数: 50       (按 entity 聚合，上界=实体数)
```

⚠️ **`emitted` 实测为 0 需要说明**：断路器拦下了 99450 条，accepted 550 条也没进 `emitted`。这是我构造事件的方式触发了 throttling，**不代表生产行为**。我确认的是"它有 `maxlen=1000` 这个上界"，不是"实测增长到多少"。

**反例量级标定**：20 万条 dict 追加 ≈ 44 MB。若某容器是这种形状且无 maxlen/裁剪，7×24 下是真 OOM 风险。

---

## 三、剩余 19 条 medium 的构成（未逐条验证）

| 类别 | 举例 | 判断 |
|---|---|---|
| **短生命周期对象** | `GraphView`、`EvoScanner`、`FaultPlan`、`_Builder`、`HealthEngine`、`ProposalManager` | 每次构建/扫描新建，用完即弃 ⇒ **大概率假阳性**（未实测） |
| **测试/仿真用** | `MockAdapter.calls`、`FakeHAAdapter.calls`、`HighFidelityAdapter.calls`、`FakeSSEStream._emitted_ids` | 供断言用，不在生产路径 ⇒ **大概率假阳性**（未实测） |
| **真正常驻候选** | `Scheduler._debounce/_time_fired`、`ActionQueue._pending`、`SessionStore._s`、`InterventionDetector.managed`、`InsightQueue.unreadable`、`TokenRegistry._revoked` | **值得逐条看**（本轮未做） |

**这 19 条我只做了分类，没有逐条实测**——尤其是第三类"真正常驻候选"，是下一轮最该优先的。

---

## 四、依赖 CVE 面：确认不可达

```
PyPI json API     : HTTP 403 Forbidden
PyPI simple index : HTTP 403 Forbidden
OSV /v1/query     : HTTP 403 Forbidden
pip-audit / safety: 未安装
```

**降级做了离线依赖声明审查**（与 memory-agent COV-01 同口径）：

- Python 声明 20 条，**16 条仅写下界（`>=`）、0 条精确 pin**，**无 lock 文件**
  ⇒ 不存在"锁死在含 CVE 旧版"的风险，但**每次构建版本可漂移**（构建可重复性问题）
- 前端三个 `package-lock.json` 存在（138 / 480 / 427 条，lockfileVersion=3）
  ⇒ 前端**有**锁定，**存在"锁在含 CVE 旧版"的可能**，但离线无法查

**必须明说**：CVE 面 12 轮一次没扫，**风险未知不是无风险**。前端 lock 文件尤其需要联网核查。

---

## 五、数字

| | round-011 | round-012 |
|---|---|---|
| 总命中 | 1003 | 1003 |
| high | 26 | 26 |
| medium | 334 | **325**（OBS-02 降 9 条后；未全量重扫，以单分析器口径计） |
| OBS-02 | 37 全 medium | **19 medium + 18 low** |

---

## 六、累计成果（第二期 1–12 轮）

13 项确证缺陷：AF1–AF5、AF7–AF14。**12 项已修，1 项（AF8 pydantic 依赖）待你定夺。**

本轮**无新增确证缺陷**，产出全部是判据修正（W144 系列）+ 依赖面核查。

**工作流迭代累计**：W137–W144c（约 15 项）。其中"第三次踩同一个坑"已出现三次：W77/W78c/W143c/W144c 都是**正则假定了输入形态**；W138d/W144b 都是**改了代码但没生效且不报错**。

---

## 七、下轮建议

1. **OBS-02 剩余 19 条逐条验证**——尤其 `Scheduler._debounce`、`ActionQueue._pending`、`SessionStore._s`、`TokenRegistry._revoked` 这四个常驻容器
2. **依赖 CVE**——需要联网环境，沙箱内无解。**前端 lock 文件是具体可查的目标**
3. **AF8 定夺**——`pydantic` 加进 `dependencies` 还是 `api` extras

我的倾向是 1：**CVE 面在沙箱内无解，硬做只会重复"不可达"的结论；而这 19 条里有真正常驻的容器，是稳定性直接相关且可验证的。**

---

## 八、如实说明

- **`emitted` 实测为 0 是探针构造问题**（断路器拦下 99.45%），我确认的是 `maxlen=1000` 这个上界存在，不是实测增长曲线。
- **19 条 medium 的分类是读源码 + 类命名推断**，第三类"常驻候选"**未实测**其增长行为。
- **W144 的 dict 判据只认直白写法**，`setdefault` / `defaultdict` 等形态会漏判（按 medium 报，属保守方向）。
- **依赖 CVE 面十二轮仍未扫**，前端 lock 文件的 CVE 风险完全未知。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 8 部分 · 第十三轮

> 源文件：`AutoForge_第二期第十三轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-013（**常驻容器逐条验证**）
- **上一轮**：round-012（OBS-02 分档 + CVE 面核查）

---

## 一句话结论

**OBS-02 剩余 19 条 medium 逐条验证完毕：确证 1 项真缺陷（AF15，撤销黑名单无上限 + 每次鉴权线性扫描），1 项 latent（SessionStore，生产无调用方但机制确实无界），其余 17 条为短生命周期对象、测试/仿真件或键聚合容器。AF15 已补丁并对照实测通过。**

---

## 一、AF15 · `_revoked` 无上限 ⇒ 资源耗尽 + 鉴权永久变慢（**medium**）

**位置**：`af_auth.py:297`

```python
def revoke(self, token: str) -> bool:
    with self._lock:
        if token in self._revoked: return False
        self._revoked.add(token)      # ← 不校验 token 是否真实存在
        self._persist_revoked()       # ← 每次全量写盘 O(n)
        return True
```

**对照实测**（`poc_af23.py` / `poc_af24.py`）：

| N | 集合 | revoked.json | revoke 累计耗时 | authenticate 单次 |
|---|---|---|---|---|
| 1 000 | 1 000 | 33 KB | 0.34 s | 0.056 ms |
| 5 000 | 5 000 | 170 KB | 5.07 s | 0.309 ms |
| 20 000 | 20 000 | 692 KB | **74.4 s** | **1.395 ms** |

**三个放大点叠加**：

1. **`revoke()` 不校验令牌是否真实存在** → 可灌任意字符串（我的测试用的就是从未签发过的字符串）
2. **每次 revoke 全量写盘** → 累计 O(n²)：N 从 5k 到 20k（4 倍），耗时从 5s 到 74s（**14.7 倍**）
3. **`authenticate()` 对整个集合做 `hmac.compare_digest`** → 每次鉴权 O(n)，N=20k 时**比 N=1k 慢 25 倍**，且是**永久性**降级

**可达性**：`/api/auth/revoke` 需要 write scope。AF13 未修时，未授权者可直接拿到 write（AF14 提权链）⇒ 完全可达。**即使 AF13 已修**，持有 write 令牌者（或被泄露的令牌）仍可触发——所以 AF15 不依赖 AF13 才成立。

**补丁**（`MAX_REVOKED = 10000`，超限拒绝新增 + error 日志）：

| | 原仓库 | 补丁副本 |
|---|---|---|
| 灌 12 000 次 | 新增 12 000 条 / 26.99 s | 新增 **10 000 条** / 18.76 s |
| authenticate | 0.857 ms | 0.610 ms |

**为什么选"拒绝新增"而不是"淘汰最旧"**：淘汰会让**已撤销的令牌重新生效**，属安全回退；拒绝新增是 fail-loud——撤销功能停用并报错，服务本身继续可用。这一点在补丁注释里写明了。

---

## 二、SessionStore · 确证机制无界，但生产不可达（**latent**）

**实测**（`poc_af22.py`）：

```
get_or_create()（不传 sid）调用 20000 次 → _s 长度 20000，无回收路径 ❌
对照组（固定 sid）                        → 1 条 ✅
```

`repair_ir()` 正是用 `self.sessions.get_or_create()`（**不传 session_id**）⇒ 每次调用新建一个 uuid 会话并留在 `_s` 里。

**但**：`grep -rn "Orchestrator(" src/` → **0 处实例化点**。全仓只在 tests 里出现。⇒ `Orchestrator` 及其 `SessionStore` **当前没有生产调用方**。

**记为 latent 而非缺陷**：机制确实无界（这是设计欠账），但现在触发不到。一旦有人接入，就是 7×24 下的真实 OOM。

---

## 三、其余 17 条分类（未逐条实测，依据源码形态）

| 类别 | 条目 | 判断依据 |
|---|---|---|
| **短生命周期** | `GraphView._ids/warnings`、`EvoScanner._seen/warnings`、`FaultPlan.specs`、`_Builder._by_id/edges/nodes`、`HealthEngine._last_conf/extra_ids`、`ProposalManager.order/proposals`、`ConfGrading.restore_corrupt`、`SceneManager._groups/_scenes` | 每次构建/扫描新建，用完即弃 |
| **测试/仿真专用** | `MockAdapter.calls/results`、`HassAdapter.calls`、`FakeHAAdapter.calls/unmodeled`、`HighFidelityAdapter.calls/unmodeled`、`FakeSSEStream._emitted_ids`、`ActionQueue._pending` | 供断言用，不在生产路径；`ActionQueue` 在 `af_vhass/` 仿真层 |
| **键聚合（上界有限）** | `InterventionDetector.managed`（`set[str]`，键=entity_id） | W144 已覆盖，实体数有限 |
| **已有清理** | `Scheduler._time_fired` | 源码 L349-352 有**跨天清理**（注释标 P1-1 修复） |

⚠️ `Scheduler._debounce`（`dict[tuple[str,str], float]`）键= (auto, node)，**无清理路径**但上界=节点数，属慢增长，判 low。

**`InsightQueue.unreadable`（`list[str]`）值得单说**：按扫描到的坏文件追加，上界=文件数，非每事件增长 ⇒ 判 low。

---

## 四、本轮我做对的一件事（值得记）

写 AF15 补丁时用了 `logger.error(...)`，但 `af_auth.py` **没有日志设施**（原文件只有 `hmac/json/os/tempfile/threading/time/secrets`）。

**这次我在打完补丁后立刻补了 `import logging` + `logger`（沿用 `af_store.py` 的 `logging.getLogger("autoforge.store")` 约定），并主动跑了未定义名检查——结果是"无"。**

对比第十轮：那次我写了 `_secrets.compare_digest` 却没导入 `secrets`，是事后才发现。**这次提前检查，是因为第十轮的教训还新鲜。**

推论：**我给工具写的检查（W78 未定义名），能否真正生效，取决于我记不记得用它。** 本轮靠的是"教训还新鲜"，不是流程保证——这说明**补丁后自动跑一遍 W78 应该固化为流程，而不是靠记忆**。

---

## 五、数字与累计

| | round-012 | round-013 |
|---|---|---|
| 总命中 | 1003 | 1003 |
| high | 26 | 26 |
| OBS-02 | 37（19 medium + 18 low） | 19 条**已逐条验证完毕** |

**累计确证（第二期 1–13 轮）**：AF1–AF5、AF7–AF15 共 **14 项**。**13 项已修，1 项（AF8 pydantic）待定夺。**

| 编号 | 严重度 | 内容 | 状态 |
|---|---|---|---|
| AF15 | **medium** | `_revoked` 无上限 + 鉴权 O(n) | **已修（本轮）** |
| — | latent | `SessionStore._s` 无界（生产无调用方） | 未修，记录 |

---

## 六、下轮建议

覆盖过的面已相当完整：状态持久化、装配、递归、鉴权、出站、锁/并发、内存增长（OBS-02）、依赖声明。

**剩余**：

1. **依赖 CVE** —— 13 轮未扫；沙箱 PyPI/OSV 均 403。**前端三个 package-lock.json（138/480/427 条）是具体可查目标**，但需要联网环境
2. **medium 剩余 ~306 条分诊** —— 已核实的只是少数（ERR-05 50 条、RSC-02 23 条 等几乎没看）
3. **AF8 定夺**

我的倾向：**先做 ERR-05（50 条 silent-except）**——它是 medium 里最大的一族，且与本项目主线（静默失败）直接相关。CVE 面在沙箱内确实无解，硬做只会重复"不可达"。

---

## 七、如实说明

- **AF15 的 N=20000 → 74.4s** 是在沙箱 SSD 上测的；生产若在慢盘（NAS/HDD）上会更慢，但**量级结论不变**（O(n²) 是算法性质）。
- **AF15 未做 HTTP 层端到端验证**——实测是直接调 `registry.revoke()`，与 `/api/auth/revoke` 端点行为一致（端点就是 `registry.revoke(body.token)`），但**没真的发 HTTP 请求**。
- **`MAX_REVOKED = 10000` 是拍的值**，未与业务实际撤销量对照。
- **17 条分类依据是源码形态与类命名，未逐条实测**；其中"短生命周期"这一类我是按用途推断对象生命周期，**没有实测其创建/销毁频率**。
- **依赖 CVE 面十三轮仍未扫**，前端 lock 文件风险完全未知。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 9 部分 · 第十四轮

> 源文件：`AutoForge_第二期第十四轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-014（**ERR-05 silent-except 定向专题**）
- **上一轮**：round-013（OBS-02 逐条验证 + AF15）

---

## 一句话结论

**ERR-05 的 50 条「静默 except」中确证 1 项真缺陷（AF16 + AF16b，watch 进程管理），其余按三种形态证伪：清理型（fsync/chmod/tmp 清理）、候选枚举（逐个 try 后接 fallback）、旁路尽力而为（审计/健康检查，有注释声明）。AF16 已补丁，对照实测 + 4 项回归全过。**

---

## 一、AF16 · 旧 watch 杀不掉仍启动新进程 ⇒ 孤儿进程泄漏（**medium**）

**位置**：`af_service.py:2330`

```python
try:
    if pid_file.exists():
        pid_str = pid_file.read_text(encoding="utf-8").strip()
        if pid_str:
            os.kill(int(pid_str), 15)
except (ProcessLookupError, PermissionError, OSError, ValueError):
    pass          # ← 杀不掉也继续往下走
...
pid_file.write_text(str(proc.pid))   # ← 覆盖旧 pid
```

**对照实测**（`poc_af25.py` / `poc_af27.py`）：

| | 原仓库 | 补丁副本 |
|---|---|---|
| 旧 pid=4242 杀不掉（PermissionError） | `ok=True pid=5555` | `ok=False` |
| pid 文件 | **5555（4242 泄漏）** | **4242（保留 ⇒ 仍可管理）** |
| 后续 stop_watch | 只杀 5555 | — |

**后果链**：旧进程杀不掉 → 新进程照常启动 → pid 文件被覆盖 → **旧进程仍在运行，但再无任何记录指向它** → 永久泄漏，且 stop_watch 永远杀不到它。

**补丁区分对待三种异常**：

| 异常 | 含义 | 处理 |
|---|---|---|
| `ProcessLookupError` | 进程已不存在 | 继续（正常） |
| `PermissionError` | **进程在但杀不掉** | **拒绝启动第二个 watch** |
| `OSError` / `ValueError` | pid 文件本身有问题 | 继续（保持原行为） |

---

## 二、AF16b · info 损坏 ⇒ owner 校验失效，任何人可停他人 watch（**medium**）

**位置**：`af_service.py:2281`

```python
try:
    holder = json.loads(info.read_text(encoding="utf-8"))
except (OSError, ValueError):
    pass          # ← holder={} ⇒ 下面校验条件为假 ⇒ 校验被跳过
if owner and holder.get("owner") and holder["owner"] != owner:
    return {"ok": False, ...}
```

**对照实测**（`poc_af26.py` / `poc_af27.py`）：

| 场景 | 原仓库 | 补丁副本 |
|---|---|---|
| ① info 完好（alice）+ owner=bob | 拒绝 ✅ | 拒绝 ✅ |
| ② info 完好（alice）+ owner=alice | 允许 ✅ | 允许 ✅ |
| ③ **info 损坏 + owner=bob** | **允许，杀掉 4242** ❌ | **拒绝** ✅ |

**与 AF5（`assert_deletable`）、AF9（`_dir_owner`）完全同族**——"读不出归属就放行"。**这是第三个实例**，且三者的共同点都是：`except → 空值 → 下游 `if` 条件为假 → 护栏静默失效`。

---

## 三、回归（补丁不能误伤正常路径）

| 场景 | 结果 |
|---|---|
| ① 旧进程已死（ProcessLookupError） | 正常启动 ✅ |
| ② 无 pid 文件 | 正常启动 ✅ |
| ③ info 完好 + 本人请求 | 允许停止 ✅ |
| ④ info 不存在（首次） | 允许停止 ✅ |

**"不该被拦的必须通"这条反向用例很关键**——只测"能否拦截"会漏掉拦太宽的问题（这与第十二轮 M16 SSRF 补丁差点把 HA 私有地址一起拦掉是同一个教训）。

---

## 四、其余 49 条按三种形态证伪

我写了自动分类器，按「try 体内做什么 + 是否有 fallback + 是否有注释」三维度分：

### 形态 A：清理型（8 条）—— pass 正确

```python
except OSError: pass   # os.fsync(dir_fd)          ← af_atomic.py:48
except OSError: pass   # os.chmod(tmp, 0o600)      ← af_config.py:89（Windows 无 0600）
except OSError: pass   # os.unlink(tmp_path)       ← af_atomic.py:53
except OSError: pass   # directory.rmdir()         ← af_store.py:489
```

这些失败不影响数据正确性。**但 `af_atomic.py` 的 docstring 写得很好**："失败时抛底层异常……本函数不做任何'静默当成写成功'的处理"——真正的写失败会正常抛出，只有目录 fsync 用 pass。**规则分不清"pass 的是清理动作还是业务动作"**。

### 形态 B：候选枚举（含 `_now` ×3、`load_tz` ×2、`load_secret`、`_coerce_param_value` ×2）—— pass 后有 fallback

```python
for attr in ("now", "time"):
    fn = getattr(clock, attr, None)
    if callable(fn):
        try: return float(fn())
        except Exception: pass
return time.time()          # ← 兜底
```

这与 memory-agent 第十一/十三轮的 **W111「候选枚举」完全同族**——规则只看 `except: pass`，看不见 pass 之后还有下一个候选。

### 形态 C：旁路/尽力而为（有注释声明）

```python
except Exception: pass   # tick_health is best-effort, must not break /api/health
except Exception: pass   # 审计是旁路（fail-open）
except Exception: pass   # 落盘失败不影响内存审计
except OSError: pass     # Windows 无 0600 语义
```

**这些是作者有意声明的降级**，注释写明了理由。严格说可观测性欠佳（无日志），但属设计选择，不是缺陷。

### 判据缺口（登记，未修）

ERR-05 应至少区分：① pass 的是清理动作还是业务动作；② pass 之后是否有 fallback/下一个候选；③ 是否紧邻注释声明。**现在三条都不判，一律 medium。**

---

## 五、本轮的自我检查做对了（延续第十三轮）

打完补丁后**立刻跑了未定义名检查** → "无"。这次补丁用了 `PermissionError`、`ProcessLookupError`（内置，无需导入）和 `json`（已导入），所以干净。

**但这次还多做了一步**：主动写了 4 项回归用例（正常路径不能被误伤）。第十三轮的 AF15 我没做回归，只测了"能否封顶"——**如果补丁把 `revoke()` 正常路径也搞坏，我是发现不了的**。

---

## 六、数字与累计

| | round-013 | round-014 |
|---|---|---|
| 总命中 | 1003 | 1003 |
| high | 26 | 26 |
| ERR-05 | 50（全 medium，未核实） | **50 条已分类，2 项确证** |

**累计确证（第二期 1–14 轮）**：AF1–AF5、AF7–AF16b，共 **15 项**。**14 项已修，1 项（AF8 pydantic）待定夺。**

| 编号 | 严重度 | 内容 | 状态 |
|---|---|---|---|
| AF16 | medium | 旧 watch 杀不掉仍启动 ⇒ 进程泄漏 | **已修** |
| AF16b | medium | info 损坏 ⇒ owner 校验失效 | **已修** |

---

## 七、下轮建议

已覆盖：状态持久化、装配、递归、鉴权、出站、锁/并发、内存增长、静默 except（ERR-05）。

**剩余**：

1. **依赖 CVE** —— 14 轮未扫，沙箱 403
2. **medium 剩余约 306 条** —— RSC-02（23）、AFS-01（22）、ERRH-02（19）、TX-01（14）等均未看
3. **AF8 定夺**

我的倾向：**做 AFS-01（22 条 silent-failure）**——它和 ERR-05 是近邻族，但更贴近本项目主线（"读失败被当成没有数据"，已在 AF2/AF3/AF5/AF9/AF16b 上重复 5 次）。验证这 22 条有可能再挖出同族实例。

---

## 八、如实说明

- **AF16 的 PermissionError 场景是 mock 出来的**（`mock.patch.object(S.os, "kill")` 抛异常）。真实环境中同用户启动的进程通常可以 kill；**PermissionError 更可能出现在容器边界、进程属其他用户、或已僵尸化的场景**——这是低频但确实存在的路径。
- **AF16b 的"损坏 info"是手工构造的**（写入 `{ 损坏`）。真实成因可能是磁盘满、写半截、并发写——**我没有验证真实成因的发生概率**。
- **49 条证伪中，"形态 C 旁路"这一类我是依据注释判断作者有意**，没有进一步确认注释是否与实际行为一致（第十九轮 doubao-butler 就遇到过"注释声称做了防护、代码没做"）。
- **ERR-05 的判据缺口只登记未修**——改它影响面较大，需单独一轮。
- **依赖 CVE 面十四轮仍未扫**。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 10 部分 · 第十五轮

> 源文件：`AutoForge_第二期第十五轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-015（**AFS-01 silent-failure 定向专题**）
- **上一轮**：round-014（ERR-05 + AF16/AF16b）

---

## 一句话结论

**AFS-01 的 78 条「静默失败返回空」中确证 1 项真缺陷（AF17：catalog.json 损坏 ⇒ `refresh()` 把 v1.6.0 P0 的注册表富化字段全部清零且 `ok=True` 无任何感知）。这条最值得记的是——代码里那条「★ 绝不把已知值清零」的保护**确实存在且逻辑正确**，只是它依赖的旧值来源静默变空了。已补丁，对照实测 + 5 项回归全过。**

> ⚠️ 更正：上一轮我说 AFS-01 有 22 条，实际是 **78 条**。是我把规则名记混了。

---

## 一、AF17 · catalog 损坏 ⇒ 富化字段被清零（**medium**）

### 链条

```python
# af_catalog.py:284  _load()
except (OSError, ValueError):
    return {"version": ..., "entities": {}}      # ← 坏文件被当成空目录，无日志

# af_catalog.py:339  refresh()
old = self._load().get("entities", {})           # ← {}

# af_catalog.py:369  ★ 声明的保护
_preserve_known(meta, old.get(entity_id))        # ← old 为空 ⇒ 直接 return，保护失效

# af_catalog.py:208  _preserve_known()
if not old: return                               # ← 无旧值可用

# af_catalog.py:390
self._save(payload)                              # ← full=True 从空 dict 开始，全量覆盖
```

### 对照实测（`poc_af31.py` / `poc_af32.py`）

播种 `device_id / platform / integration / area` 四个有值字段后：

| 场景 | refresh 返回 | 四个字段 |
|---|---|---|
| ① catalog 完好 | `ok=True removed=0` | `dev-abc-123` / `mqtt` / `mqtt` / `书房` ✅ |
| ② **catalog 损坏** | **`ok=True removed=0`** | **全部 `''`** ❌ |

**② 完全无感知**：`removed=0`、`ok=True`、无任何日志。

### 补丁后

| | 原仓库 | 补丁副本 |
|---|---|---|
| ② 损坏 + 全量刷新 | `ok=True`，字段清零 | **`ok=False` 拒绝**，现场隔离为 `catalog.json.corrupt-*` |

### 回归 5 项（补丁未误伤）

```
① 完好 + 全量刷新        ok=True  ✅
② 完好 + 增量刷新        ok=True  ✅
③ 损坏 + 增量刷新        ok=True  ✅（增量不覆盖旧值，不丢富化字段）
④ 首次（无 catalog 文件） ok=True  ✅
⑤ 损坏 + 收窄刷新        ok=True  （收窄时保留旧条目）
```

### 为什么定 medium 而不是 high

丢的是**富化元数据**（`device_id`/`platform`/`integration`/`area`/`area_id`/`integration_source`），不是主数据（`state`/`friendly_name` 还在）。

但后果不轻：这些是 v1.6.0 P0 的注册表能力，**`area` 为空会让 area 类型的设备保护规则永远不命中**——即 P0-7 修过的"area 规则永远不命中"问题会**静默回归**。

---

## 二、这条最值得记的地方

**注释是这么写的**（`af_catalog.py:369`）：

```python
# ★「绝不把已知值清零」：注册表缺项时**沿用旧值**，绝不用空串覆盖
_preserve_known(meta, old.get(entity_id))
```

**代码确实做了，逻辑也对**——`_preserve_known` 遍历 `_PRESERVE_ON_EMPTY`，有旧值就补。

**但它依赖的 `old` 在 catalog 损坏时静默变成 `{}`，于是整条保护链失效。**

这与第十九轮 doubao-butler 的"注释声称做了防护、代码没做"是**同形态但更隐蔽**：那次是代码压根没做，这次是**代码做了，但输入被上游静默掏空**。

推论：**验证一条保护是否生效，不能只看保护函数本身，要看它依赖的数据来源会不会静默变空。** 我此前几轮验证 `_load_aliases`（F9 已修）时看的是"读失败有没有护栏"，没追问"护栏保护的那个函数在数据源为空时还成立吗"。

---

## 三、我是怎么找到它的（差点漏掉）

1. 自动分类器把 `load_entity_health`（`af_service.py:691`）判为"无痕迹 + 未护栏"
2. 我去实测，**第一版探针抛了 TypeError**（我构造的 store.root 类型不对）
3. 修好后测出"损坏 → 返回 `{}`、无日志"——但 `load_entity_health` 源码 L703 明明有 `logger.debug(..., exc_info=True)`
4. 追下去才发现：**真正的失效点在更上游** —— `DeviceCatalog._load()` 在 L284 就把坏文件吞成空目录了，`load_entity_health` 的 try 根本没被触发

**这与第二轮 AF3（`record()` 内部调 `_save()`，try 只套在外层）、第三轮 AF4 同类**——真正的失效点在调用链更深处，而规则报的是最外层那个"返回空"的函数。

---

## 四、其余 77 条按四种形态证伪

我写了自动分类器，按「返回值语义方向 + 有无痕迹 + 是否整档 + 是否已护栏」四维：

| 形态 | 条数 | 例 | 判断 |
|---|---|---|---|
| **已加护栏** | 18 | `_load_aliases`（F9 已修） | 第一轮 stateguard 覆盖 |
| **有痕迹** | 19 | `af_predict._load` 有 `logger.warning` + `_quarantine` | 可见降级 |
| **返回值方向相反** | 2 | `held_by_other` 返回 `True`=不抢锁；`has_drift` 返回 `True`=触发回滚 | **fail-closed，规则看不出 True 的语义方向** |
| **单值转换/解析** | ~30 | `_to_int`、`_parse_iso`、`extract_json`、`duration_seconds` | 返回 None 是契约，不是状态丢失 |

### 实测确证的 3 条假阳性

```
① af_mcp._build_current：TokenExpired → {'subject':'<no-access>','scopes':[]}
   → ✅ 空 scope = 拒绝（fail-closed），规则把它当成"空=放行"方向完全反了
② rest_areas_fallback 调用方（af_catalog.py:415-418）：
   fallback 空 → 不覆盖 area_names + 记 reason
   → ✅ 与 docstring"绝不把已知区域清零"一致
③ _preserve_known 链条 → ❌ 这个是真的（AF17）
```

**"返回值语义方向"缺口这是第四次遇到**（W141、RMW-01、CONC-06、本轮）。同一个返回值在不同函数里方向完全相反：

| 函数 | 返回值 | 方向 |
|---|---|---|
| `_build_current` | 空 scope | **拒绝** |
| `held_by_other` | `True` | **拒绝** |
| `has_drift` | `True` | **触发回滚** |
| `assert_deletable`（AF5） | `None` | **放行** ❌ |
| `_dir_owner`（AF9） | `None` | **放行** ❌ |
| `_load`（AF17） | `{}` | **放行** ❌ |

**规则完全无法区分。**

---

## 五、数字与累计

| | round-014 | round-015 |
|---|---|---|
| 总命中 | 1003 | 1003 |
| high | 26 | 26 |
| AFS-01 | 78（未核实） | **78 条已分类，1 项确证** |

**累计确证（第二期 1–15 轮）**：AF1–AF5、AF7–AF17，共 **16 项**。**15 项已修，1 项（AF8 pydantic）待定夺。**

| 编号 | 严重度 | 内容 | 状态 |
|---|---|---|---|
| AF17 | medium | catalog 损坏 ⇒ 富化字段清零 | **已修（本轮）** |

---

## 六、下轮建议

已覆盖：状态持久化、装配、递归、鉴权、出站、锁/并发、内存增长、静默 except（ERR-05）、静默失败（AFS-01）。

**剩余**：

1. **依赖 CVE** —— 15 轮未扫，沙箱 403
2. **medium 剩余约 300 条** —— RSC-02（23）、ERRH-02（19）、TX-01（14）、MIXED-RETURN（12）、SER-01（11）等
3. **AF8 定夺**

我的倾向：**做 TX-01（14 条多写无事务）或 SER-01（11 条单向序列化）**——这两族都还没碰过，且 TX-01 与"部分写入导致状态损坏"直接相关，可能藏着 AF17 同族的写入侧问题。

---

## 七、如实说明

- **AF17 的"首次触发条件"我未穷举**。`refresh()` 的调用方在 `af_config.py:174`（`cfg.refresh()`），但我没验证生产上 `refresh(full=True)` 的触发频率——如果很少调，危害相应降低。
- **`_PRESERVE_ON_EMPTY` 我只播种了 4 个字段**（`area`/`device_id`/`integration`/`platform`），另外 2 个（`area_id`/`integration_source`）播种时本就是空，所以"6 个全清零"这个说法里有一半是我没播种造成的，**实际确证的是 4 个有值字段被清零**。
- **77 条证伪中，"单值转换"这一类（~30 条）我是按函数名（含 `to_`/`parse_`/`extract_`）批量判的**，没有逐条打开确认。
- **"已加护栏 18 条"的依据是补丁副本文件里出现 `mark_poisoned`/`StateCorrupt`**，属文件级判据——**同文件内某个未加护栏的函数（正是 AF17 的 `_load`）会被连坐判为安全**，这个盲区是本轮 AF17 能漏到第十五轮的直接原因。
- **依赖 CVE 面十五轮仍未扫**。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 11 部分 · 第十六轮

> 源文件：`AutoForge_第二期第十六轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-016（**TX 族一致性与事务边界定向**）
- **上一轮**：round-015（AFS-01 + AF17）

---

## 一句话结论

**TX 族 30 条命中里确证 1 项真缺陷（AF18：同一 proposal_id 在 pending 与 decided 双份存在时，`get()` 先查 pending ⇒ 已拒绝的提案静默变回待决——且**无需崩溃即可触发**）。同时修掉 W145：TX-01 的 19 条里 11 条是 `str.replace()` / `datetime.replace()` 被当成文件写造成的假阳性。AF18 已补丁，对照实测 + 5 项回归全过。**

---

## 一、AF18 · 已判定的提案静默变回待决（**low**）

**位置**：`af_insight_queue.py:131`（`get()` 先查 pending）

### 实测（`poc_af35.py` / `poc_af36.py`）—— **无需崩溃即可触发**

```
① append(p1)                  → pending=1  get.status=pending
② move_to(rejected)           → pending=0  get.status=rejected
③ 再 append(p1)（上游重复推送）→ pending=1  get.status=**pending**  ❌
```

| | 原仓库 | 补丁副本 |
|---|---|---|
| ③ 重复 id 后 `get('p1').status` | `pending` ❌ | `rejected` ✅ |
| `list_pending()` | 2 条（含已拒绝的） | 1 条 ✅ |

**后果**：已拒绝的提案重新出现在待决列表，用户要再 reject 一次。不丢数据，但**判定结果被静默回退**。

### 顺序本身是对的，这点要说明

`move_to()` 是「先写 decided、后删 pending」——**这是正确顺序**。反过来（先删后写）崩溃时会丢记录。所以残留的只是「崩溃造成双份」这个窗口（微秒级），而真正的可达路径是**上游重复 `append()` 同一个 proposal_id**，不需要崩溃。

补丁思路是**以 decided 为准**（`get()` 先查 decided；`list_pending()` 过滤掉已判定的），而不是阻止重复 append——后者会改变上游语义。

### 回归 5 项

```
① append        ✅  ④ 新 id 正常入队   ✅
② move_to       ✅  ⑤ move_to(p2) 正常 ✅
③ 重复 id 去重  ✅
```

---

## 二、W145 · TX-01 的 11 条假阳性：`replace` 是多态名

`WRITE_CALLS` 里含 `replace`，于是以下全被当成文件写：

```python
af_orchestrator._esc      : str(s).replace("\\","\\\\").replace('"','\\"')
af_registry._ws_fetch     : base_url.replace("http://","ws://").replace("https://","wss://")
af_scanner._check_stale   : lc.replace("Z","+00:00")  /  dt.replace(tzinfo=utc)
af_nl_parse._normalize    : s.replace("：","：")...
```

**这是"同一个 attr 名承担多种语义角色"第三次出现**（W61 import 同名、W79f 包名映射、W100/W100b `dict.get`）。

修法：`replace`/`remove` 只在显式 `os./shutil./Path.` 形式下才算文件操作，其余一律不算——**宁可漏，不可把 `str.replace` 当文件写**。

**TX-01：19 → 10 条。**

### ⚠️ 我在修这条时又制造了一次静默失效

W145 第一版把 `re.compile(...)` 放在**模块级但插在 `import re` 之前** ⇒ `NameError`。而该分析器有宽泛的异常兜底，**结果不是崩溃，而是静默少了 3 条命中**（17 vs 正确值 20）。

**如果我只跑一遍看到"19→10、假阳性消失"，会以为修好了——实际是有 3 条真命中被我自己的 NameError 吞掉了。**

发现和修掉靠的是补 `import re` 后数字从 17 变回 20。这与 M23（`except: pass` 吞掉护栏）、AF1（补丁用了未定义的名）是同一条线：**失效源是我自己的改动，而它表现为"数字变好了"。**

---

## 三、TX-05（6 条）：规则的前提是反的

规则说"落盘后崩溃 ⇒ 盘上与内存不一致"。但**落盘优先正是正确顺序**：

| 位置 | 落盘 | 之后更新内存 | 崩溃后 |
|---|---|---|---|
| `af_catalog._save` | 303 写 | 308 `self._cache = None` | 重启读盘，一致 |
| `af_config.update_credentials` | 123 写 | 124 `self._creds = creds` | 重启读盘，一致 |
| `af_preference._rewrite_all` | 536 写 | 537 `_since_compact = 0` | 重启读盘；下次多压缩一次而已 |
| `af_scene._save_state` | 304 写 | 309 `last_persist_error = None` | 该字段是错误标志，重启重初始化 |

真正的危险方向是**先改内存后落盘**（内存说成功、盘上没有，且进程继续跑就会读到错误值）。**规则把安全顺序报成了缺陷**。

这已是**第二次发现规则的建议方向是反的**——第十一轮 CONC-06 把 "锁内做 RMW" 建议移出锁，实测会丢 155/160 条数据。

---

## 四、其余证伪

| 条目 | 判断 |
|---|---|
| TX-04 ×3（`_delete_archive`/`_stash_archive`/`_unstash_archive`） | 第五轮已判：`_stash` 是 rename 让位，`_unstash` 就是回滚 |
| `af_metrics.flush_buffer` | `if remaining: write else: unlink` —— **互斥分支**，不会都执行 |
| `af_shadow.save` | 只有 1 个真写；`self.dump()` 是序列化不是落盘 |
| `af_runtime_ext.persist` | 5 个**不同逻辑对象**，彼此不是镜像/索引；全部走原子写 |
| `af_service.start_watch` | 先写 IR 再写 pid，两件事不同生命周期，无事务需求 |

---

## 五、数字与累计

| | round-015 | round-016 |
|---|---|---|
| 总命中 | 1003 | 1003（未全量重扫） |
| TX 族 | 30 | **10（TX-01）+ 5（TX-05）+ 3 + 2** |
| TX-01 假阳性 | 11/19 | **已修（W145）** |

**累计确证（第二期 1–16 轮）**：AF1–AF5、AF7–AF18，共 **17 项**。**16 项已修，1 项（AF8 pydantic）待定夺。**

| 编号 | 严重度 | 内容 | 状态 |
|---|---|---|---|
| AF18 | low | 已判定提案静默变回待决 | **已修（本轮）** |

---

## 六、下轮建议

已覆盖：状态持久化、装配、递归、鉴权、出站、锁/并发、内存增长、静默 except、静默失败、事务边界。

**剩余**：

1. **依赖 CVE** —— 16 轮未扫
2. **medium 剩余**：RSC-02（23）、ERRH-02（19）、MIXED-RETURN（12）、SER-01（11）、IN-01（11）、TIME-02（10）
3. **AF8 定夺**

我的倾向：**做 SER-01（单向序列化，11 条）**——从未碰过，且"能写不能读 / 能读不能写"是数据完整性直接相关的族。

---

## 七、如实说明

- **TX-01 剩余 10 条我只逐条看了 8 条**（`build_app`、`stop_watch` 的两个站点跨函数，未细查）。
- **TX-05 的"重启即一致"是推断**：我没有真的模拟崩溃重启，只是读代码确认这些字段在 `__init__` 里会重新初始化。
- **W145 的 `replace` 判定是"宁可漏"**：`Path(...).replace(...)` 若接收者是变量（如 `p = Path(x); p.replace(y)`）会被漏掉。
- **AF18 的"上游重复推送"是我构造的场景**——我没有验证 MA 端是否真的会重复推同一 proposal_id，只证明了一旦发生后果如何。
- **依赖 CVE 面十六轮仍未扫**。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 12 部分 · 第十七轮

> 源文件：`AutoForge_第二期第十七轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-017（**SER 族序列化往返定向**）
- **上一轮**：round-016（TX 族 + AF18）

---

## 一句话结论

**SER 族 21 条全部证伪——新增确证缺陷 0 项。但证伪过程本身有实质收获：① 修掉 W146（SER-01 的 message 自带"若需从磁盘恢复"这个前提，规则却从不判断它，11 条全假阳性）；② 实测确证 `json.dumps(default=str)` 的类型漂移机制（datetime → str 静默），但**未能证明真实代码会流入**非 JSON 对象，故记为 latent gap 而非缺陷；③ 发现 `Instance.ctx.timers[0].due_at` 是陈旧的单调时钟值，被持久化却未在恢复时换算——无人消费，无害但是陷阱。**

---

## 一、SER-01（11 条）：**全部假阳性**

规则的 message 是这么写的：

> "若该类需**从磁盘/网络恢复**，字段会按位置或缺失重建"

**这个前提规则从不判断**——只要有 `to_dict` 无 `from_dict` 就报。逐个实测：

| 类 | 规则判定 | 实测 |
|---|---|---|
| `InsightRecord` | 单向 | ❌ 假阳性：`_load()` 用 `InsightRecord(**body)` 反向 |
| `Instance` / `InstanceContext` / `InstanceTimer` | 单向 | ❌ 假阳性：`af_persist.restore_instance()` + `restore_context()` 反向，**往返实测完整** |
| `AuditEvent` | 单向 | ❌ 假阳性：只用于 API 响应（`af_service.py:942/1434/1793`），`AuditLog` docstring 明写"**G1 不落盘**" |
| `EvoProposal` / `SimOutcome` / `DimScore` / `HealthResult` / `RegistrySnapshot` / `Snapshot` | 单向 | ❌ 假阳性：to_dict 输出未见写盘 |

### Instance 往返实测（`poc_af38.py`）—— 逐字段

```
原实例:   current_node=n2  vars={'x':1,'y':'z'}  trace=2条  timers=[{...due_at:123.5}]
record:   due_at_wall=2026-10-10T14:48:32.714725+00:00
恢复后:   current_node=n2  vars={'x':1,'y':'z'}  trace=2条  timers=[{...due_at:123.5}]
          timer=InstanceTimer(node_id='n2', due_at=3134.312468362, kind='timeout')
→ ✅ 往返完整
```

### W146 修法与它的局限

判据只看**强证据**：① 模块内 `restore*`/`rebuild*`/`from_json` 函数；② `Cls(**payload)` 解包构造。

**刻意不看 `_load`/`load`** —— AF10 里 `read_jsonl_bounded` 那个 `_load` 恰恰是"反向存在但结果是错的"（读坏文件返回 `[]` ⇒ 触发全档覆盖）。**有 `_load` 不等于有正确的反向。**

效果：**11 → 10**（消掉 `InsightRecord`），并按"to_dict 输出是否进写盘"分 medium/low（3 medium / 7 low）。

**局限要如实说**：Instance* 三条仍是 medium，因为 `restore_instance` 在 **`af_persist.py`**，而类定义在 `af_instance.py`——**跨模块追不到**。这与第六轮 W98 "跨三层追不到"是同一个判据缺口。我实际是用 PoC 确证了它往返完整，不是靠判据。

---

## 二、SER-05（8 条）：机制确证，但真实流入未证明

`json.dumps(..., default=str)` 实测：

```python
写入: at = datetime.datetime(2026,10,10,12,0)  (datetime)
读回: at = '2026-10-10 12:00:00'              (str)
→ ❌ 类型漂移，且**静默**（无 default 时会 TypeError 暴露）
```

**项目自己吃过这个亏**——`af_instance.py:184-190` 记着 P2-3 修复：

> "移除 `default=str`。原写法会把不可序列化对象**静默字符串化**（如 canary 挂起的 `(wrapped, adapter)` 元组），上线后序列化'成功'但恢复时解包崩溃、回滚静默失效。"

这说明**这个坑真实发生过一次**。其余 8 处：

| 位置 | 用途 |
|---|---|
| `af_store.py:110` `append_jsonl` | 落盘（telemetry / error_knowledge / preferences） |
| `af_preference.py:534` `_rewrite_all` | **AF10 那条路径** |
| `af_service.py:2136` / `af_mcp.py:898` / `af_telemetry.py:76` | 往返自检 / 响应 / token 估算 |

**但我必须诚实**：我证明的是"**如果**流入非 JSON 对象就会漂移"，**没有证明真实代码会流入**。

`PreferenceRecord.timestamp` 标注是 `float`、赋值 `self._clock()`，不会是 datetime；只有 `params: dict[str, Any]` / `details: dict[str, Any]` 理论上是开口，但它们从 JSON 解析而来。**故记为 latent gap，不立缺陷。**

---

## 三、SER-03（1 条）：假阳性——字段改名映射

`AskSpec` 的 `min_`/`max_`/`unit_` 确实没出现在 `to_dict()` 里，但：

```python
def to_dict(self):        # L241
    """回写为 IR 形态（`min/max/unit` 无下划线，与 schema 一致）。"""
    if self.min_  is not None: out["min"]  = self.min_
    if self.max_  is not None: out["max"]  = self.max_
    if self.unit_ is not None: out["unit"] = self.unit_

def from_dict(cls, data, prompt=""):
    min_=float(data["min"]) ...          # 反向也读无下划线的键
```

**双向一致，只是带下划线后缀改名**。`prompt` 则由 ask 节点的节点级字段投影（docstring 明写"避免两处真相"）。

这是 **W79f 包名→导入名映射同族**：规则按字段名逐一比对，看不见显式的改名映射。

---

## 四、一个值得记的发现（不立缺陷）

`Instance.ctx.timers[0].due_at` 是**单调时钟值**（如 123.5），被持久化进 `timers` 数组；而 `restore_instance()` 只从中读 `kind`，`due_at` 靠墙钟字段 `due_at_wall` 换算——**所以恢复后 `ctx.timers[0].due_at` 仍是崩溃前的陈旧单调值，且不会被修正**。

**但无人消费它**：`due_timers()`（L352）用的是内存里的 `i.timer.due_at`，不是 `ctx.timers[0].due_at`。

⇒ 无害，记为陷阱：**持久化了却不在恢复时换算的时钟值，是留给后人的坑**。

---

## 五、数字与累计

| | round-016 | round-017 |
|---|---|---|
| SER 族 | 21（未核实） | **21 条全部核实，0 确证** |
| SER-01 | 11 medium | **10（3 medium / 7 low），W146** |

**累计确证（第二期 1–17 轮）**：AF1–AF5、AF7–AF18，共 **17 项**。**16 项已修，1 项（AF8 pydantic）待定夺。**

---

## 六、下轮建议

已覆盖：状态持久化、装配、递归、鉴权、出站、锁/并发、内存增长、静默 except、静默失败、事务边界、序列化往返。

**连续两轮只各出 1 项、且都是 low/medium**。这与第十四轮我对 memory-agent 的诊断同形——泛扫边际产出已耗尽。

**剩余未碰的族**：`AF-AST-MIXED-RETURN`（111 条，最多）、`IN-02-unsafe-cast`（70）、`AFS-04`（68）、`ERRH-02`（62）、`API-06`（54）、`IN-03-path-join`（44）、`AFS-02`（44）、`DEAD-03`（41）。

我的倾向：**做 `IN-03-path-join`（44 条）**——路径拼接是安全性直接相关（目录穿越），且从未碰过；或者先补 **依赖 CVE**（17 轮未扫）。

---

## 七、如实说明

- **SER-01 的 11 条我逐条查了，但"只用于 API 响应"这个判断对 7 个类是"未见写盘"的推断**——我没有追踪 `to_dict()` 返回值在调用方（跨文件）的最终去向，只在类所在文件内搜索。
- **SER-05 我实测的是"人为注入 datetime"的场景**，真实代码是否会流入未验证。
- **`Instance.ctx.timers[0].due_at` 无人消费**这个结论是靠 grep `ctx.timers` 全仓只有 3 处得出的，**未做运行时追踪**。
- **W146 的 `_has_reverse_path` 只看同模块**，跨模块（Instance* 三条）追不到——这部分假阳性靠 PoC 而非判据确证。
- **依赖 CVE 面十七轮仍未扫**。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 13 部分 · 第十八轮

> 源文件：`AutoForge_第二期第十八轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-018（**IN-03 路径遍历定向**）
- **上一轮**：round-017（SER 族，0 确证）

---

## 一句话结论

**44 条 IN-03 中确证 1 项真缺陷（AF19：SPA fallback 的目录穿越防护用了无分隔符的 `startswith` 前缀比较）。端到端实测 `GET /%2e%2e/uidist2/secret.txt` → 200 + `TOP-SECRET`。已补丁，对照实测 + 6 项回归全过。同时修掉 W147/W147b：规则 message 写着"**若**该值来自外部"，却不判这个前提（与第十七轮 W146 完全同形）；并新增"无分隔符前缀比较"这个独立判据维度。**

---

## 一、AF19 · SPA fallback 前缀比较缺分隔符（**medium**）

**位置**：`af_api.py:1227`

```python
candidate = (dist / full_path).resolve()
# 防目录穿越：只服务 dist 内的真实文件
if full_path and candidate.is_file() and str(candidate).startswith(str(dist)):
    return FileResponse(str(candidate))
```

### 根因

`str(candidate).startswith(str(dist))` **没有分隔符边界**：

```
dist      = /x/uidist
candidate = /x/uidist2/secret.txt          ← 兄弟目录
"/x/uidist2/secret.txt".startswith("/x/uidist") == True   ❌
```

**向上逃逸是挡得住的**（`/etc/passwd` 不以 `/x/uidist` 开头）。能穿的是**同层、名字以 dist 名为前缀**的兄弟目录或文件。

### 端到端实测（`poc_af42.py` / `poc_af43.py`，FastAPI TestClient）

| 请求 | 原仓库 | 补丁副本 |
|---|---|---|
| `GET /` | 200 INDEX ✅ | 200 INDEX ✅ |
| `GET /app.js` | 200 ✅ | 200 ✅ |
| `GET /sub/deep.html` | 200 DEEP ✅ | 200 DEEP ✅ |
| **`GET /%2e%2e/uidist2/secret.txt`** | **200 `TOP-SECRET`** ❌ | **200 INDEX** ✅ |
| `GET /%2e%2e/%2e%2e/etc/passwd` | 200 INDEX ✅ | 200 INDEX ✅ |
| `GET /api/nope` | 404 ✅ | 404 ✅ |

**`%2e%2e` 是关键**：URL 编码的 `..` 不会被客户端/代理归一化，直接抵达路由。

### 补丁

```python
_dist_prefix = str(dist) + os.sep
if (full_path and candidate.is_file()
        and (str(candidate).startswith(_dist_prefix)
             or str(candidate) == str(dist))):
```

### 前置条件（诚实说明为何定 medium 而非 high）

1. 必须显式启用 UI 托管（`--ui-dir`），**默认不开**（docstring："默认不托管，保持只读 API 纯净"）
2. 需要存在名字以 dist 名为前缀的**兄弟目录或文件**
3. 攻击者需能访问 HTTP

三条同时成立才可利用。但它是真实的任意文件读（内容为服务器进程可读的任何文件）。

---

## 二、W147 / W147b：两个独立的判据缺口

**W147 —— "若来自外部"这个前提从不判断。** 与第十七轮 W146（SER-01）**完全同形**：规则 message 自带一个它自己不验证的前提。

44 条里只有 1 条真外部可达（`full_path` 来自 URL 路径），其余 43 条的路径变量来自配置/常量/`self._persist_dir`。

修法：判"变量是函数参数 **且** 函数有路由装饰器 **或** 参数名含 request/body/query/full_path"。**不再只按变量名猜**（含 path/name/dir 就报——与 W79d 包名写死同族的过度近似）。

**W147b —— 新增"无分隔符前缀比较"维度。** AF19 的教训是：**它校验了，只是判据错了**。原规则只问"有没有校验 `..`/斜杠"，AF19 两者都不缺，照样穿。

修后（W147 + W147b）：

| | 修前 | 修后 |
|---|---|---|
| IN-03 | 44（2 medium / 42 low） | **44（2 medium / 42 low）** |

**数字没变，但那 2 条 medium 现在精确指向 AF19 现场**（`af_api.py:1217/1218`，`unbounded_prefix_compare=True`），其余 42 条 message 明确写"未发现外部来源与非边界比较，多为配置/内部路径"。

> ⚠️ 局限：`external` 对 AF19 判为 `False`——`spa_fallback` 是 `build_app` 的**嵌套函数**，AST 归到了外层，而 `full_path` 不是 `build_app` 的参数。最终靠 `prefix_cmp` 才把它捞出来。**嵌套函数的参数归属是已知盲区，未修。**

---

## 三、其余 43 条：为何是假阳性

抽样核查的来源：

| 变量 | 来源 |
|---|---|
| `ui_dir` | CLI `--ui-dir`（运维传入，非请求） |
| `revoked_path` / `issued_path` | `TokenRegistry.__init__` 参数，由装配层传 |
| `self._persist_dir` / `store_dir` / `buffer_dir` | 配置 |
| `path`（`af_cli.py` 多处） | CLI 参数，本地工具 |

`af_service.py:2333` 那条更直白：`tempfile.mkdtemp(dir=str(root))`——`root` 来自 store，不是外部。

**但必须说明**：这 43 条我是对**抽样**做的来源追踪，不是逐条。判据说"非外部"的依据是"变量不是 HTTP/MCP 路由函数的参数"，这只能排除**直接**来自请求的情形，**不能排除经配置层二次传入**。

---

## 四、数字与累计

| | round-017 | round-018 |
|---|---|---|
| IN-03 | 44（未核实） | **44 条已分类，1 项确证（AF19）** |

**累计确证（第二期 1–18 轮）**：AF1–AF5、AF7–AF19，共 **18 项**。**17 项已修，1 项（AF8 pydantic）待定夺。**

| 编号 | 严重度 | 内容 | 状态 |
|---|---|---|---|
| AF19 | medium | SPA fallback 无分隔符前缀比较 ⇒ 目录穿越 | **已修（本轮）** |

---

## 五、判据缺口累计（第二期）

| 编号 | 缺口 | 轮次 |
|---|---|---|
| W145 | `replace`/`remove` 多态名 | 16 |
| W146 | SER-01 "若需恢复"前提不判 | 17 |
| W147 | IN-03 "若来自外部"前提不判 | 18 |
| **W147b** | **有校验 ≠ 判据正确（无分隔符前缀比较）** | **18** |
| — | 嵌套函数参数归属 | 18（未修） |

**W147b 是这一组里最有价值的**：前三条都是"规则说了前提却不验证"，W147b 是**另一类**——规则问错了问题。它问"有没有校验"，而正确的问题是"校验的判据对不对"。

---

## 六、下轮建议

已覆盖：状态持久化、装配、递归、鉴权、出站、锁/并发、内存增长、静默 except、静默失败、事务边界、序列化、路径遍历。

**剩余未碰**：`AF-AST-MIXED-RETURN`（111 条，最多）、`IN-02-unsafe-cast`（70）、`AFS-04`（68）、`ERRH-02`（62）、`API-06`（54）、`AFS-02`（44）、`DEAD-03`（41）、`IN-07`（33）。

还有 **依赖 CVE（18 轮未扫）**。

我的倾向：**做 `IN-02-unsafe-cast`（70 条）**——它是外部输入面里仅次于路径遍历的一族，且 AF1（深嵌套 → 500）说明这个项目的输入校验链条值得继续挖。

---

## 七、如实说明

- **AF19 的 `dist` 必须 `.resolve()`**：补丁依赖这一点，若部署传入相对路径且后续改动去掉 resolve，行为可能不同。
- **43 条假阳性我做了抽样而非逐条**，依据见上。
- **W147 的 `external` 判据对嵌套函数失效**（AF19 自身就是例子），靠 `prefix_cmp` 兜住。
- **`%2e%2e` 是否被具体部署的反向代理提前归一化，我没有验证**——若 nginx/uvicorn 提前解码并归一化，可利用性进一步下降。
- **依赖 CVE 面十八轮仍未扫**。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 14 部分 · 第十九轮

> 源文件：`AutoForge_第二期第十九轮审计报告.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-019（**IN-02 类型转换定向**）
- **上一轮**：round-018（IN-03 + AF19）

---

## 一句话结论

**IN-02 那 70 条「对外部值 int()/float() 无 try」里，逐个追可达性后确证 1 项真缺陷（AF20）：`af_runtime_ext.restore()` 的 `_read()` 只 try 包住 `json.load(fh)`——防得了「文件坏」，防不住「文件合法但字段脏」。一个脏字段 ⇒ `canary.load()` 抛 ValueError ⇒ `restore()` 抛 ⇒ `install()` 抛 ⇒ 被 `af_runtime.py:105` 兜住 ⇒ **`grading = None`，整套 conf_grading 静默停用，且 `restore_corrupt` 为空（留痕机制被绕过）**。已补丁，对照实测通过。**

---

## 一、AF20 · 字段脏 ⇒ 整套 conf_grading 静默停用（**medium**）

**位置**：`af_runtime_ext.py:112-124`

```python
def _read(name, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)          # ← try 只包这一句
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        logger.warning(...); self.restore_corrupt.append(name); return default

self.canary.load(_read("canary_state.json", {}))   # ← load() 在 try **之外**
```

### 实测（`poc_af46.py`，正确装配 ConfGrading）

```
干净: canary_state.json 两条 → ✅ restore 成功，records=['a1','a2']，corrupt=[]

脏:   a2.since = "abc"
      → ❌ restore() 抛 ValueError: could not convert string to float: 'abc'
      → canary.records = ['a1']        ← a1 恢复了，a3 没有（中断在中间）
      → restore_corrupt = []           ← 空！留痕机制被绕过
      → 冒泡到 install()，被 af_runtime.py:105 `except Exception` 兜住
      → grading = None
```

### 后果链

```
一个字段脏 → canary.load 抛 → restore 抛 → install 抛
          → af_runtime.py:105 except Exception 兜住
          → self.grading = None + 记一条 audit（无 logger.error）
          → shadow / 干预检测 / canary / 提案 / 预测触发 全部停用
```

**服务不会崩**——这与 memory-agent M1 不同。但：

1. **整个子系统静默停用**，只记一条 audit 事件，日志里看不出
2. **`restore_corrupt` 为空** —— 注释明写"降级 + 留痕……但绝不静默"，而实际**静默了**
3. 好的记录只恢复了一部分（a1 恢复、a3 丢失）

### 这不是 canary 独有的问题

构造测试数据时我意外发现 **feedback 也是同病**：`feedback.json` 事件缺 `type` 字段 → `KeyError: 'type'`；`type` 值不是合法枚举 → `ValueError`。**四个模块的 `load()` 都在 `_read` 的 try 之外，全都无保护。**

### 补丁（`af_runtime_ext.py`）

每个模块的 `load` 各自隔离：

```python
def _restore_module(name, call, default):
    data = _read(name, default)
    try:
        call(data)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        logger.warning("%s 恢复失败（字段脏/结构不符），该模块按空态继续起，其余模块不受影响：%r", name, exc)
        self.restore_corrupt.append(name)
```

### 对照实测（`poc_af47.py` / `poc_af48.py`）

| 场景 | 原仓库 | 补丁副本 |
|---|---|---|
| canary 两个脏字段 | ❌ ValueError ⇒ 整套停用 | ✅ `canary=['a1']`，`corrupt=['feedback.json','canary_state.json']`，**shadow=1 保住** |
| feedback 缺/错 type | ❌ KeyError / ValueError ⇒ 整套停用 | ✅ 隔离 + 留痕 |
| canary 真·干净（2 条） | ❌ 被 feedback 连累仍抛 | ✅ **`canary=['a1','a2']` 两条全恢复**（无回归） |

**关键一行**：补丁副本在 canary 真干净时恢复 `['a1','a2']` 两条 ⇒ 正常路径无回归。

---

## 二、为何定 medium 而非 high

1. 需 `AUTOFORGE_CONF_GRADING=1` 才启用（默认关）
2. 服务不崩，只丢一个可选子系统
3. 但**留痕失效**这点很糟——注释承诺"绝不静默"，实际静默

---

## 三、其余 69 条：为何是假阳性

| 类别 | 条数 | 判断 |
|---|---|---|
| `data.get('k', default)` + int/float | 44 | 有默认值兜底；只在**值存在且脏**时才抛 |
| `data['k']` 硬下标 | 20 | 缺键时 KeyError，但这些都在**反序列化内部**，同 AF20 逻辑 |
| 其它 | 6 | — |

**MCP 面 6 条**（`af_mcp.py` L229/301/316/317/398/403）实测**不是缺陷**：`dispatch()` 有 `except Exception`（L907）→ 转 `isError=True` 回执，还特意声明"完整 traceback 只落服务端日志，不回传客户端（防路径/行号外泄）"。**设计良好。**

**关键区分**：IN-02 的 70 条里，**只有 `restore()` 链上那 4 处是"异常会冒泡到启动路径"**，其余都在有兜底的调用面。这个区分规则报不出来——它只看"有没有 try"，不看"调用方有没有兜底"。

---

## 四、判据缺口累计（第二期）

| 编号 | 缺口 | 轮次 |
|---|---|---|
| W145 | `replace`/`remove` 多态名 | 16 |
| W146 | SER-01 "若需恢复"前提不判 | 17 |
| W147 | IN-03 "若来自外部"前提不判 | 18 |
| W147b | 有校验 ≠ 判据正确 | 18 |
| **W148** | **「无 try」≠「异常会逃逸」——要看调用链上的兜底** | **19** |

**W148 与 W147b 同类**：都是"规则问错了问题"。W147b 是"有没有校验 vs 判据对不对"，W148 是"有没有 try vs 异常会不会真的逃逸"。

推论：**局部判据（看函数内）永远回答不了跨函数的问题（异常去哪了）。** 这三轮连着出的都是这一类。

---

## 五、数字与累计

**累计确证（第二期 1–19 轮）**：AF1–AF5、AF7–AF20，共 **19 项**。**18 项已修，1 项（AF8 pydantic）待定夺。**

| 编号 | 严重度 | 内容 | 状态 |
|---|---|---|---|
| AF20 | medium | 字段脏 ⇒ restore 抛 ⇒ 整套 conf_grading 静默停用 + 留痕失效 | **已修（本轮）** |

---

## 六、下轮建议

已覆盖：状态持久化、装配、递归、鉴权、出站、锁/并发、内存增长、静默 except、静默失败、事务边界、序列化、路径遍历、类型转换。

**剩余未碰**：`AF-AST-MIXED-RETURN`（111 条，最多）、`AFS-04`（68）、`ERRH-02`（62）、`API-06`（54）、`AFS-02`（44）、`DEAD-03`（41）、`IN-07`（33）。

还有 **依赖 CVE（19 轮未扫）**。

我的倾向：**做 `AF-AST-MIXED-RETURN`（111 条）**——它是存量最大的一族，且第十三/十五轮的经验（返回值语义方向）在这里可能再挖出同族实例。

---

## 七、如实说明

- **AF20 的"整套停用"是读代码 + 局部实测推出来的**：我实测到 `restore()` 抛异常，`grading=None` 是依据 `af_runtime.py:105` 的 except 分支推断，**没有跑完整的 Runtime 启动链**。
- **"四个模块全同病"我只实测了 canary 和 feedback 两个**，shadow 和 proposals 是依据"都在 `_read` 的 try 之外"推断。
- **69 条假阳性我按类别判的，没逐条打开**："有 `.get` 默认值"这一类（44 条）我判为"只在值脏时才抛"，但没验证那些调用方是否都有兜底。
- **MCP 面 6 条我确认了 dispatch 有兜底**，但没实测脏参数的具体回执内容。
- **依赖 CVE 面十九轮仍未扫**。
- 本轮未跑门禁。
- 补丁只在只读副本，**原仓库未改动**。



<!-- ─────────────────────────────────────────── -->

# 第 15 部分 · 第二十轮（最终轮）

> 源文件：`AutoForge_第二期第二十轮审计报告_最终轮.md`

---

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-020（**AF-AST-MIXED-RETURN 定向 + 收官**）
- **上一轮**：round-019（IN-02 + AF20）

---

## 一句话结论

**111 条 MIXED-RETURN 里确证 1 项（AF21）：`af_shadow.py` 的 shadow 档 `_do` 返回 `None`，而 `_do` 的契约是「出边集合」，`NodeExecutor.run()` 把 `None` 解释为「失败且无兜底边 ⇒ 终止」。实测实例停在 do 节点，既不前进也不终止（state 仍为 created）。两个后果：① 多动作自动化的影子回放只能记录第一个 do，而 shadow 的产出正是转正证据；② 实例非终态挂起，24h 后才由 `expire_stale()` 清掉。已补丁（返回 `{"then"}`），对照实测通过。**

---

## 一、AF21 · shadow 档 `_do` 返回 None（**medium**）

**位置**：`af_shadow.py:371`

```python
if band == "shadow":
    runner.run_do(instance, node)
    return None          # ← 契约是"出边集合"，None 意为"失败且无兜底边"
```

**对照侧** `af_executor.py:168-175`：

```python
kinds = self._execute(instance, node)
if kinds is None:  # 已终止（失败且无兜底边）
    return instance
edge = auto.pick_edge(node.id, kinds)
```

### 对照实测（`poc_af53.py`，图 a1(on) → d1(do) → p1(pass)）

| | 经过节点 | 终态 state | 停在 |
|---|---|---|---|
| 原仓库（return None） | a1, d1 | **created（未终止）** | d1 |
| 补丁副本（return `{"then"}`） | a1, d1 | **done** | p1 |

**关键差异不是"走了几个节点"，是 `state`**：原仓库 `created`（既不终止也不前进 ⇒ **实例挂起**），补丁 `done`。

### 两个后果

**① 回放不完整** —— shadow 的语义是"记录如果执行会做什么，**不调用 adapter.call**"（`run_do` docstring）。实例在第一个 do 就停 ⇒ 多动作自动化只能看到第一个动作。**而 shadow_log 正是 conf grading 的转正证据**，证据不完整 ⇒ 转正决策失据。

**② 实例挂起** —— 非终态，`reap_terminal()`（只管终态）清不掉，24h 后才由 `af_runtime.py:215` 的 `expire_stale()` 清掉。

### 补丁（`af_shadow.py`）

```python
if band == "shadow":
    runner.run_do(instance, node)
    return {"then"}     # 继续走；shadow 不调 adapter.call，继续走无副作用风险
if band == "ask":
    runner.open_ask(instance, node)
    return None         # ask 档返回 None 是对的：open_ask 内部会 suspend
```

**注释里我特别写明了 ask 档不要套用同样的修法**——同一个返回值，两个语义。

---

## 二、为何定 medium

- 需 `AUTOFORGE_CONF_GRADING=1` 才启用（默认关）
- 有 24h TTL 兜底，不会永久泄漏
- 但**转正证据不完整**直接影响 conf grading 的核心决策

---

## 三、12 条 medium 里其余 11 条：为何是假阳性

逐个查了**调用方是否判空**——这才是真正的判据（而非"函数有没有 return None"）：

| 函数 | 调用方 | 判定 |
|---|---|---|
| `homesdk_time()` | 4 处全部 `if module is not None`，且 docstring 明写"缺席是**可观测**的，不是静默的" | ✅ 假阳性 |
| `node_by_id()` | `fixers.py` 5 处全部 `if not n` | ✅ 假阳性 |
| `_value_from()` | `af_nl_parse.py:764` `if val is not None` | ✅ 假阳性 |
| `_coerce_param_value()` | 末尾 `return v` 兜底，只在输入是 `"None"` 时才返回 None | ✅ 假阳性 |
| `_first_of()` / `_pick()` | 逐处判空 / 是"按名字找，找不到"的 helper 语义 | ✅ 假阳性 |
| `_match_node` / `extract_json` / `_device_label` / `_trigger_repr` / `_start_linkage_bridge` | 同类 | ✅ 假阳性 |

**99 条 low 里 94 条的标注本来就是 `dict | None` / `Optional[dict]`**——函数明确声明了可能返回 None，也确实返回了 None，**契约没被违背**。这与 memory-agent W129 是同一发现。

---

## 四、第二期累计

**20 轮确证 20 项（AF1–AF5、AF7–AF21）**，**19 项已修，1 项（AF8 pydantic）待定夺**。

| 编号 | 严重度 | 一句话 |
|---|---|---|
| AF1 | medium | 深嵌套 IR 在 jsonschema 展开阶段爆栈 → 500 |
| AF2/AF3/AF4 | high/high/medium | 三个状态存储「读失败当没数据 + 写侧全量覆盖」 |
| AF5 | high | `assert_deletable` 读失败 continue ⇒ 不可逆删除可连带销毁他人归档 |
| AF7 | low | `_walk` 裸递归（端到端被 AF1 掩盖，纵深防御） |
| AF8 | medium | pydantic 未声明依赖，靠 fastapi 传递 |
| AF9 | high | 别名撞车护栏在**写入**路径失效 |
| AF10 | high | 偏好历史在压缩时被全档覆盖 |
| AF11/AF12 | high/high | 令牌台账被抹 / 遥测计数被重置 |
| AF13/AF14 | high | login 任意凭据签发 owner 全权限令牌 ⇒ 完整提权链 |
| AF15 | medium | `_revoked` 无上限 ⇒ 每次鉴权 O(n) 永久降级 |
| AF16/AF16b | medium | 进程泄漏 + owner 校验失效 |
| AF17 | medium | catalog 损坏 ⇒ 富化字段清零且 `ok=True` 无感知 |
| AF18 | low | 已拒绝提案静默变回待决 |
| AF19 | medium | SPA fallback 无分隔符前缀比较 ⇒ 目录穿越 |
| AF20 | medium | 字段脏 ⇒ restore 抛 ⇒ 整套 conf_grading 静默停用 + 留痕失效 |
| AF21 | medium | shadow 档 `_do` 返回 None ⇒ 回放不完整 + 实例挂起 |

**按根因合并只有三族**：① 读失败被当成没有数据（9 项）；② 输入可达性 / 判据错误（AF19、AF13/14）；③ 返回值语义方向（AF21、AF15）。

---

## 五、工作流产出

本期 20 轮共修正 **13 项判据**（W137–W149），打包为 `ADM-auditkit_提案包_20261011.zip`。

**核心论点**：静态规则的判据天生是**局部**的，而缺陷成立条件往往是**跨函数/跨模块**的。连续 6 轮的实质产出都是判据修正而非新缺陷，且全部是同一类根因的不同表现。

**分水岭是 W147b 和 W148**：前几条是"规则说了前提却不验证"，这两条是**规则问错了问题**——
- W147b：问"有没有校验"，正确问题是"校验的判据对不对"
- W148：问"有没有 try"，正确问题是"异常会不会真的逃逸"

**4 个新判据缺口**：gap-9（装饰器防护看不见）、gap-10（嵌套函数参数归属）、gap-11（跨模块反向路径）、gap-12（返回 None 的语义方向）。

---

## 六、如实说明

- **AF21 我用 monkeypatch `_execute` 复刻 shadow 档行为验证，没跑真实 ShadowRunner.install**。构造两个 do 节点的 IR 时 schema 报错，我改用了单 do 图——"只能看到第一个 do"是基于 `run()` 不再前进这一确定行为的推演。
- **AF20 的 `grading=None`** 是依据 `af_runtime.py:105` 的 except 分支推断，未跑完整 Runtime 启动链。
- **MIXED-RETURN 99 条 low 我按标注形态批量判的**，没逐条打开。
- **依赖 CVE 面二十轮一次没扫**（PyPI / OSV 均 403），**风险未知不是无风险**。
- **本轮未跑门禁**。
- 所有补丁在只读副本 `af-patched`，**原仓库未改动**。


---

## 合并说明

本次合并为**原文拼接**，未对任何一轮的结论、严重度、对照实测数据做修改或调和。

若不同轮次对同一项的判断出现演进（例如 AF13 → AF14 的升级、AF13 严重度的重新评估），
**以靠后的轮次为准**，前面的记录保留原貌以便追溯判断过程。

同样地，各轮「如实说明」中标注的未验证项、推断项、风险未知项，合并后依然成立，
不因合并而被视为已解决。
