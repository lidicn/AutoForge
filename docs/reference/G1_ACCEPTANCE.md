# G1 验收用例映射表

> 版本：v1.0　日期：2026-09-14
> 关联：`KICKOFF.md` §6｜`IR_AND_RUNTIME.md` §17｜`NAMING.md`

> 8 条用例是 **G1 达标的唯一标准**。用例 1–5 验**逻辑语义**，用例 6–8 验**编译期拦截**。
> 全部在 vhass（基于 `pytest-homeassistant`）内运行；**G1 不接真实 HA**，HA 适配器默认 `dry_run=True`。

---

## 1. 用例 ↔ 实现点 ↔ 测试落位

| # | 场景 | 期望 | 主要验证的实现点 | 测试文件 | 样例 IR |
|---|---|---|---|---|---|
| 1 | 白天人在 + 光照<200 + 灯灭 | 开灯，断言 `light.study_main=on` | 边沿触发、多条件 AND、段内原子快照、`do` 单次下发 | `tests/acceptance/test_case01_day_light.py` | `examples/ir/case01_day_light.json` |
| 2 | 人离 10 分钟关灯，第 5 分钟人回 | **关灯被取消**（持续条件语义） | `for=10m` 边沿+持续、条件破坏即取消 | `tests/acceptance/test_case02_leave_for_10m.py` | `examples/ir/case02_leave_for_10m.json` |
| 3 | 夜晚（太阳历仿真）电脑开 | 挂灯开 | `sun.below_horizon`、vhass 原生太阳历、时间旅行 | `tests/acceptance/test_case03_night_sun.py` | `examples/ir/case03_night_sun.json` |
| 4 | 温度>27 + 门关 → 询问，60s 无应答 | 走 `on_timeout` → 静默，**实例不挂起** | `ask` 挂起、实例级 Timer、`on_timeout` 兜底 | `tests/acceptance/test_case04_ask_timeout.py` | `examples/ir/case04_ask_timeout.json` |
| 5 | 询问中"人离开" | 走 `on_cancel` → 取消，**已执行动作不回滚** | `on_cancel` 优先级最高、取消不回滚 | `tests/acceptance/test_case05_ask_cancel.py` | `examples/ir/case05_ask_cancel.json` |
| 6 | `http.post delete_all` | **编译期拦截**，不进仿真 | L3 高危动作 + HTTP 域名白名单 | `tests/acceptance/test_case06_l3_block.py` | `examples/ir/invalid_case06_delete_all.json` |
| 7 | 适配器配置带 `retry=3` | 编译期拦截（违反纯执行层契约） | 适配器配置禁止 IR 未定义策略参数 | `tests/acceptance/test_case07_retry_block.py` | `examples/ir/invalid_case07_retry.json` |
| 8 | A 开灯 → B 关灯 → A | **实体依赖图环检测报错** | 跨自动化实体读写依赖矩阵 + 环检测 | `tests/acceptance/test_case08_dep_cycle.py` | `examples/ir/invalid_case08_cycle.json` |

---

## 2. 用例 6/7/8 的断言要求（易错点）

**不能只断言"报错"**，必须同时断言 **IR 没有进入 Runtime / 仿真**：

```python
# 正确姿势
result = build(ir_path)              # 第一道闸
assert result.exit_code != 0          # 1) 非 0 退出
assert any(d.code == "L3_ACTION" for d in result.diagnostics)
assert sim_invoked is False           # 2) 未进入仿真（用 spy/标志位确认）
```

三者的拦截点不同，需在报告里体现来源：

| # | 拦截点 | 诊断码 |
|---|---|---|
| 6 | `af_scanner` 高危动作 + 出站白名单 | `L3_ACTION` / `HTTP_NOT_WHITELISTED` |
| 7 | `af_scanner` 适配器策略参数检查 | `ADAPTER_POLICY_PARAM` |
| 8 | `af_scanner` 跨自动化依赖矩阵环检测 | `ENTITY_DEP_CYCLE` |

---

## 3. 执行方式

```powershell
# 全量
.\.venv\Scripts\python.exe -m pytest tests/ -q

# 只跑验收
.\.venv\Scripts\python.exe -m pytest tests/acceptance -q

# 单条
.\.venv\Scripts\python.exe -m pytest tests/acceptance/test_case02_leave_for_10m.py -q

# CLI 三子命令冒烟
forge build examples/ir/case01_day_light.json
forge sim   examples/ir/case01_day_light.json
forge run   examples/ir/case01_day_light.json
```

---

## 4. vhass 与 FakeHA：什么时候用哪个

**首选真 vhass**（`pytest-homeassistant-custom-component`，钉住 `homeassistant==2026.9.2`），
因为 KICKOFF §3 红线要求"不自研仿真器"，且 HA 原生实体模型 / 事件总线 / **太阳历** / 服务调用能杜绝"仿真过了实际跑不通"。

### 4.1 本机落地结论（2026-09-14 实测）

