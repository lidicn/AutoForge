# AutoForge 稳定性与功能性审计报告

> 审计日期：2026-10-11 ｜ 审计对象：`master` @ `1f9603c`（含 8 个未提交改动）
> 口径：**只报影响稳定性/功能性的真实缺陷**（数据损坏、静默失败、崩溃、死锁、契约破裂、门禁假绿）。
> 不报代码风格、命名、可读性、"建议改用 xx"、假设性未来风险。
> 每条标注**核实方式**：`🔬 已复现`（本机跑出错误输出）／`🧭 静态坐实`（读码＋grep 调用点，无歧义）／`📎 未独立复核`（分块审查结论，需再验一遍）。

---

## 〇、测试基线（本次审计的第一手证据）

`.venv314` 解释器跑 `pytest tests -q`：

```
8 failed, 3984 passed, 53 skipped, 65 subtests passed in 640.74s
```

失败的 8 条，按"单独跑"与"全量跑"两种姿势分开看，暴露出两个**互相独立**的问题：

| 测试 | 单独跑 | 全量跑 |
|---|---|---|
| `test_dcd_20261004_atomic_write_nine_sites.py::test_af_atomic_depends_on_nothing_but_stdlib` | **红** | **红** |
| `test_dcd_20261004_auth_limits.py::test_owner_face_still_sees_plaintext` | **红** | **红** |
| `test_dcd_20261004_auth_limits.py::test_third_party_write_token_gets_the_mask_not_the_code` | **红** | **红** |
| `test_v0_8_auth.py`（3 条：`test_legacy_single_token_backward_compat` / `test_multi_token_scope_grading` / `test_revocation_immediate_over_http`） | **绿** | **红** |
| `test_v1_4_token_expiry.py::test_expired_token_is_403_over_http` | **绿** | **红** |
| `test_fidelity_canon_vocabulary.py::test_self_referential_container_reports_instead_of_recursion_error` | **红** | **红** |

- 前 3 行 = **真缺陷**（F-01、F-02、F-10），不是测试问题。
- 第 4、5 行 = **测试环境污染**（F-03）：同一份代码，跑法不同结果不同，说明有测试在往 `os.environ` 里写东西且没清掉。
- 已确认 `tests/conftest.py` 里**没有** `deselect` / `xfail` 机制能解释这个差（只有 `vhass` / `integration` 两个 skip 标记）。

`gates.sh` 里 `grep pytest` 只命中注释和文档行，**没有一条 pytest 断言**；CI 只跑 `pytest tests/ -q`（`.github/workflows/ci.yml:27`）——所以这 8 条红在 CI 上是**必然红**，不是本机偶发。

---

## 一、P0 — 会造成数据损坏或鉴权整体失效

### F-01 🔬 已复现 ｜ `AF_ALLOW_NOAUTH=1` 把 scope 分级整体废掉

**位置**：`src/autoforge/af_api.py:366-369`

```python
def dep(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> TokenInfo | None:
    # 开发模式逃生舱：只要显式设置 AF_ALLOW_NOAUTH=1，不管有没有配置令牌都放行
    if os.environ.get("AF_ALLOW_NOAUTH", "").lower() in ("1", "true", "yes"):
        return None
    if not registry.enabled:
        ...
        if os.environ.get("AF_ALLOW_NOAUTH", "")...:   # ← 这一档现在永远走不到
            return None
```

**改动前**，逃生舱挂在 `not registry.enabled` **分支内部**（`:370-373`），语义是"没配令牌时才放行人工开发"。**改动后**被提前到函数第一行，语义变成"只要设了这个开关，**所有** scope 校验一律跳过"——因为 `dep` 直接 `return None`，`_scope_ok(scope, info.scopes)`（`:397`）整段被短路。

复现（已跑）：

```
A1 只读令牌(scopes=["read"]) + AF_ALLOW_NOAUTH=1 打 write 端点 /api/graphs/enable -> 200 {'ok': True, 'enabled': True, ...}
A2 无任何令牌              + AF_ALLOW_NOAUTH=1 打 write 端点 /api/graphs/enable -> 200 {'ok': True, 'enabled': True, ...}
```

`200` 就是**真的执行了启用操作**，不是"放行到后面的 400"。

**同一文件里另有一个端点仍用旧口径**（`:1129-1134`，SSE 配对端点）：

```python
local_escape = (not registry.enabled) and os.environ.get("AF_ALLOW_NOAUTH", ...)
```

它要求"未配置令牌"AND"开关打开"两条都成立。而 `requires()` 的改动后口径只要开关打开。**同一个进程、同一个环境变量，两条鉴权路径对"是否算已授权"给出不同答案。**

**根因**：把"未配置令牌的本地开发"和"已配置令牌的正常运行"两档合并成一档，同时把写侧的 scope 门槛一起拿掉。

**影响面**：全 130 个端点。`docker/docker-compose.api.yml:81` 本批新加了
`- AF_ALLOW_NOAUTH=${AF_ALLOW_NOAUTH:-}`，意味着**宿主机 `export AF_ALLOW_NOAUTH=1` 一次，NAS 生产实例的所有写端点即刻对全局域网无鉴权开放**——而 compose 里同时把 `AUTOFORGE_HA_TOKEN` 也取消注释注入了，即"拿到设备控制能力"这条链是通的。

**修法方向**：逃生舱必须回到 `not registry.enabled` 分支内；`requires()` 里的 scope 校验不能返回 `None` 来代替校验，应返回一个受控的临时 `TokenInfo` 或保持抛 403。

