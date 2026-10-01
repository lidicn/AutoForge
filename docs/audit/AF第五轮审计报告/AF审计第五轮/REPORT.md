# AutoForge 第五轮审计报告（稳定性 / 功能性）

> 校验器：`coverage-ledger` PASS（14 单元）｜`findings` PASS（2 记录）｜状态 `complete`

## 核心发现

**`af_preference.py`：`PreferenceModel.record()` 每次调用全量重写整个偏好库，且无记录上限 → O(N²)。**

实测单次 `record` 耗时随记录数严格线性上升：501条→7.58ms、1001→14.73ms、2001→29.25ms、4001→58.73ms；累计插入 4001 条耗时 **119.51 秒**。落盘 158KB→1268KB，约 326 字节/条。`_records` 无任何裁剪（grep `maxlen`/`MAX_`/`prune`/`evict`/`expire` 命中 0 次）。

`af_preference` 在前四轮均为 `out_of_scope`，**本轮首次审计即命中 P1**。

## 第二项（回归类）

`af_bus` 的 `_last_state`/`_last_accepted`/`_changes` 键永不摘除。实测旧设备下线后旧键永久残留（200→400）。与第一轮 P1（`AuditLog`/`bus.emitted`/`_time_fired` 无上限）同属**"只增不减"**一族，计为回归而非新发现。

## 本轮证伪

**`af_predict` —— 无缺陷。** 跨午夜窗口、负 start、inf/nan、naive datetime、`window_minutes=0/1e9`、`automation_id` 非法全部正确处理。参数校验是全项目最严格的。

**`af_bus` 去重与熔断 —— 正确。** `_seen` 有 LRU 上限 4096；熔断参数合理；自定义事件跳过熔断是源码注释明确的设计决策。

## 一个方法论上的修正

第四轮我判断"边际收益会持续下降"。**第五轮推翻了这个判断。**

区别在：第四轮审的是已审面的邻近区域，第五轮转向**前四轮完全没碰过的模块**。

所以"还能找到 bug 吗"的准确答案不是"强度衰减"，而是**"取决于还剩多少处女地"**。按此，下一轮应直打 `af_evo`(1474) + `af_service`(2102) —— 合计 3576 行从未审计。
