# 豆包（db）执行路线图 · AutoForge 实测缺口第二批+第三批

> **PM：Claude（本会话）· 开发：豆包（db，免费额度至 2026-10-25）**
> 仓库：`E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24`（当前分支 `claude/busy-meninsky-653c24`）
> 上游：master（发版线）。缺口来源：`docs/缺口登记_实测自动化_20261023.md`（G1-G9）+ `docs/书房场景自动化_试演金丝雀验证_20261024.md`（G10）
> **已完成不接**：G1/G2/G4（commit 50c97d5）+ AST 门禁收账（89c8be9）+ 书房场景验证（2eaa335）。

---

## 0. 先读这一段：为什么这样切活

这个仓有一道**机械前置**（`scripts/check_ledger_same_batch.py`，裁定 20261014 §一 Q1）：

> 凡改动会移动 `file:line` 的提交，与它对应的清单必须在同一笔提交里重生成；分两笔 ⇒ CI 判红。

射程（已现读核验）：
- `scripts/check_observability.py` 只扫 `src/autoforge/**/*.py`（**`tests/`、`scripts/` 完全在射程外**）
- `scripts/check_except_pass.py` 同上
- `scripts/check_release_line.py` 扫 `src/autoforge/**/*.py`，且**逐锚点钉 `af_api.py`**
- `scripts/check_ledger_same_batch.py` 读 **HEAD 树**判提交边界

五份名册清单：`docs/可观测性清单.md`、`docs/进程模型清单.md`、`docs/信任边界清单.md`、`docs/发布线清单.md`、`docs/仓库卫生清单.md`。

**已核验的事实**（这是本路线图的切活依据，不是猜的）：
- `af_draft.py` / `af_auth.py` / `af_apply.py` 在 `docs/可观测性清单.md` 里 **0 登记**（这三个文件当前 0 个 `logger.*` 站点）
- `af_audit.py` 只依赖 `af_atomic`，**不 import `af_auth`** ⇒ `af_auth` 反向调 `af_audit` 无循环
- `af_audit` 是**非 logger 留痕通道**（结构化审计事件），不新增 `logger.*` 站点 ⇒ 不撞可观测性台账

**切活结论**：
- **给豆包**：`af_draft.py`、`af_auth.py`、`tests/` 下的改动 + 纯新增门禁脚本（不在任何名册里，由 `check_gates_coverage.py` 管的是工作流挂载而非清单锚点）
- **留给自己**：任何碰 `af_api.py` / `af_mcp.py` 的改动（G6、G3 的适配层决策）——这两个文件撞发布线台账逐锚点钉 + 可观测性台账，豆包改完我再补台账，**一天窗口来不及**

---

## 1. 豆包开工前必读（铁的纪律）

### 1.1 每条命令都要带 PYTHONPATH

```bash
cd "E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24"
```

本 worktree 的 `src` 不在默认 import 路径，**任何 python/pytest 命令都必须带前缀**：

```bash
PYTHONPATH="E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24/src" python -m pytest tests/unit/xxx.py -q
```

不带会 `ModuleNotFoundError: autoforge`，或更糟——静默 import 到主 checkout 的旧代码，测试绿但改的不是同一份。

### 1.2 每一笔 commit 前必跑门禁

```bash
PYTHONPATH="E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24/src" bash gates.sh
```

**贴完整输出**，不接受「跑过了」。退出码语义：0 过 / 1 有未获批违规 / 2 环境或配置不对（**2 不是绿**）。

### 1.3 ⛔ 绝对不许碰的文件

| 文件 | 原因 |
|---|---|
| `src/autoforge/af_api.py` | 发布线台账逐锚点钉 + 可观测性台账 §五 HTTP 面读数 |
| `src/autoforge/af_mcp.py` | 同上，且 `_TOOLS_WITH_CONTEXT` 改动牵动 scope 语义 |
| `src/autoforge/af_executor.py` | 进程模型台账 + 信任边界台账在册（canary/ask/resume 路径被逐行钉） |
| `src/autoforge/af_canary.py` / `af_vhass/fake.py` | canary 契约与仿真底座，改动影响全量既有仿真期望值 |
| `scripts/check_*.py` | 门禁自身，改了等于自己给自己打分 |
| `.gates.toml` / `.gates-baseline.txt` / `.gates-tally.txt` | 基线冻结与计数棘轮 |
| `docs/可观测性清单.md` | 自动段由 `check_observability.py --write` 生成，手改必红 |
| `docs/发布线清单.md` | 同上 |
| `docs/进程模型清单.md` / `docs/信任边界清单.md` | 同上 |