---

### F-02 🔬 已复现 ｜ 登录主体改名 ⇒ owner 面识别失效 ⇒ 授权码明文全掩码

**位置**：`src/autoforge/af_api.py:259`（`_OWNER_SUBJECTS = frozenset({"owner", "shared"})`）＋ `:1001 / :1022 / :1028`（三处 `issue_for_agent(body.username, ...)`）＋ `:1160`（`reveal = info is None or info.subject in _OWNER_SUBJECTS`）

本批改动把 `/api/auth/register`、`/api/auth/login`（严格档 + 兼容档）三处的签发主体从**固定字面量 `"owner"`** 改成了 **`body.username`**。但 `:1160` 判定"谁算 owner 面"的口径没跟着改，仍然只认 `{"owner", "shared"}`。

于是：任何真实登录的用户名（`"lidicn"`、`"sp"`、`"admin"`）都不在 `_OWNER_SUBJECTS` 里 ⇒ 一律被当成**第三方** ⇒ 授权码列表返回定形掩码。

复现（已跑）：

```
login: 200 {'username': 'sp', 'role': 'admin'}        ← 端点自己说 role=admin
seed:  200 {'code': '64459944', ...}                    ← 创建时明明拿到了明文
rows:  200 {'codes': [{'code': '********', ...}]}      ← 列表里自己的码变成了掩码
```

对照测试 `tests/unit/test_dcd_20261004_auth_limits.py:442-444` 断言的正是"owner 面看得见明文"：

```
assert '48751160' in {'********'}
```

**这条判据的对照档意义被改动自己打掉了**：测试注释写着"如果实现退化成『无脑全掩码』，本条先红"——现在确实退化成全掩码了，但退化发生在"owner 判定"这一层，不是掩码逻辑那一层。

**影响面**：`/api/user/auth-codes` 是唯一能看到授权码明文的面，用户从此无法念码部署。同时 `test_legacy_shared_token_counts_as_the_owner_face` 仍绿（`shared` 在集合里），所以这个洞不是全灭，而是**只对真实用户名失效**——更难被发现。

**修法方向**：`_OWNER_SUBJECTS` 不能是字面量集合。签发时用 `body.username`、判定时用集合，两处必须同源（例如把"登录用户"这个身份建模成一个显式标记，而不是靠 subject 名字猜）。

---

### F-03 🔬 已复现 ｜ 测试环境污染：8 条红的 5 条是同一根因，且**门禁读不出**

**位置**：`tests/unit/test_v0_6_tags.py:144`

```python
def _client(tmp_path, *, noauth: bool = True):
    import os
    if noauth:
        os.environ.setdefault("AF_ALLOW_NOAUTH", "1")   # ← 写进全局环境，从不清理
```

`conftest.py:18-21` 有一段醒目的警告：

> 请自行 `monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")`；**不要全局 setdefault**——会污染那些故意测 fail-closed 行为的测试（如 `test_v0_8_auth.py`）。

`test_v0_6_tags.py` 违反了这段警告。`os.environ.setdefault` 一旦写入，`monkeypatch` 不会回收（它只回收自己 `setenv` 过的键）。pytest 默认字母序收集，`test_v0_6_tags.py` 排在 `test_v0_8_auth.py`、`test_v1_4_token_expiry.py`、`test_dcd_20261004_auth_limits.py` **之前**——污染就传过去了。

复现（已跑，把污染源和被污染方放一起）：

```
pytest tests/unit/test_v0_6_tags.py test_v0_8_auth.py test_v1_4_token_expiry.py \
       test_dcd_20261004_auth_limits.py
→ 11 failed, 46 passed
```

单独跑同一批文件：

```
pytest tests/unit/test_v0_8_auth.py test_v1_4_token_expiry.py  → 1 failed, 35 passed   ← 那条是 F-10 真缺陷
pytest tests/unit/test_dcd_20261004_auth_limits.py              → 2 failed, 46 passed ← 那两条是 F-02 真缺陷
```

**这就是"单独绿、全量红"的全部解释**：`test_v0_8_auth` 的 3 条和 `test_v1_4_token_expiry` 的 1 条，被测代码**没有错**（过期令牌确实返回 403，已单独复现），是环境变量泄漏让 `requires()` 走了逃生舱分支。

**为什么会走到这里而不被发现**：`tests/unit/test_dcd_20261004_auth_limits.py` 里 7 条断言"未带令牌必须 403"（`test_asks_pending_now_needs_a_token` 等）在污染下也一起红——**fail-closed 契约的判据被环境污染批量推翻**，而失败列表里有 11 条，很容易被归因为"一批 flaky"。

**修法方向**：`test_v0_6_tags.py` 改用 `monkeypatch.setenv`。另外建议加一条 meta 测试：收集阶段扫描 `tests/` 下所有 `os.environ[` / `setdefault` 直写，命中即红——这个形状在本仓已经发生过一次，`setdefault` 是静默的，靠人读代码防不住。

---

## 二、P1 — 功能空转、崩溃或门禁假绿

### F-04 🔬 已复现 ｜ `af_atomic.py` 新增 `import json`，但它自己的门禁判据把它排除在外

**位置**：`src/autoforge/af_atomic.py:22`（`import json`）；判据在 `tests/unit/test_dcd_20261004_atomic_write_nine_sites.py:106`

```python
assert all(not m.startswith("autoforge") and m.split(".")[0] in
           {"os", "tempfile", "pathlib", "__future__"} for m in mods), mods
```

