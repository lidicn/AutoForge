# AutoForge 第二期第二轮审计报告

- **审计目标**：AutoForge（`lidicn/AutoForge`）
- **轮次**：round-002（第二期第二轮）
- **报告日期**：2026-10-10
- **上一轮**：round-001（命中 1022，确证 AF1，报告 `AutoForge_第二期第一轮审计报告.md`）

---

## 一句话结论

**Follow-up 三兄弟（AF2/AF3/AF4）全部确证为「读失败被当成没有数据 + 写侧全量覆盖」——与第一期 F8–F13 完全同族，但第一轮 stateguard 护栏清单漏了这三个文件。三个均已在补丁副本修复并实测：原仓库 3 条全部 `data_lost`，补丁副本 3 条全部 `guarded`（拒绝写入，现场保留于 `.corrupt-*`）。**

---

## 一、本轮命中与确证

| 编号 | 位置 | 简述 | 严重度 |
|---|---|---|---|
| **AF2** | `af_fire_recorder.py:50` | `_load` 读失败置 `self._records = {}`，**无任何日志**；写侧 `_save` 全量覆盖 | **high** |
| **AF3** | `af_undo.py:286` | `_load` 有 `logger.warning` 但**照样返空**；写侧全量覆盖 | **high** |
| **AF4** | `af_pretrigger.py:122` | `_load` 读失败后 `return`，`_events` 保持为空；写侧 `_save` 全量覆盖（且在 `except OSError` 时 fail-open 仅打日志） | **medium** |

**三者的共同形态与第一期 F8–F13 完全一致，属于同一根因族的第 9–11 个实例。**

---

## 二、对照实测（原仓库 vs 补丁副本）

| | 原仓库 | 补丁副本 |
|---|---|---|
| AF2 fire_recorder | 播种 3 → 写后 1 ❌ **data_lost** | **guarded**（抛 StateCorrupt，现场保留）✅ |
| AF3 undo | 播种 3 → 写后 1 ❌ **data_lost** | **guarded** ✅ |
| AF4 pretrigger | 播种 3 → 写后 1 ❌ **data_lost** | **guarded** ✅ |

实测脚本：`/data/workspace/af_poc/poc_af2_final.py`

---

## 三、已打补丁的内容

三处均按 `af_stateguard` 的标准形态（与 af_auth/af_catalog 等既有 6 处完全一致）：

```python
# 读侧
except (OSError, ValueError) as exc:
    logger.warning(...)
    mark_poisoned(self, "_xxx_poisoned", path, f"...不是合法 JSON：{exc}")
    return

# 写侧
def _save(self):
    barrier(self, "_xxx_poisoned", path, "写入 xxx")
```

| 文件 | 置位属性 |
|---|---|
| `af_fire_recorder.py` | `_fire_poisoned` |
| `af_undo.py` | `_undo_poisoned` |
| `af_pretrigger.py` | `_pre_poisoned` |

---

## 四、后果（按业务影响排序）

- **AF3（undo）最严重**：`undo_log.json` 丢失 ⇒ **撤销功能直接失效**。用户刚部署了一批自动化，想撤销时系统说"没有记录"。
- **AF2（fire_recorder）**：首演/触发计数清零 ⇒ 防重放与频次控制退化为冷启动。
- **AF4（pretrigger）**：`pretrigger_history` 丢失 ⇒ 预测器 `learn` 的历史全没，行为预测退化。

---

## 五、本轮教训：PoC 判定口径错了两次

**第一次**：PoC 把 AF4 的 `guarded` 误判成 `data_lost`——`_save()` 抛 `StateCorrupt` 后我读盘失败 → `after = -1` → `before > after` 成立。

**根因**：**「拒绝写入」与「丢数据」在「盘上内容变了」这一观察下无法区分，必须靠异常类型区分。**

**第二次**：AF3 的 `record()` **内部就调 `_save()`**，我把 try 只套在 `_save()` 上，异常从 `record()` 那行冒出去。

这两次与 memory-agent 第 13/17 轮的探针误判是同一形态——**探针的判定口径有缺陷时，会把"修好了"判成"没修好"，而且看起来完全正常**。

**已修**：判定统一为「捕获 `StateCorrupt` ⇒ guarded」；调用点也包进 try。

**推论**：写 PoC 时必须先问「这个类的写接口是哪一个、它内部会不会自己落盘」。我三次都栽在"以为 `_save()` 是唯一的写入入口"。

---

## 六、第一轮遗留、本轮仍未解决

| 项 | 状态 |
|---|---|
| AF1 补丁对 expr 深度 ≥800 | 探针侧 `json.dumps` 先 RecursionError，服务端行为**未观测** |
| AF1 阈值 `_MAX_SCHEMA_DEPTH=64` | 会抢在 expr 预算（`MAX_EXPR_DEPTH=32`）之前触发，错误信息从详细的 `EXPR_INVALID` 变成粗略 400 —— **功能正确但阈值未与 MAX_EXPR_DEPTH 协调**，建议下轮对齐 |
| 一期台账编号 | 实际为 **F1–F16**（非 M1–M16，用户口述为编号误记），本报告沿用 F/AF 编号 |
| 52 条 high 中非安全类部分 | 未逐条分诊 |

---

## 七、如实说明

- AF2/AF3/AF4 的补丁**只在只读副本 `af-patched`，原仓库未改动**。
- 依赖 CVE 面仍未扫（PyPI/OSV 受限）。
- DO/FO 族剩余命中（DO-01 af_experience.py:61、DO-01 af_store.py:358、DO-03 af_catalog.py:499、DO-04 af_preference.py:528、TX-04 ×3）未逐条核验——它们所在的 5 个文件**已在第一轮护栏清单内**，但需确认是否真已修复。
- 本轮未跑门禁（时间用于 PoC 判定修正）。
