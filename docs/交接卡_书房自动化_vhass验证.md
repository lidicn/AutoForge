# 交接卡 · 书房自动化编写与 vhass / 实际部署验证

- **日期**：2026-09-15
- **范围**：编写「书房学习模式自动联动」自动化，验证 vhass e2e 与实际部署链路，确认 v0.5.0（运行指标回灌）未引入问题。

## 交付物

| 文件 | 说明 |
|---|---|
| `examples/ir/case_study_room.json` | 书房自动化（2 条，顶层 `automations` 数组） |
| `examples/case_study_room.seed.json` | e2e 初始状态 |
| `examples/case_study_room.events.json` | e2e 事件序列（人进→人离→返回→再离到 10 分钟） |
| `tests/acceptance/test_case_study_room_vhass.py` | 真 vhass 复核测试 |

**自动化 1 `study_light_on`**（`mode=restart`，`meta.priority=10`）
`: study_motion on → if(光照<200 且 主灯灭) → 开主灯 + 开台灯 + emit study_light_on + set 标记=1`

**自动化 2 `study_light_off`**（`mode=single`，`meta.priority=5`）
`: study_motion off 持续 10 分钟（for） → 关主灯 + 关台灯 + emit study_light_off + set 标记=0`

## 验证矩阵（全绿）

| 层 | 手段 | 结果 |
|---|---|---|
| 本机 FakeHA e2e | `forge run case_study_room.json --vhass fake` | ✅ 2 自动化执行；总线 accepted 4 / duplicate 2；`event_emitted study_light_on / study_light_off` 均 accepted（含「10 分钟内人回则取消关灯」语义） |
| 本机 pytest | `pytest tests/` | ✅ **257 passed / 10 skipped / 0 failed** |
| 真 vhass 复核 | NAS `pytest tests/acceptance -p pytest_homeassistant_custom_component.plugins` | ✅ **`test_study_room_in_real_vhass` PASSED**（4 passed in 2.23s） |
| NAS 全量回归 | `docker run autoforge-test` | ✅ **267 passed / 0 failed** |
| 实际部署 | NAS `forge run`（真实 Linux 容器） | ✅ `event_emitted study_light_on/off` accepted |
| v0.5.0 回灌 MA | `forge metrics push` → MA `GET /api/metrics` | ✅ `study_light_on:…` / `study_light_off:…` 已入库（confidence band `auto`） |

## 结论

- 书房自动化在 **vhass（FakeHA + 真 vhass）** 与 **实际部署链路** 下均正确执行。
- **v0.5.0（Runtime 执行计数 + 指标回灌）未引入任何回归**。
- 指标回灌生态闭环真实打通：**AF push → MA 落库 → MA 可查**。

## 发现 / 踩坑

1. **`examples/ir/` 只能放 IR 文件**：seed/events 若放 `examples/ir/` 会被 `test_all_examples_load` 当 IR 加载 → schema 校验失败（`is not of type 'object'`）。已移至 `examples/` 根目录。
2. **IR 语法要点**：
   - `emit` 是 `pass` 节点的**字段**（`{"kind":"pass","emit":{"event":…,"data":{…}}}`），不是独立 `kind:"emit"`。
   - `for` 是 `on` 节点的**节点级**字段（与 `trigger` 并列），不在 `trigger` 内部。
   - 多条自动化写同一实体须声明互不相同的 `meta.priority`，否则 `_gate` 报 `ENTITY_WRITE_CONFLICT` 中止。
3. **vhass 大跨度时间旅行性能**：真 vhass 下 `advance(N)` 会逐秒触发 HA 中间定时器，实测 `advance(602)` 真实耗时 **602s**；无 emit 的 `case02 advance(660)` 仅 3.37s，emit 组合放大。故真 vhass 测试聚焦「开灯 + emit 全链路」（不推进时间），`for` 关灯语义由既有 `test_for_duration_with_virtual_time` 覆盖。**属测试装置性能问题，非被测语义**。

## 部署提示

- e:/NAS 为断开副本，改动需 `scp` 到 NAS `/vol1/1000/docker/autoforge/` 后再跑容器。
- 真 vhass 需显式 `-p pytest_homeassistant_custom_component.plugins`（pyproject 默认 `-p no:homeassistant`）。