`mods = ['__future__', 'json', 'os', 'tempfile', 'pathlib']` → `json` 不在白名单 → 红。

**确定地在 HEAD 上就红**：`git show HEAD:src/autoforge/af_atomic.py | grep -n "^import json"` 有命中。不是本批改动引入的，但也不是本批修的。

代码注释（`:36-38`）自己写得很清楚：

> 放在本模块而不是各自抄一份，是因为这里已经是 9 站共同脚下、且**只依赖标准库**：加一个 `json` 不引入任何新的仓内边。

作者知道意图是"只用标准库"，但判据白名单没同步。`json` 确实是标准库——**问题在判据写成了精确白名单而不是"非仓内依赖"**，而 `refuse_when_shape_unreadable` 的用途（`:43` `json.loads`）是刚需，回退掉不现实。

**修法方向**：判据改成 `m.split(".")[0] not in sys.stdlib_module_names`（或把白名单扩到含 `json`），同时保留"不许出现 `autoforge.*`"这条真正要紧的约束。

---

### F-05 🔬 已复现 ｜ `_load_revision` 对非 dict 的 `revision.json` 抛 `AttributeError`，服务起不来

**位置**：`src/autoforge/af_config.py:87-91`

```python
def _load_revision(self) -> int:
    try:
        return int(json.loads(self._revision_path().read_text(encoding="utf-8")).get("revision", 0) or 0)
    except (OSError, ValueError):      # ← 不含 AttributeError
        return 0
```

复现（已跑）：

```
revision.json 内容 = "null"
  File "af_config.py", line 89, in _load_revision
AttributeError: 'NoneType' object has no attribute 'get'
```

`json.loads` 对 `null` / `42` / `[]` / `"x"` 都返回非 dict 的合法 JSON，`.get()` 一律 `AttributeError`。`except` 只列了 `(OSError, ValueError)`。

**同文件里的姊妹函数没有这个洞**（`:76-83`）：

```python
if not isinstance(data, dict):
    # 形状不对与读不出来同一条失败方向：`_creds.get` 会当场 AttributeError
    _logger.warning("credentials.json 形状不是对象，按读不出来处理: ...")
```

`_load_credentials` 早就被同类问题咬过并修了，`_load_revision` 漏了同款防护。

**失败场景**：
- 启动时 `Config.__init__`（`:51`）→ 未捕获 → `get_config()` 抛穿 → 服务起不来。
- 运行中 TTL 过期 → `get_config` → `cfg.refresh()`（`:183`）→ `_load_revision()` → 运行中崩溃。

`get_config` 是凭据访问的唯一入口（`af_api.py:522/527`、`af_cli.py:571/785/799`）。

**修法方向**：加 `isinstance(data, dict)` 守卫，形状不对按 revision=0 处理（或告警后回退内存值，与 `_load_credentials` 口径对齐）。

---

### F-06 🧭 静态坐实 ｜ `Scheduler.tick()` 的 for 到期分支缺 `enabled` 检查 ⇒ 禁用的自动化仍被触发

**位置**：`src/autoforge/af_scheduler.py:124-135`

三条触发路径的 enabled 检查不一致：

| 路径 | 位置 | 有 enabled 检查 |
|---|---|---|
| `handle_event`（状态事件） | `:88-89` | ✅ `if not auto.enabled: continue` |
| `_fire_time_triggers`（定时） | `:364-365` | ✅ `if not getattr(auto, "enabled", True): continue` |
| `tick()` for 持续条件到期 | `:124-135` | ❌ **无** |

`:132-133` 直接：

```python
auto = self.graph.get(pending.automation_id)
instance = self._try_fire(auto, auto.node(pending.node_id), pending.event)
```

**失败场景**：`for=10m to:off` 挂起 → 用户在 WebUI 点禁用 → 10 分钟后 `tick()` 到期 → `_still_holds` 只看实体状态（未受影响）→ 照常触发。三条路径里两条守住了，一条漏了，用户会看到"我明明关掉了，灯还是关了"。

**同段还有第二个洞**：`self.graph.get(automation_id)` 是 `self._by_id[automation_id]`（`af_ir/models.py:743-744`），**直接 KeyError**。`_pending` 只在 `:338` 一处 `pop`（条件破坏时），而 `tick()` 分支自己 `del self._pending[key]`（`:129`）——如果自动化在这一窗口被从 graph 删除，`:132` 就是未捕获 KeyError，会冒泡到 `runtime.tick()` 的调用方（`af_cli.py:277/452/531/637/1100`、`af_live.py:362`），**整个 tick 循环停摆**。

---

### F-07 🧭 静态坐实 ｜ `af_conflict_runtime` 的 WAIT 挂起 ⇒ 实例被静默判 done，retry 脱开主循环

**位置**：`src/autoforge/af_conflict_runtime.py:103`（默认值）、`:350-352`（WAIT 分支）、`:419-425`（retry）

三处串起来：

```python
wait_edges: tuple[str, ...] = ()          # :103 默认空 tuple

...
if decision is RequestDecision.WAIT and not observe:
    self._park(executor, original, instance, node, automation_id, instance_id)
    return tuple(self.settings.wait_edges)   # :352 返回 ()
```

`:352` 返回 `()` 回到 executor 主循环：

```python
# af_executor.py:184-187
edge = auto.pick_edge(node.id, kinds)     # kinds = ()
if edge is None:
    self._terminate(instance)             # ← 走终态
    return instance
```