**如果你认为必须改上面某个文件：停下来，写进 `docs/db_roadmap_blockers.md`，不要动。**

### 1.4 ⛔ 不许出现的写法

- **不许** `except Exception: pass` / `except: pass`——`scripts/check_except_pass.py` 是棘轮门，基线 16 枚只减不增。**新增一枚直接红。** 真要容错，写具名捕获 + 具名码 logger 或 `af_audit` 事件。
- **不许** `git stash`（stash 栈与主 checkout 共享，会 pop 到别人的改动）。要暂存用 `git stash push -u -m "db-<任务号>"` 并记下 SHA，恢复用 `git stash apply <sha>`。
- **不许** 直接 `git push origin master` 或打 tag。发版由 PM 统一做。
- **不许** commit message 里出现真实令牌值 / 授权码值（仓库 public，分类器会拦）。

### 1.5 工作方式：一笔 commit 一件事

```
fix(draft): <一句话>
test(draft): <一句话>
chore(release): bump 1.13.4   ← 这个 PM 做，豆包别做
```

每个任务的分支命名：`db/task-NN`（从当前 HEAD 切）。做完**不要合回**，让 PM review 后统一合。

### 1.6 每笔 commit 的自检三问

1. `gates.sh` 完整输出贴了吗？退出码 0 吗？
2. 这笔碰了 §1.3 禁区的文件吗？碰了就停下。
3. 新增了 `except.*pass` 吗？加了就重写。

---

## 2. 任务清单（按依赖排序，可并行）

### DB-01 · G5：字符串意图 crash 成 DraftError（隔离性最强，建议第一个做）

**现状（已实测复现，2026-10-24）**：
```python
PYTHONPATH=.../src python -c "
from autoforge.af_draft import draft_intent, DraftError
draft_intent('打开客厅灯', catalog=None, session_id='probe')"
# → DraftError: E_UNKNOWN_DOMAIN 意图包含未知域: ['打', '开', '客', '厅', '灯']
```
根因：`draft_intent`（`src/autoforge/af_draft.py:213`）第一段就是
```python
unknown = [k for k in intent if k not in _INTENT_FIELDS]
```
传入 `str` 时迭代的是**字符**，于是每个汉字都成"未知域"。

**这不是无害的报错丑化**——它把「用户传了字符串而不是 dict」这个真实协议错误，伪装成了「域名单错了」，
agent 拿到的 `fix.allowed` 是 `_INTENT_FIELDS`，会去改字段名，而真正的修法是包一层 dict。**误导性归因。**

**判据腿（先写测试，必须红）**——新建 `tests/unit/test_draft_intent_type_guard.py`：

| # | 输入 | 期望 |
|---|---|---|
| 1 | `draft_intent("打开客厅灯")` | 抛 `DraftError`，`code` 是**新码**（如 `E_INTENT_NOT_OBJECT`，不要复用 `E_UNKNOWN_DOMAIN`），message 明说「意图必须是 JSON 对象，收到 str」 |
| 2 | `draft_intent(["name", "开门"])` | 同上，message 里的类型是 `list` |
| 3 | `draft_intent(None)` | 同上 |
| 4 | `draft_intent({"name": "开门", "when": {...}, "do": {...}})` | **不回归**——正常 dict 路径照旧走通 |
| 5 | `draft_intent({"unknown_field": 1, ...})` | 仍走 `E_UNKNOWN_DOMAIN`（原有行为不动） |

**改法**：在 `draft_intent` 的 `unknown = [...]` **之前**插类型闸：
```python
if not isinstance(intent, dict):
    raise DraftError(
        E_INTENT_NOT_OBJECT,
        f"意图必须是 JSON 对象（dict），收到 {type(intent).__name__}",
        fix={"expected": "object", "example": {"name": "...", "when": {...}, "do": {...}}},
    )
```
`E_INTENT_NOT_OBJECT` 常量加在 `af_draft.py` 顶部错误码区（与既有 `E_*` 同风格）。**不要**改 `_INTENT_FIELDS`。

