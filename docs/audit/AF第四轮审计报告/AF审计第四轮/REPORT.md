# AutoForge 第四轮审计报告（稳定性 / 功能性）

> 校验器：`coverage-ledger` PASS（17 单元）｜`findings` PASS（2 记录）｜状态 `complete`

## 先回答"还能找到吗"

**能，但产出强度明显下降。**

第四轮找到 2 条（中 / 低-中），且都不是崩溃级。对比前三轮：P0 ×2 → 高 → 中高 → 中/低。

这是符合预期的衰减——每轮我都刻意换面，先啃风险最集中的区域，剩下的面本身风险密度更低。而且这个项目**测试全绿（1277 passed）、静态闸扎实**，高密度缺陷本就不多。

## 本轮找到的

**① 设备目录静默截断到 120 送 LLM**（`af_orchestrator.py:916`）

`list(catalog)[:120]` 按原始字典序截断，无 `truncated` 回报、无相关性排序。而同项目的 `af_catalog.py` 有规范分页纪律，两者直接矛盾。典型家庭 300~1500 实体 → LLM 只看得到 8%~40%，其余设备只能猜 entity_id，且外层 `try/except: pass` 让失败静默降级。

**② undo 各域恢复策略不一致**（`af_undo.py`）

灯 `fail-soft`（丢 brightness 字段仍 turn_on）vs 空调/窗帘 `fail-closed`（整体放弃）。同为"快照值非法"三种策略。严重度有限，属维护隐患。

## 一条重要的正面结论

我对声明的 **26 类静态闸逐项做了漏检探测，全部有效，无一漏检**。

两个"疑似漏检"核实后是我自己构造错了：
- `lock.unlock` 报 `L2_NEEDS_CONFIRM` 是**对的**（lock 属 L2，L3 是 shell_command/python_script 等）
- `conf=0.3` 未报是因为 do 节点目标要写 `params.entity_id` 而非顶层 `target`

顺带确认 `target_entities()` 对 `params.target.device_id`/`area_id` 有前缀标记，**P0-5 已修**。

**G2 静态闸是本项目实现质量最高的部分。**

## 本轮证伪

全仓 311 处 `except`、90 处静默吞噬——逐处核实后**大部分是合理的 best-effort 容错**（跨平台 fsync 差异、rmdir 非空等），不是缺陷。`af_persist` 租约仲裁完整、naive→UTC 有处理、instance_id 无碰撞。

## 跨轮提醒

`af_scheduler.py:343` 的 `instance.id` —— **连续三轮确认未修复**，仍是唯一确定的 P0。