`pick_edge`（`af_ir/models.py:670-673`）：`wanted = set(())` → `candidates = []` → `return None`。**空 tuple 一定返回 None**，一定走 `_terminate` → 实例标为 done。

而 `_park` 已经把 waiter 存起来了，之后锁释放触发 `_on_pending_ready`（`:419-425`）：

```python
if self.scheduler is not None:
    try:
        self.scheduler.call_later(0.0, _retry)   # ← Scheduler 没有这个方法
        return
    except Exception as exc:
        self._audit_degraded("schedule", exc, ...)
_retry()     # ← fallback：同步重入，但 executor.run 早已 return
```

`grep -nE "def (call_later|schedule|add_timer|later)\b" src/autoforge/af_scheduler.py` → **无匹配**。`Scheduler` 从头到尾没有延迟调度接口（见 F-08）。所以 `AttributeError` 一定抛，一定被吞，一定 fallback 到同步 `_retry()`——而此时 executor 主循环已经结束，`_retry` 里算出的 `edges` 无人消费。

**用户可见结果**：UI 显示"执行完成"（假绿），IR 后续节点从未执行。

**注**：这条只在 `AUTOFORGE_CONFLICT_ARBITER=1` + `AUTOFORGE_CONFLICT_WAIT=1` 且仲裁器真判出 WAIT 时触发。`grep` 确认 `AUTOFORGE_CONFLICT_ARBITER` / `AUTOFORGE_CONFLICT_WAIT` 在 `src/` 里**没有任何 `getenv` 读取点**（只有 `AUTOFORGE_CONF_GRADING` 在 `af_runtime.py:100` 有），即这两个开关当前是**未接线的**——但 `ConflictSettings.wait_edges` 的默认值陷阱是结构性的，一旦接线就会踩。

---

### F-08 🧭 静态坐实 ｜ `make_later` 恒返回 `None` ⇒ `CONF_GRADING` 整块引擎空转

**位置**：`src/autoforge/af_runtime_ext.py:36-52`（`make_later`）、`:142`、`:216-228`

```python
def make_later(scheduler: Any) -> LaterFn | None:
    if scheduler is None:
        return None
    for name in ("call_later", "schedule", "add_timer", "later"):
        fn = getattr(scheduler, name, None)
        if not callable(fn):
            continue
        ...
        return _later
    return None        # ← Scheduler 上这四个名字全找不到，必然走这里
```

已确认 `af_scheduler.py` 全文无这四个方法。所以 `AUTOFORGE_CONF_GRADING=1` 打开时 `later is None`，`:216` 的 `if later is not None:` 硬短路，整个定时器块不执行：

```python
if later is not None:
    def _tick() -> None:
        shadow.compare_due(at=now)
        intervention.check_holds(at=now)
        intervention.flush_expired(at=now)
        canary.check(at=now)
        grading.tick(hours=1.0)
        grading.persist()
        later(3600.0, _tick)
    later(3600.0, _tick)
    pretrigger.start(later)   # F12 周期扫描
```

**下游全部有同款短路**（已逐个确认）：
- `af_shadow.py:449-455` `ShadowRunner._schedule`：`if self.later is None: return`
- `af_intervention.py:350-359` `InterventionDetector._schedule_hold`：`if self.later is None or ...: return`
- `af_pretrigger.py:351-360` `PreTriggerService.start`：`if later is None: return`

**结论**：`AUTOFORGE_CONF_GRADING=1` 打开后，灰度晋升/降级判定、conf 时间衰减、影子比对、人工干预 hold 结算、canary 巡检、F12 预测性触发扫描——**全部空转**。API 照常响应，运营者无从察觉。

**同根**：F-07 和 F-08 是同一个接口契约缺失的两种表现。代码按"Scheduler 上会有延迟调度能力"写了 6 处调用，`Scheduler` 从未实现。

**修法方向**：在 `Scheduler` 里补一个真正的 `later(delay, callback)`（内部维护 `_pending_delays`，由 `tick()` 消费）。修好这一处，F-07 的 `_retry` 和 F-08 的整块引擎同时活了。

---

### F-09 🧭 静态坐实 ｜ `_ir_writes_devices` 查错字段名 ⇒ strict 静态闸永不拦截写设备

**位置**：`src/autoforge/af_proposal.py:144-155`

```python
def _ir_writes_devices(ir: Mapping[str, Any]) -> bool:
    for auto in ir.get("automations", []) or []:
        nodes = auto.get("nodes", {})
        node_iter = nodes.values() if isinstance(nodes, Mapping) else (nodes or [])
        for node in node_iter:
            if node.get("kind") != "do":
                continue
            entities = node.get("entities") or node.get("target_entities") or []
            if entities:
                return True
    return False
```

IR 节点根本没有 `entities` / `target_entities` 字段。真实位置在 `af_ir/models.py:536-555` 的 `Node.target_entities()`：

```python
raw = self.params.get("entity_id")                       # 形式一
target = self.params.get("target")                        # 形式二
if isinstance(target, dict):
    for key in ("entity_id", "device_id", "area_id"):
        val = target.get(key)
```

函数体**永远返回 `False`**。而它的唯一用途是 `StaticGuardPolicy.resolve` 的 strict 分支（`:136-138`）——`_ir_writes_devices(plan.ir)` 为 False 就不抛 `ProposalError`。

**失败场景**：proposal 带一条写灯的 do 节点（`params: {"entity_id": "light.x"}`），strict 模式本应拒部署，实际放行。