**验收**：
```bash
PYTHONPATH="E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24/src" python -m pytest tests/unit/test_draft_intent_type_guard.py -q
PYTHONPATH="E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24/src" python -m pytest tests/unit/test_draft_intent_channel_gaps.py -q
PYTHONPATH="E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24/src" bash gates.sh
```

**⛔ 不许顺手做**：不要在 `af_draft.py` 里加 `logger.warning`。这个文件当前 0 个留痕站点，加一个就是新增台账行 ⇒ 撞 §1.3 禁区。异常本身就是留痕（带新码可机读）。

---

### DB-02 · G8：配对令牌过期剔除零告警（用 af_audit 通道，不撞台账）

**现状（已现读）**：`src/autoforge/af_auth.py:406` `_purge_expired_locked()`：
```python
def _purge_expired_locked(self) -> None:
    """清掉已过期的已签发令牌（须持锁）。"""
    now = time.time()
    dead = [t for t, info in self._tokens.items()
            if info.expires_at is not None and info.expires_at <= now]
    for t in dead:
        self._tokens.pop(t, None)
```
**零留痕**。配对复盘（2026-10-23）的结论：过期剔除静默发生，出问题时无法回答「什么时候掉的、掉了几枚、是不是误伤」。

**已核验的可行通道**：`af_audit`（`src/autoforge/af_audit.py`）只依赖 `af_atomic`，**不 import `af_auth`** ⇒ `af_auth` 反向 import 无环。`af_audit` 是结构化审计事件，**不是 `logger.*` 站点** ⇒ 不新增可观测性台账行。这是本任务不撞禁区的关键，**别改用 logger**。

`AuditEvent` 形状（`af_audit.py:115`）：
```python
@dataclass(frozen=True)
class AuditEvent:
    type: str
    at: datetime
    message: str
    automation_id: str = ""
    instance_id: str = ""
    node_id: str = ""
    entity_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
```

**判据腿（先写测试，必须红）**——新建 `tests/unit/test_auth_purge_alerting.py`：

| # | 场景 | 期望 |
|---|---|---|
| 1 | 构造 store，`issue_for_agent` 签一枚 `expires_at` 已过的令牌 → 再签一枚正常令牌（触发 `_purge_expired_locked`） | `af_audit` 落一条 `TOKEN_EXPIRED_PURGED` 事件，`data` 带 `count=1`、`subjects=[...]`、`at` |
| 2 | 无过期令牌时触发 purge | **不落事件**（不许每次签发都写一条，会灌爆审计） |
| 3 | 一次 purge 掉 3 枚 | 一条事件，`count=3`，`subjects` 三个主体都在 |
| 4 | 事件必须**可 JSON 化**（`to_dict()` 不抛） | `json.dumps(event.to_dict())` 成功 |
| 5 | `data` 里**不许出现令牌明文** | 断言 `data` 的序列化串里没有那枚 token 值（用 `token not in json.dumps(...)`）——这是安全腿，别省 |

**改法要点**：
- 新事件类型常量加在 `af_audit.py` 的常量区（与 `DEPLOY_AUDIT` / `WRITE_CONFLICT` 同风格），命名 `TOKEN_EXPIRED_PURGED`
- **import 放函数内**（`from . import af_audit`），别放模块顶——`af_auth` 是启动期最早加载的模块之一，顶层 import 有环风险
- `_purge_expired_locked` 在 `dead` 非空时才落事件；`subjects` 收集 `self._tokens[t].subject`
- ⛔ 不要把 token 值写进 `message` 或 `data`

**验收**：
```bash
PYTHONPATH="E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24/src" python -m pytest tests/unit/test_auth_purge_alerting.py -q
PYTHONPATH="E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24/src" python -m pytest tests/unit -q -k auth
PYTHONPATH="E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24/src" bash gates.sh
```

---

### DB-03 · G9：双令牌名共存无告警（与 DB-02 同通道，可并行）

**现状（已现读）**：`src/autoforge/af_auth.py:208-238` `_load_env()`——新名 `AF_ACCESS_TOKENS` 优先，旧名 `AUTOFORGE_TOKENS` 兜底：
```python
raw = (
    load_secret("AF_ACCESS_TOKENS")
    or load_secret("AUTOFORGE_TOKENS")
    or ""
).strip()
```
**旧名生效时无任何提示**。运维看着配置齐全，实际跑的是回退路径；哪天删旧名会静默改变鉴权面。

