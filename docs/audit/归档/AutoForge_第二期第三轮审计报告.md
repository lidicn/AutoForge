# AutoForge 第二期第三轮审计报告

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-003
- **上一轮**：round-002（确证 AF2/AF3/AF4）

---

## 一句话结论

**本轮新确证 1 条真缺陷（AF5，不可逆删除的 fail-open），并证伪 1 条（CONC-08 锁覆盖，32 线程 × 5 轮实测无一丢失）。数字从 1022/52 收敛到 1013/43——降下来的 9 条全是判据修正，不是缺陷消失了。**

---

## 一、确证缺陷

### AF5 · `assert_deletable` 读失败 `continue` ⇒ 不可逆删除可连带销毁他人归档（**high**）

**位置**：`af_store.py:182`

**形态**：删除前遍历目录里每条 `v*.json` 核对归属，坏记录 `continue` 跳过。

```python
for path in sorted(directory.glob("v*.json")):
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue   # ← 读不出来 = 当这条不属于别人
```

**为什么危险**：`DELETE /api/automations/{name}` 与 `overwrite` 导入会 `rmtree` / 逐条 `unlink` 整个目录。**删除不可逆**，只要目录里混进过别人的版本记录，删下去就是连带销毁另一条归档。

**源码注释写的是**"坏记录由加载路径各自处置，这里不为它放行别名"——**注释自洽但行为是 fail-open**：它确实"不为坏记录放行别名"，但也"不为坏记录拒绝删除"。

**对照实测**（`af_poc/poc_af5.py`）：

| 场景 | 原仓库 | 补丁副本 |
|---|---|---|
| v2 完好（对照组，证明构造有效） | 拒绝：ArchiveNameConflict | 拒绝：ArchiveNameConflict |
| **v2 损坏**（读不出归属名） | **放行删除（fail-open）** ❌ | 拒绝：StateCorrupt ✅ |

**状态**：补丁副本**第一轮已修**（`raise StateCorrupt`）。原仓库规则报 FO-01 是对的。

---

## 二、证伪

### CONC-08 锁覆盖不一致 ×5 → **0**（假阳性）

**规则报**：`PairCodeStore`/`AuthCodeStore`/`TokenRegistry`/`PremiereStore`/`TrialStore` 的 `_load` 裸写同一成员。

**实测**：32 线程 barrier 同发起 `create()`，**5 轮全部 32/32，无一丢失**。

**根因**：`_load()` 是 `self._codes[c["code"]] = c`——**合并语义**，从不清空容器。共享 dict 下两个线程各自合并后写回仍是并集，不丢。

**规则只看"裸写了同一成员"，看不出是替换还是合并。**

这是 CONC-08 判据缺口的**第三次**：
- doubao-butler 第七轮：`_load` 只在 `__init__` 调（无并发窗口）
- memory-agent 第十一轮：同款
- **本次**：`_load` 是合并而非替换

前两次根因相同，这次不同——但**同源：判据只看结构，不看语义**。

**另证伪 1 条**：`TokenRegistry._purge_expired_locked` 名字带 `_locked` 后缀（docstring 明写"须持锁"），唯一调用点在 `with self._lock:` 内（af_auth.py:334）。规则只看"函数自己有没有 with self._lock"，看不见"调用方持锁"——与 W62（timeout 在被调函数里）同族。

---

## 三、工作流迭代

| 编号 | 内容 | 效果 |
|---|---|---|
| **W138** | 新增 `_is_merge_write()`：区分裸写方法是**合并**（`self.X[k]=v`，无重建）还是**替换**（`self.X={}` / `.clear()`）。合并 ⇒ 不算竞态 | CONC-08 5 → 1 |
| **W138b** | 新增 `_requires_caller_lock()`：`_locked` 后缀 + 所有调用点都在 `with self._lock` 内 ⇒ 不算裸写 | 1 → 0 |
| **W138c** | 修 Attribute 调用识别（`self._purge_expired_locked()` 的 func 是 Attribute 不是 Name） | — |
| **W138d** | 修**我自己引入的缩进错误** | — |

### W138d 值得单独记

我按区间替换时，把计数器塞进了 `continue` 之后：

```python
if nm != meth:
    continue
    if id(n) in locked_nodes:    # ← 永远不执行
        calls_in_lock += 1
```

**后果**：W138b 完全不生效，而语法检查通过、AST 解析通过、输出看起来"规则改了但结果没变"（CONC-08 仍是 1）。

这是"工具静默失效"第 N 次，但**这次失效源是我自己的补丁**——与 M23 同族。

**能被发现全靠"结果没变时我先怀疑自己刚改的东西"**，而不是"规则本来就该这样"。

---

## 四、数字变化

| | round-001 | round-003 |
|---|---|---|
| 总命中 | 1022 | **1013** |
| high | 52 | **43** |
| 分析器 | 28 ok | 28 ok |

**降下来的 9 条 high 全部是判据修正**（AUTH-04 4 + CONC-08 5），**不是缺陷消失**。

---

## 五、如实说明

- **AF5 的补丁副本已修**（第一轮 F12 边缘实例），本轮确证的是**原仓库**行为。
- 剩余 43 条 high 中，已核验：DO 族（部分）、FO-01 ×3、CONC-08 ×5、AUTH 族、OUTB 族。**未核验**：ASM-01 ×11、GOD-01 ×5、RSC-05 ×3、TX-04 ×3、IN 族。
- **GOD-01 ×5 疑似同款假阳性**（规则报"read_text 排在前、护栏装在后"，但 `read_text`  самий 不是危险操作，且跨函数比较行号可能无意义）——未核验。
- **RSC-05 ×3 需重点看**：`_nnf()` / `_walk()` / `_walk_operand()` 未用已定义的 `MAX_EXPR_DEPTH`——这正是第一期 F2 的主题，需确认 F2 是否修完。这是下轮第一优先。
- 依赖 CVE 面仍未扫。
- 补丁只在只读副本，**原仓库未改动**。
- 本轮未跑门禁。