**注**：`grep _ir_writes_devices` 在 `tests/` 下零命中——**这个函数没有任何测试引用**，所以它一直是死的。

---

### F-10 🔬 已复现 ｜ `verify_roundtrip` 对循环容器抛穿 `IRDepthError`，违反自己的契约

**位置**：`src/autoforge/af_fidelity.py:280`；`src/autoforge/af_ir/models.py:772 → 232-237`

函数 docstring 明确写着：

> 词汇表外的形状**不再抛穿**：落成 `not_comparable` 一档，`ok=False`，`detail` 带位置与原因。

但 `:280` 的 `Automation.from_dict(dict(ir))` 在 `try` 块**之外**：

```python
auto = Automation.from_dict(dict(ir))     # ← 这里抛穿
...
try:
    rebuilt = Automation.from_dict(project_automation(auto))
    ...
except (FidelityNotComparable, ParamDepthError, LeafUnserializable) as exc:
    not_comparable = True
```

复现（已跑，测试文件 `tests/unit/test_fidelity_canon_vocabulary.py:88-95`）：

```
loop: list = []
loop.append(loop)
verify_roundtrip(_ir({"loop": loop}))
  → src/autoforge/af_ir/models.py:234
  IRDepthError: IR 整篇嵌套深度超过上限 128（validate_automation(root_key=None)）
```

`_exceeds_container_depth`（`models.py:214-229`）是迭代实现，对**自引用容器**是死循环式的（同一个对象反复入栈，`depth` 每轮 +1，永远走不到 `isinstance` 之外的分支），只能靠 `depth > limit` 早停拦下来——拦下来了，但抛的是 `IRDepthError`，而调用方的 `except` 里没有它。

**影响**：HTTP 面上的保真校验（若有端点直连 `verify_roundtrip`）会把本应返回诊断的调用变成 500。这正是要避免的形状。

**修法方向**：把 `:280` 挪进 `try`，`except` 里补 `IRDepthError`；或让 `_exceeds_container_depth` 带 `seen` 集合识别环，直接判超预算。

---

### F-11 🧭 静态坐实 ｜ `import_bundle` overwrite 策略遇"部分版本校验失败"⇒ 新旧版本混档

**位置**：`src/autoforge/af_store.py:840`、`:851-870`

```python
if self.versions(name) and strategy == "overwrite" and not entry_has_error:
    ...
    stash = self._stash_archive(name)     # 让位旧归档
try:
    # 合法版本仍然写入（非法版本已被跳过）
    for ver, graph_dict in validated:
        self.save_version_raw(target, graph_dict, int(ver.get("version", 1)), ...)
...
report["imported"].append(target)          # ← 无条件追加
```

**失败场景**（`strategy="overwrite"`）：
1. store 已有 `demo` v1..v5（v5 最新）
2. bundle 里 `demo` 条目含 v1（合法）、v2（IR 校验失败）、v3（合法）
3. v2 失败 → `entry_has_error=True` → `:840` 条件不成立 → `stash=None`，**旧归档不让位**
4. `:851-861` 仍然写 v1、v3 → **直接覆盖旧 v1.json / v3.json**
5. 旧 v2.json / v4.json / v5.json 原样保留
6. `latest("demo")` → 5（旧 v5），`load("demo")` 读到旧 v5 的 graph——**不是 bundle 里的 v3**
7. `:870` 无条件 `report["imported"].append("demo")`

结果：`ok=False`（errors 里有 v2 的明细），但 `imported` 声称已导入，`latest()` 指向旧版本，磁盘上是新旧混杂的 5 个版本文件。

**根因**：`:840` 的 `not entry_has_error` 把"部分失败"和"全部合法"混为一谈——前者跳过了让位，却仍然写合法版本。docstring 声称 overwrite 语义是"先删除已存在归档，再导入"，实际既没删也没重置版本号。

**修法方向**：`entry_has_error=True` 时整条归档跳过（一个版本都不写），记入 `errors` 而非 `imported`。

---

## 三、P2 — 契约破裂、静默失败与资源泄漏

### F-12 🧭 静态坐实 ｜ `/api/asks/pending` 吞异常返回 `ok=True, asks=[]`

**位置**：`src/autoforge/af_api.py:678-681`

```python
try:
    data = json.loads(sc.read_text(encoding="utf-8"))
    return {"ok": True, "asks": data.get("asks", []), "ts": data.get("ts", 0)}
except Exception:
    return {"ok": True, "asks": []}
```

sidecar 文件存在但内容损坏/半截写入时，返回 `ok=True, asks=[]`——**契约上等同于"没有待答 ask"**。

`af_live.py:433-441` 的写侧注释自己点名了这个形状：

> 崩在半截会被读侧当成「没有待答」而静默丢弃

HTTP 读侧在这里把它做实了。DB 侧持 write 令牌周期轮询这个端点（`af_api.py:668-671` 的 docstring 明写用途），一轮损坏就读成"无待答"，跳过整轮提问，且无任何告警通道。

对比：`af_store` / `af_premiere` / `af_undo` 三处的写侧都装了 `refuse_when_shape_unreadable` 护栏，读侧却按"读不出＝空"处理——**只守住了写，没守住读**。

**修法方向**：区分"文件不存在"（返回空列表，正常）与"文件存在但读不出"（返回 `ok=False` + 诊断，或告警）。

---

### F-13 🧭 静态坐实 ｜ `InsightQueue.unreadable` 无上限裁剪

**位置**：`src/autoforge/af_insight_queue.py:105`、`:192`