**判据腿（先写测试，必须红）**——新建 `tests/unit/test_auth_legacy_env_warning.py`：

| # | 场景 | 期望 |
|---|---|---|
| 1 | 只设 `AF_ACCESS_TOKENS` | **不落** legacy 事件 |
| 2 | 只设 `AUTOFORGE_TOKENS` | 落一条 `TOKEN_ENV_LEGACY_NAME` 事件，`data.legacy_name="AUTOFORGE_TOKENS"`、`data.new_name="AF_ACCESS_TOKENS"` |
| 3 | 两个都设 | 落事件（新名优先的事实要说出来），`data.legacy_name` 与 `data.new_name` 都在 |
| 4 | 两个都不设 | 不落事件 |
| 5 | 单密钥旧名 `AUTOFORGE_API_TOKEN` 同理 | 落事件 |
| 6 | `data` 不含任何令牌值 | 安全腿，同 DB-02 #5 |

`_load_env` 是**启动期一次性**，所以事件只落一次是天然正确的——不需要额外去重。

**改法要点**：与 DB-02 同一个 `af_audit` 通道、同一个函数内 import 纪律。**⛔ 不要用 logger**（同上，af_auth 当前 0 留痕站点）。

**若 DB-02 已合并**：在同一个 `af_audit.py` 常量区加 `TOKEN_ENV_LEGACY_NAME`，别新建文件。

**验收**：同 DB-02 的三条命令，测试文件换成 `test_auth_legacy_env_warning.py`。

---

### DB-04 · G10 判据腿：canary duration 掐链（只写测试，不改源码）

这是本轮**最值得做**的一项，因为它是唯一「判据腿本身就是交付物」的任务——修法有两个方向（见下），选哪个是架构决策，归 PM；但**无论选哪个，判据腿先写出来都是净收益**。

**缺陷（已实测定位，2026-10-24；断点已现读逐行确认）**：canary 带 `duration` 挂在**中间 do 节点**时，整条链后半段静默不执行。

真实路径（`src/autoforge/af_executor.py`，行号现读 2026-10-24）：
```
canary_observe 计时器 → scheduler else 分支 → timeout() → resume(instance, "on_timeout")
→ :248  pending_canary 恢复分支：漂移检查照做（verified / failed / unmodeled 三态都落）
→ :332  edge = auto.pick_edge(node.id, {kind, "default"})
        af_draft 给 do 节点只发 then / on_error 边（af_draft.py:337-372 现读）
        ⇒ {on_timeout, default} 都匹配不上
→ :334  edge is None ⇒ self._terminate(instance)  ⇒ 链条后半段永不执行
```

**⚠️ 一个必须弄清的细节**（免得豆包把缺陷形状搞错）：执行器侧的 canary 恢复逻辑**是好的**——
漂移检查、回滚、三态证据（`_feed_canary_evidence`）全都跑了，异常还有双兜底不留假绿。
**坏的只有最后一跳选边**。所以测试要断言的不是「canary 检查没跑」，而是「canary 检查跑了、
证据落了、但下游节点没被调度」。

**现场教训**：书房场景验证时，第二个 climate 节点（set_temperature）带 `duration: 300` 后从来没跑过。规避法（已写入 `docs/书房场景自动化_试演金丝雀验证_20261024.md`）：canary 只写 `auto_rollback` 不写 `duration`。

**判据腿（先写测试，必须红）**——新建 `tests/unit/test_canary_duration_chain_break.py`：

| # | 场景 | 期望（**按缺陷现状钉，不是按理想行为钉**） |
|---|---|---|
| 1 | 三节点链 `i2 → d3(canary+duration) → d4`，仿真到计时器超时 | `d4` 的动作**从未被调用**（这是缺陷的实物形状） |
| 2 | 同图，`d3` 的 canary **不带** duration | `d4` 正常执行（规避路径仍通的护栏） |
| 3 | 模拟结束时，`d3` 的 canary 证据**确实落了**（`_feed_canary_evidence` 那一路跑通） | 证明断点在选边而不在漂移检查——别把「检查也没跑」误当成本缺陷 |