| 环境 | 结论 |
|---|---|
| **Python 版本** | 该包 `python_requires=">=3.14"`，本机 3.12/3.13 装不上 → 已装 **Python 3.14.3**，用它建 `.venv314` |
| **Windows** | ⚠️ **真 vhass 跑不起来**：`homeassistant.runner` 会 `import fcntl`（POSIX 专有），pytest 插件加载阶段即 `ModuleNotFoundError` |
| **Windows 上的路径** | 8 条验收走 **FakeHA**（`af_vhass/fake.py`）：`StateProvider`/`TimeSource`/`Adapter` 三接口一致，Runtime 侧零改动；仅**用例 3** 的太阳历用内置桩（18:00–06:00 视为 `below_horizon`） |
| **Linux / WSL / Docker** | `tests/acceptance/test_vhass_native.py` 自动启用真 vhass（无需改代码） |
| **NAS（192.168.2.200）** | ✅ **已实测通过**：`docker build -f docker/Dockerfile.test -t autoforge-test .` + `docker run --rm -v /vol1/1000/docker/autoforge:/app autoforge-test` → **98 passed**（含 3 条真 vhass 复核） |

### 4.2 切换开关

- `tests/conftest.py` 的 `vhass_mode` 夹具读环境变量 `AUTOFORGE_VHASS=ha|fake`（默认 `ha`）
- `test_vhass_native.py` 自动 skip 条件：未装 `pytest_homeassistant_custom_component`，或 `sys.platform == "win32"`

### 4.3 在 Linux/WSL/Docker 上跑真 vhass

```bash
pip install -e ".[dev]"
pytest tests -q                      # 8 条验收 + 3 条真 vhass 复核
pytest tests/acceptance/test_vhass_native.py -q -m vhass
```

> 真 vhass 下的差异只有一处：`HassAdapter.call()` 采用**入队 + `await flush()`**，
> 因为 AutoForge Runtime 是同步的、HA 服务调用是异步的，在事件循环里同步 await 会死锁。
> 语义仍是"单次下发、无重试无降级"。

### 4.4 真 vhass 实测踩坑（2026-09-14，NAS 上逐条验证）

这四条都是"不踩就永远跑不通"的坑，已固化在 `af_vhass/harness.py` 里：

| # | 坑 | 现象 | 解法 |
|---|---|---|---|
| 1 | **裸 vhass 没有集成** | `ServiceNotFound: Action light.turn_off not found` | `register_device_services()` 按图里用到的 `domain.service` 注册桩服务；状态映射复用 `af_vhass.fake.SERVICE_STATE`，保证与 FakeHA 口径一致。**设备是假的，HA 内核（事件总线/服务注册/状态机/定时器/太阳历）全是真的** |
| 2 | **`dt_util.utcnow()` 是冻结的** | `async_fire_time_changed(hass, utcnow()+1h)` 后 `utcnow()` 纹丝不动，只涨了零点几秒；太阳历永不重算 | 必须用 `freezer.tick(timedelta)` 推进，再 `async_fire_time_changed(hass)`（不传 dt） |
| 3 | **两个时钟要同步推进** | HA 时钟走了但 `for`/`wait` 不到点（或反之） | `VhassHarness.advance()` 里 `freezer.tick()` 与 `runtime.clock.advance()` 成对调用 |
| 4 | **启用 `sun` 会留定时器** | teardown 报 `Lingering timer ... EntityPlatform._async_handle_interval_callback` | 这是 HA 自身行为，测试里覆盖 `expected_lingering_timers` 夹具返回 `True` |

> 另注：虚拟时钟起点必须取 `dt_util.utcnow()`（HA 的 now），不能用自己写死的起点——
> 否则时间旅行传进去的时刻相对 HA 是"过去"，同样不生效。

---

## 5. G4 验收映射（置信度分级自主 + canary）

> 追加于 2026-09-14（G4 收口）。G4 在 G2 编译期闸门之上补齐运行期自主决策，验收以单测 + `forge conf` 实测为准，不进仿真（canary 仅在非 dry-run 真机生效，见真机接线一节）。

| # | 场景 | 期望 | 主要验证点 | 测试落位 |
|---|---|---|---|---|
| G4-1 | case01 conf=1.0（auto 带）经 240h 衰减 + 一次人工干预 | 由 `auto(1.0)` 跌到 `shadow(0.817)`，不再自动下发设备 | `decay()` 指数衰减 + `record_negative()` 负样本回灌 | `tests/unit/test_af_conf.py` |
| G4-2 | `forge conf case01 --decay-hours 240 --intervene <id>` | CLI 输出级别由 `[auto]` 变为 `[shadow]` | CLI `--decay-hours` / `--intervene` 模拟 | `af_cli.py` `conf` 命令 |
| G4-3 | auto 带 + 节点标 `canary` + 非 dry-run 下发后实体状态未变预期 | 触发 `ENTITY_DRIFT` 审计 + 自动回滚（反向动作下发） + 走 `on_error` 软失效 | `CanaryGuard.has_drift/rollback` + `af_executor._do` | `tests/unit/test_af_canary.py` |
| G4-4 | shadow 带（0.6–0.85）或 conf<0.6 | 编译期即被 G2 闸门拦截写设备（不进 Runtime） | `SHADOW_WRITES_DEVICE` / `LOW_CONF_WRITES_DEVICE` | G2 验收已覆盖 |
| G4-5 | 阈值常量 `AUTO_MIN=0.85` / `SHADOW_LOW=0.60` | `af_conf.py` 与 `af_scanner.py` 共用同一组常量，改一处必须同步 | 常量一致性 | `test_af_conf.py` 边界用例 |

**契约刚性**：阈值 0.85 / 0.60 是 G2 编译期闸门与 G4 运行期决策的共同口径，任何一处改动必须同步另一处并补单测（G4-5）。