```python
self.unreadable: list[str] = []
...
self.unreadable.append(f"{path.name}: {type(exc).__name__}: {exc}")
```

无裁剪。同仓 `af_linkage_feed.py:263-264` 有同款防护：

```python
if len(self.unreadable) > UNREADABLE_MAX:
    del self.unreadable[: len(self.unreadable) - UNREADABLE_MAX]
```

`list_pending()` 经 HTTP `/api/insights/pending`（`af_api.py:870`）周期调用，坏文件不清理则每次调用都 append，永不裁剪。同一族代码两个模块两套口径。

**修法方向**：抽到 `af_bounded_caches` 或 `af_atomic` 这一类"9 站共同脚下"的模块里共享，避免再出第三份。

---

### F-14 🧭 静态坐实 ｜ `PremiereStore._by_code` 与 `TriggerHistory._events` 只增不减

**位置**：`src/autoforge/af_premiere.py:115-218`、`src/autoforge/af_pretrigger.py:91-104`

- `PremiereStore`：每次部署 `issue()` 生成新 6 位码存入 `_by_code`；`consume()` 只设 `consumed_at`，不删除；过期码（TTL 300s）也不删除；`save()` 每次**全量**序列化落盘。`_gen_unique_code_locked` 判 `code not in self._by_code` 时过期码仍占位（浪费码空间）。
- `TriggerHistory.record`：`_events[aid].append(rec)` + `_save()` 全量重写，无任何 trim/prune。`Predictor._prune` 只裁剪 `Predictor._models[aid].events`，**不回写** `TriggerHistory._events`——写侧和消费侧是两份独立存储，消费侧的裁剪动作不到达写侧。

**失败场景**：常驻自动化每天触发多次，运行数月后 `_events` 与 `save()` 耗时线性增长；`save()` 是全量重写，磁盘 IO 也随之线性增长。

**注**：`af_intervention.py`、`af_shadow.py`、`af_error_knowledge.py`、`af_feedback.py`、`af_metrics.py` 都有 `max_records` / `trim` / 环形缓冲上限——这两个模块是**唯一两个漏掉的**。

---

### F-15 🧭 静态坐实 ｜ `Predictor._load` 用 `+=` 叠加绝对值计数器 ⇒ 每次重启翻倍

**位置**：`src/autoforge/af_predict.py:897-900`

```python
model.learned = int(blob.get("learned", 0))
model.dropped += int(blob.get("dropped", 0))          # ← +=
model.pruned = int(blob.get("pruned", 0))
model.skipped_predicted = int(blob.get("skipped_predicted", 0))
```

`:897` 是 `=`，其余三个是 `+=`。而 `_save`（`:839-840`）保存的是当前内存中的绝对值。

**失败场景**：盘上 `dropped=100` → 进程重启 → `_load` 新建 model（`dropped=0`）→ `+=100` → 正确。看起来没事。**但 `_load` 自己内部也在累加**（`:879`、`:883` 两处 `model.dropped += 1`，读坏行时）——如果盘上文件本身含 N 条坏行（上次已计数并持久化），本次 `_load` 又计一遍，`dropped` 变成 `100 + N`。下次重启再叠一次。**坏行越多，翻倍越快。**

不影响触发逻辑，但 `stats()`（`:711-714`）返回的指标持续失真，运维会拿它判断"丢弃率是否在恶化"。

**修法方向**：把三个 `+=` 改成 `=`，坏行的重复计数应在**读**的时候跳过而不是累加。

---

### F-16 🧭 静态坐实 ｜ `_NODE_KINDS` 漏 `group` ⇒ AF-Spec 无法往返 group 节点

**位置**：`src/autoforge/af_spec.py:56` vs `src/autoforge/af_ir/models.py:78`

```python
# af_spec.py:56
_NODE_KINDS = ("on", "if", "do", "ask", "wait", "set", "pass")        # 7 种
# af_ir/models.py:78
NODE_KINDS = ("on", "if", "do", "ask", "wait", "set", "pass", "group") # 8 种
```

- `compile_spec("automation a1\ngroup g1 ...")` → 命中不了 `_NODE_KINDS` → 落到 `_parse_option` → `E_UNKNOWN_PRIMITIVE`
- `render_spec(graph)` 遇 group 节点 → `af_spec.py:500-501` → `raise SpecError("未知节点类型：group")`

**根因**：`af_ir/models.py:82` 的注释已经点名了这件事（大意是"结果 af_spec 的 `_NODE_KINDS` 已经漏了 group 而无人报红"）——注释在，修复没跟上。`af_scanner` 支持 group，但 AF-Spec 层不往返。

**修法方向**：`_NODE_KINDS` 直接引 `af_ir.models.NODE_KINDS`，一处真源，避免第 9 种节点再来一遍。

---

### F-17 🧭 静态坐实 ｜ `build_ir` 兜底 IR 不合 schema ⇒ 无 `suggested_ir` 的 proposal 永远部署失败

**位置**：`src/autoforge/af_proposal.py:158-177`

```python
return {
    "version": 1,                                  # ← 应为 ir_version
    "automations": [{
        "id": aid,
        "confidence": None,
        "trigger": {"kind": "manual"},             # ← trigger 应在 on 节点内
        "nodes": {
            "ask": {"id": "ask", "kind": "ask", "prompt": ...},  # ← 应为 array
        },
    }],
}
```