**⚠️ 这是本任务最容易做错的地方**：测试要钉**当前的缺陷行为**（`d4` 未被调用），不是钉你希望的修后行为。修好后这个测试会红——那是信号，说明架构决策落地了，由 PM 改成钉新行为。**别为了让测试绿而把断言写成"允许 d4 不被调用"。**

**修法方向（PM 待裁，豆包只记录不改）**：
- ① `af_draft` 给带 `duration` 的 do 节点补一条 `on_timeout → next` 边
- ② `af_executor.py:332` 的 `pick_edge(node.id, {kind, "default"})` 在 canary 恢复分支补 `then` 兜底
  （注意：改 `af_executor.py` 会动进程模型/信任边界台账，这条路 PM 接，豆包别动）
- 判据腿本身的改进：断言**第二个节点的适配器调用真的发生了**，而不只是断言 AC=cool（当前书房场景的判据只验终态，漏判"后半段没跑"）

**验收**：
```bash
PYTHONPATH="E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24/src" python -m pytest tests/unit/test_canary_duration_chain_break.py -q
PYTHONPATH="E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24/src" bash gates.sh
```

**⛔ 本任务不许改 `src/` 下任何文件。** 只加 `tests/unit/test_canary_duration_chain_break.py` 一个文件。

（若豆包在写判据腿时发现 `af_draft.py:337-372` 的边生成**本身**有可动手处——比如方向 ① 只改 `af_draft.py`
不碰 `af_executor.py`——**仍然不要动**。`af_draft.py` 虽不在禁区表，但 DB-01 正在改它；
两笔 commit 同时改一个文件，台账同批判定会算不清。记进 blockers，PM 排先后。）

---

## 3. 明确不派给豆包的（PM 自己接）

| 项 | 原因 |
|---|---|
| **G6** `af_apply` 主体透传 | 断点在 `af_mcp.py:411` `_t_apply` 不接 `current` + `af_apply.py:199` `submitted_by="af_apply"` 硬编码。改 `af_mcp.py` 撞发布线台账 + `_TOOLS_WITH_CONTEXT` 语义 |
| **G3** FakeHA climate 建模 | `af_vhass/fake.py` 的 `service_effect` 是仿真底座契约，改它影响所有既有仿真的期望值，需全量回归 |
| **G7** staging 持久化 | `af_draft.py:82` 进程级全局 `_staging`。落盘牵动原子写门 + 有界缓存门 + 仓库卫生清单 |
| **发版** | 1.13.4 bump / push HEAD:master / tag / WebUI 升级，PM 统一做 |

---

## 4. 豆包交付格式（每笔 commit 的回执）

在 commit message 或单独 `docs/db_task_NN_receipt.md` 里给出：

```markdown
## DB-NN <标题>
- 判据腿：tests/unit/test_xxx.py（N 条，修前红→修后绿）
- 改了哪些文件：<逐个列，标注是否在禁区清单里>
- gates.sh 退出码：<0/1/2> + 完整输出贴下面
- 新增 except-pass：0（若 >0 说明为什么）
- 新错误码 / 新审计事件类型：<名字 + 为什么新建而不是复用>
- 我没做的事：<明确列出，防止 PM 误以为做了>
```

**最后一条是硬要求。** 上一轮 G1 修复的教训：测试绿 ≠ 链路通，「我没验证 X」必须显式写出来。

---

## 5. 时间盒（免费额度 10-25）

| 顺序 | 任务 | 预估 | 依赖 |
|---|---|---|---|
| 第 1 批（并行） | DB-01 | 短（纯类型闸 + 5 条测试） | 无 |
| 第 1 批（并行） | DB-04 | 中（要读 scheduler/canary 路径） | 无 |
| 第 2 批（串行） | DB-02 → DB-03 | 中 → 短 | 02 先落 `af_audit` 新常量，03 复用 |

**若额度不够**：保 DB-01 + DB-04（一个是真缺陷修复、一个是判据腿资产）。DB-02/03 是告警补强，可以等下一批。

**若某项卡住超过 30 分钟**：写进 `docs/db_roadmap_blockers.md`（文件路径 + 卡在哪一行 + 你试过什么），换下一个任务，不要死磕。