`af_ir/schema/ir.schema.json` 要求 `ir_version`（枚举）、`mode`（枚举）、`nodes` 为 array。这份兜底 IR 三处全不符，`validate_automation` 必抛 `IRValidationError`。

`_auto_deploy`（`af_proposal.py:293-308`）的 `except` 分支捕获后置 `FAILED`——**每次都不部署，每次都是 FAILED**，且失败原因看起来是"下游校验不过"，真正的根因是这份模板本身就是坏的。

---

### F-18 🧭 静态坐实 ｜ 明文令牌 `issued_tokens_clean.json` 未被 `.gitignore` 覆盖

**位置**：仓库根目录 `issued_tokens_clean.json`（未跟踪）；`.gitignore:34-39`

文件内容含一枚明文令牌：

```json
{
  "af_ec63983fd1ab17a5823e14fc33bf8dd62b0195f1": {
    "subject": "owner",
    "scopes": ["live", "read", "write"]
  }
}
```

`.gitignore` 已忽略 `issued_tokens.json` 和 `issued_tokens.json.tmp`，但 `.gitignore:34-37` 的注释明确写着这个 `_clean.json` 变体**不忽略**（理由写的是"归属不明"）。已验证 `git check-ignore -v issued_tokens_clean.json` 返回码 1（未被忽略）、`git ls-files --error-unmatch` 无命中（未跟踪）——**当前处于"含明文令牌、无版本控制保护、任何人 `git add -A` 都会带上去"的状态**。

同批还有 `docker-compose.api.yml.tmp`（根目录）也是编辑残留、未被忽略。上一批提交 `fb700b5` 的 message 明写"补明文令牌注册表面"，但这个变体没在射程里。

**修法方向**：`.gitignore` 加 `*.tmp` 和 `issued_tokens*.json`；同时确认这枚令牌是否还活着（`_revoked` 文件里若没有它，就还活着）。

---

## 四、P2 — 前端功能性

### F-19 📎 未独立复核 ｜ 真实模式下「配对新 Agent」按钮是死按钮

**位置**：`ui-user-mimo/src/api/http.ts:186-189`

`createPairRequest` 的实现只有 `if (currentPair) return currentPair; throw PAIR_INVALID`。真实后端**没有**"用户端主动创建配对码"的端点——配对码只能由匿名 agent 经 `POST /api/mcp/pair/request`（`af_api.py:1196`）触发。所以 UI 的「生成配对码」按钮在真实模式下永远无输出，只有 agent 侧先发起、SSE 推入才能弹出。

**修法方向**：后端补 `POST /api/user/pair/request`（对齐 `api_pair_request_bootstrap`，加 `_write` 依赖），而不是前端绕过。

---

### F-20 📎 未独立复核 ｜ 批准 pending 后自动化状态不自动更新

**位置**：`ui-user-mimo/src/stores/main.ts:206-209`；`http.ts:123-143`

乐观更新的匹配键错了：`mapAutomation` 里 `id = c.id`（归档名），而 `resolvePending(opId)` 拿到的是 `PendingStore.submit` 生成的 `uuid.uuid4().hex[:16]`（`af_pending.py:137`），经 `_pending_map`（`af_api.py:1303-1312`）以 `payload.name → op_id` 映射传出。`if (m.id !== opId) return m` 拿归档名与 uuid 前 16 位比较，**永远不相等**，automations 数组不会被改写。

用户看到"已批准该操作"的成功提示，但启用列表里仍看不到这条自动化，需手动刷新。

---

### F-21 📎 未独立复核 ｜ 授权码被 consume 后前端仍显示为可用

**位置**：`ui-user-mimo/src/api/http.ts:244-254`

后端 `list()`（`af_auth.py:843-867`）已返回 `consumed` 字段，前端 filter 只过滤 `revoked`，遗漏 `consumed`；`types/api.ts:54-59` 的 `AuthCode` 类型也没有 `consumed` 字段。用户以为自己还能再部署一次。

**旁注**：`ui-user-mimo` 树没有 `vue-tsc` 类型硬门（CI 只跑 `npm test` + `npm run build`，vite/esbuild 不做类型检查），所以上面这类类型层面的不一致不会被 CI 拦截。对照 `ui/` 树有 `vue-tsc -b --force`。

---

## 五、P2 — 部署配置

### F-22 📎 未独立复核 ｜ `docker-compose.api-test.yml` 无 `build:` ⇒ 测试环境跑旧后端

**位置**：`docker/docker-compose.api-test.yml`（新文件，未跟踪）

`docker/docker-compose.api.yml` 有 `build: { context: .., dockerfile: docker/Dockerfile.api }`；新文件只写 `image: autoforge-api:latest`，**复用同一个 tag**。两个文件对同一 tag 的写入方不同，本机/远端可能各自有一枚。开发者按测试 compose 起服务时，跑的是本机很久之前烘的旧镜像——测试结论对不上源码。

另外新文件里 `AUTOFORGE_HA_TOKEN` / `AUTOFORGE_TOKENS` / `AF_ALLOW_NOAUTH` 三行都没传（`AF_ALLOW_NOAUTH` 缺失意味着测试容器默认 fail-closed，与主配置本批新加的 `AF_ALLOW_NOAUTH=${AF_ALLOW_NOAUTH:-}` 不一致）。

---

## 六、其他已确认但影响有限的项

以下条目**已核实为真**，但影响面有限，按观察项登记，不建议本批处理：

| # | 位置 | 说明 |
|---|---|---|
| A | `af_proposal.py:241-254` | `_child_entity_ops` 只读 `params.entity_id`，不读 `params.target.{entity_id,device_id,area_id}`。用 target 形式的 do 节点不进冲突矩阵，`cross_automation_conflicts` 漏报。结果只做信息输出、不阻断部署，故为漏报。 |
| B | `af_scanner.py:747-764` | `_check_snapshot` 只查表达式树**顶层**的 `op == "and"`，嵌套在 `or` 里的多条件 `and` 漏报 `SNAPSHOT_FALSE_MULTI_AND`（撕裂读风险，WARNING 级）。 |
| C | `af_ir/condition_norm.py:56-62` | `_leaf_key` 用 `json.dumps` 做规范键，`1`（int）与 `1.0`（float）序列化不同，而求值层 `expr.py:449-450` 的 `_compare` 允许 int/float 互比 ⇒ 语义等价的两个条件被判不等价。 |
| D | `af_predict.py:611-620` | `pre_trigger` 先落盘 `predicted=True` 再调 executor；executor 瞬时失败被 `except Exception` 吞掉后 `predicted` 标记保留 ⇒ 当天永久跳过。`grep clear_prediction` 全仓只有定义、**零调用点**。 |
| E | `af_fire_recorder.py:116-122` | `release` 保持 `state="claimed"` 但 `attempts` 不回退 ⇒ release 后重试仍在消耗配额，3 次瞬时失败即当天永久哑火。 |
| F | `af_api.py:1104-1105` | SSE 配对事件生成器 `except Exception: pass`，无日志。`mark_pushed` 可能已对部分码执行过，推送状态半完成。 |
| G | `af_runtime.py:261-263` | `_on_terminal` 循环调观察者无 try/except，与 `af_bus.py:372-387` 的"逐个 handler 隔离"纪律不一致。观察者抛异常会阻断终态事件的下游传播（状态已改，但审计/MQTT 侧看不到）。 |
| H | `af_scheduler.py:246-247` | `_drain_queues` 出队时硬编码 `auto.entry_nodes()[0]`，忽略事件实际触发的入口节点。多入口自动化在 QUEUED 模式下走错分支。 |
| I | `af_api.py:1180-1186` | `api_agent_delete` / `api_agent_rename` 无主体校验：第三方 write 令牌可删/改任意主体（已复现 `bot` 令牌调 `/api/user/agents/owner` 返回 200）。当前无危害的巧合是 `rename_subject` 的 count 口径让它返回 `renamed: 0`；但**这条端点本身就是横向越权**。 |
| J | `af_mcp.py:463` vs `af_api.py:248` | MCP 面 scope 是纯逐名比对，HTTP 面 write 蕴含 read ⇒ 同一枚 write-only 令牌在两面行为不同。当前 MCP 的 read 域工具 scope 都是 `None`（公开），暂无触发面。 |
| K | `af_mcp.py:319` / `af_api.py:545` / `:559` | MCP 面的 `af_live_run` 无 `undo` 参数、`af_enable_by_tag` 有 `allow_bulk`、`af_import_store` 有 `allow_bulk`；HTTP 面对应端点都没有。两面能力集不同源。 |

---

## 七、审计方法说明与已知局限

**做了什么**
- 全量测试跑了两遍（`-x` 早停 + 全量），并用单文件/组合跑法定位出"全量红单独绿"的污染源。
- 对 6 条候选缺陷用可执行脚本跑出实际错误输出（F-01、F-02、F-03、F-04、F-05、F-10）。
- 对 12 条做了静态坐实：读码 + `grep` 确认调用点非死代码 + 跨文件交叉核对字段名/契约。
- 逐行读了 `src/autoforge/` 下 102 个 py 文件（45985 行）、`ui-user-mimo/` 23 个文件、`docker/`、`gates.sh`、`ci.yml`。

**没有做的**
- **`af_orchestrator.py`（2641 行）/ `af_service.py`（2804 行）/ `af_closedloop/`（10 文件）/ `af_evo.py`（1484 行）未逐行深审**——本批审计资源主要花在了鉴权层、运行环与前端契约上。这几块是最可能出现未报告缺陷的地方，尤其是 2641 行的 orchestrator。
- **没有做变异测试、依赖 CVE 面（pip-audit）、semgrep 规则面扫描**——这几格在仓内台账里本来也挂着"待窗项"（`docs/audit/index.md` §四），本轮未新增覆盖。
- **`af_vhass/` 仿真层**：只抽查了 device_sm / event_bus 的去重与缓冲上限，没做端到端仿真跑通验证。
- 前端 3 条（F-19/20/21）来自分块审查结论，我核对了后端契约那一半，**没有跑前端**（本机无 node 环境验证）。标记为 📎 未独立复核，建议按此三条各写一条断言再落码。
- `docs/audit/index.md` 记载的项目历史（ADM-auditkit 台账 F1/F2/F6/F12/F15 已修、F16 裁定库侧修）本轮**未重复核实**，按台账采信。

**建议的修复顺序**
1. **F-01 + F-02**（同一批改动引入的鉴权回归，且 compose 已把开关注入生产）——先修，止血。
2. **F-03**（测试环境污染）——修完这条，F-01/F-02 修好后才能真正读到"全绿"，否则会被 11 条红淹没。
3. **F-04**（门禁判据，一行改动）。
4. **F-08 + F-07**（同一个接口契约缺失，补 `Scheduler.later` 两处一起活）。
5. **F-05 / F-06 / F-09 / F-11**（独立缺陷，各修各的）。
6. 其余 P2 按批次走。
