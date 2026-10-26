# 豆包（db）入门：开工前必读（2026-10-26）

> 你是新 agent、新对话，没有 AutoForge（下称 **AF**）的任何上下文。这份文档给你**全部前置环境**。
> 读完这份 + 第四批路线图，你就可以开工，不需要再问 PM 要背景。
> 预计阅读 15 分钟。**⛔ 不要跳过 §1 和 §2**——那两节是用前三批 30 failed + 三门红的代价换来的。

## 这个仓为什么有这么多规矩（先给你心理准备）

AF 是一台**跑在 NAS 上、管真实家电的生产系统**。它有三类门禁，每一类都对应一次真实事故：

| 门禁 | 防什么 | 出过什么事 |
|---|---|---|
| 台账同批（`check_ledger_same_batch`） | 改了代码但名册没跟上 ⇒ 后来人读到的地图是假的 | db 第二批分两笔提交 ⇒ B 腿红 |
| 静态门（AST 扫描 / 计数棘轮） | 悄悄加一条吞异常、悄悄放宽基线 | 基线 16 枚 `except-pass` 只减不增 |
| 判据腿 | 声明"我修好了"但没有能变红的证据 | 变异测试证明空门（`||`→`??` 让中间档塌了） |

**所以这个仓的核心文化是：任何"好了"都要有能变红的反例。** 你写的每条判据腿，
PM 会问"把它改坏会不会红"。写不出反例的断言＝空门。


---

## 0. 你是谁、我是谁、这个仓是什么

| 角色 | 是谁 | 干什么 |
|---|---|---|
| **PM** | Claude（另一个会话） | 定规格、复核你的代码、统一发版。**不写业务代码** |
| **开发** | **你（豆包 db）** | 按路线图写代码 + 判据腿 + 台账同批 |
| **出资人** | lidicn | 做产品裁定。**不输命令**——执行层面的东西都由 PM 或你做 |

**AF 是什么**：NAS（`192.168.2.200`）上跑的智能家居自动化平台，Docker 部署，端口 `8787`。
三条对外面目：

| 面 | 地址 | 给谁用 |
|---|---|---|
| 用户端 WebUI（**mimo**） | `http://192.168.2.200:8787/mimo/` | 出资人（浏览器） |
| 开发控制台（`ui/`） | `http://192.168.2.200:8787/` | 开发者（浏览器），含 `/governance` 治理页 |
| MCP 面 | `POST http://192.168.2.200:8787/mcp` | agent（你将来也可能是） |

**代码结构**：`src/autoforge/` 是主体（约 40 个 `af_*.py` 模块），`tests/` 是判据腿，
`docs/` 是台账与规格，`ui-user-mimo/` 和 `ui/` 是两棵前端树。
**当前版本 1.13.5**（`autoforge/__init__.py`、`af_service.py`、`pyproject.toml` 三处同值）。

---

## 1. 环境（每条都现读验过，2026-10-26）

### 1.1 工作目录——只能在这一个地方干活

```bash
cd "E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24"
```

- 这是一个 **git worktree**，当前分支 **`db/task-01`**。
- ⛔ **不要 `cd` 到 `E:/NAS/AutoForge`（主 checkout）**。主 checkout 的 HEAD 是 `b4c6ddb`，
  它**不在发布线历史上**（落后 61 笔、领先 1 笔）。在主 checkout 上跑测试会读到旧代码。
- ⛔ **不要用 `git stash` / `git stash pop`**（裸命令）——stash 栈与主 checkout **共享**，
  会 pop 到别人的改动。必须用时：`git stash push -u -m "db-<任务号>"`，
  立刻 `git stash list --format='%H %gs'` 记下 SHA，恢复用 `git stash apply <sha>`（**不是 pop**）。

### 1.2 每条 python/pytest 命令都必须带 PYTHONPATH

本 worktree 的 `src` 不在默认 import 路径：

```bash
PYTHONPATH="E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24/src" python -m pytest tests/unit/xxx.py -q
```

不带会 `ModuleNotFoundError: autoforge`，**或更糟**——静默 import 到主 checkout 的旧代码，
测试绿但你改的不是同一份。**这一条没有例外。**

### 1.3 `gates.sh` 必须带 `GATES_PYTHON=python`

```bash
GATES_PYTHON=python bash gates.sh
```

`gates.sh:23` 是 `PYTHON="${GATES_PYTHON:-python3}"`。本机裸 `python3` **找不到 `homesdk`**
（私有 wheel 装在 `python` 的 user site-packages 里）⇒ 门禁会以"环境不对"失败。
**退出码语义：0 过 / 1 有未获批违规 / 2 环境或配置不对（⛔ 2 不是绿）**。

### 1.4 现读环境读数（2026-10-26）

| 项 | 读数 |
|---|---|
| python | 3.13.2（`python`），`homesdk` 可导入 |
| node | v24.14.0（mimo 前端测试用） |
| `ui-user-mimo/node_modules` | **已装好**（`npm install` 不用重跑） |
| 全量单测规模 | **3844 条**，collect 约 18 秒 |
| mimo 测试跑法 | `cd ui-user-mimo && npm test` |
| 当前分支 / HEAD | `db/task-01` / `03e0dc0` |
| 发布线 | `origin/master@9c6e4af` ＝ 标签 `v1.13.5`，本 worktree HEAD **已含它全部提交** |

### 1.5 ⚠️ 开工第一件事：证明你读的是发布线

```bash
cd "E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24"
git merge-base --is-ancestor HEAD origin/master && echo "OK: HEAD 含发布线" || echo "FAIL: 不在发布线"
```

**退出码必须为 0。** 把输出贴进回执。

**为什么立这条**：本批立项前，一份评审报告读了主 checkout（`b4c6ddb`，落后发布线 61 笔），
得出"`_admin` 不存在""owner 通道不存在""agent 令牌会 24h 过期"等结论，**5 条核心指控里 4 条作废**，
整轮返工。**读错树会以高置信度产出错误结论**，这是本仓最典型的失败形态。

---

## 2. 铁的纪律（四条，都是实测踩过的）

### 2.1 台账清单：不是"不许碰"，是"必须同批碰"

这个仓有一道**机械前置**（`scripts/check_ledger_same_batch.py`）：

> 凡改动会移动 `file:line` 锚点的提交，与它对应的清单**必须在同一笔提交里**重生成；分两笔 ⇒ 判红。

五份名册清单：`docs/可观测性清单.md`、`docs/进程模型清单.md`、`docs/信任边界清单.md`、
`docs/发布线清单.md`、`docs/仓库卫生清单.md`。

```bash
# 改了 src/autoforge/ 下任何文件后，逐个跑受影响的生成器：
PYTHONPATH=".../src" python scripts/check_observability.py --write   # af_audit/af_api 等
PYTHONPATH=".../src" python scripts/check_process_model.py  --write   # af_auth/af_store 等
PYTHONPATH=".../src" python scripts/check_trust_boundary.py --write   # 改路由档位时
# 然后把重生成的清单与源码改动放进同一笔 commit
```

**三条必须注意的**：

1. **`--write` 只重写自动段**（`<!-- AUTO-BEGIN/END -->` 之间）。手工段（如
   `docs/可观测性清单.md` 的「## 三、具名码登记」）要**手工同步行号**，否则 B 腿判"过期登记"。
   db 在 DB-02 漏过这一节，PM 补的。
2. **`--write` 会破坏 CRLF**（实测：`write_text` 不保留行尾，整个文件 CRLF→LF ⇒ 480 行假 diff）。
   **跑完必须修行尾**：
   ```bash
   python -c "import pathlib; p=pathlib.Path('docs/可观测性清单.md'); b=p.read_bytes(); p.write_bytes(b.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'))"
   ```
   验证用 `git diff --ignore-cr-at-eol --numstat`——**CRLF round-trip 做好了的标志是"只有新增行"**；
   看到整文件 delete+insert 就是你修行尾失败了。
3. **清单 regen 必须与源码改动同一笔**。分两笔 ⇒ B 腿判红。

### 2.2 ⛔ 绝对不许碰的文件（碰了就停下写 blockers）

| 文件 | 原因 |
|---|---|
| `scripts/check_*.py` | 门禁自身，改了等于自己给自己打分 |
| `.gates.toml` / `.gates-baseline.txt` / `.gates-tally.txt` | 基线冻结与计数棘轮 |
| `src/autoforge/af_executor.py` / `af_canary.py` | 进程模型 + 信任边界台账逐行钉（canary/ask/resume 路径） |
| `src/autoforge/af_vhass/fake.py` | 仿真底座，改动影响全量既有仿真期望值 |

⛔ **如果你认为必须改上面某个文件：停下来，写进 `docs/db_roadmap_blockers.md`（文件路径 + 卡在哪一行 + 试过什么），不要动。**

⚠️ **`af_api.py` / `af_mcp.py` / `af_auth.py` 不是禁区，但它们是台账重点盯防对象**：
- `af_api.py` 发布线台账逐锚点钉 + 信任边界清单 94 条路由逐行对撞
- `af_auth.py` 在进程模型清单有锚点（`threading.Lock` 站点）
- **第四批的批 0/批 2/批 4 都要改它们** ⇒ 每批都必须按 §2.1 同批带台账，且按 §2.4 跑对应 pin。

### 2.3 ⛔ 不许出现的写法

- **不许** `except Exception: pass` / `except: pass`——`scripts/check_except_pass.py` 是棘轮门，
  基线 16 枚只减不增，**新增一枚直接红**。真要容错：具名捕获 + 具名码 logger 或 `af_audit` 事件。
- **不许** 直接 `git push origin master` 或打 tag——**发版由 PM 统一做**。
- **不许** commit message 里出现真实令牌值 / 授权码值（仓库 public，分类器会拦）。

### 2.4 ⚠️ 两套东西，都要跑（`gates.sh` 只读前者）

**`gates.sh` 跑的是静态门**（AST 扫描 / 台账对账 / 计数棘轮 / 读 HEAD 树），**不跑单测**。
db 第二批汇报"gates 全绿"是真的，但那次全量单测是 **30 failed**。

改了 `src/autoforge/` 下任何文件后，除了 `--write` 台账，**还要跑对应的 pin 测试**：

| 你改的东西 | 除了 `--write` 台账，还要跑 |
|---|---|
| 新增/移动留痕站点 | `tests/unit/test_observability_gate.py` |
| 新增 except-pass 或行漂 | `tests/unit/test_except_pass_gate.py` |
| 动发布线载体 | `tests/unit/test_release_line_gate.py` |
| 动进程模型 | `tests/unit/test_process_model_gate.py` |
| 动信任边界/路由档位 | `tests/unit/test_trust_boundary_gate.py` |
| 动 owner 主体判定 | `tests/unit/test_owner_subject_gate.py` |
| 动前端调用点 | `tests/unit/test_ui_api_paths_gate.py` |

**这是 PM 自己踩过的坑**（2026-10-24）：跑了 `--write` 台账所以门禁绿，漏了同文件的测试 pin ⇒ 全量 3 failed。

### 2.5 ⛔ 判据腿的顺序污染：`os.environ` 一律走 `monkeypatch.setenv`

**2026-10-24 实测踩到，代价是全量单测 30 failed / 3760 passed，而 `gates.sh` 同期全绿。**

db 的 DB-03 判据腿用裸 `os.environ["AUTOFORGE_TOKENS"] = ...` 设密钥名，配了一个 autouse fixture 做
`monkeypatch.delenv(...)` 清理。**这个组合无效**——`monkeypatch.delenv` 只恢复 monkeypatch 自己记录的修改，
对裸 `os.environ` 赋值不负责恢复。结果那几个密钥名残留在进程环境里，后续 **19 个测试**
读到"新名优先"而拿不到自己设的旧名令牌 ⇒ `authenticate` 返回 None ⇒ 全红。

**为什么自检发现不了**：跑自己那个文件全绿。只有按 pytest 收集顺序跑（全量）才红。

```python
# ✗ 会污染（fixture 救不回来）
os.environ["AF_ACCESS_TOKENS"] = json.dumps({...})

# ✓ 正确（monkeypatch 自动恢复）
def test_xxx(monkeypatch):
    monkeypatch.setenv("AF_ACCESS_TOKENS", json.dumps({...}))
```

**同理适用于**：模块级单例（`af_watch._default._by_auto`、各种 `_DEFAULT` 缓存）、
`sys.path` 修改、`logging` handler、cwd。**判据腿要么用 monkeypatch/fixture 管好生命周期，要么显式还原。**

**提交前必跑**（比跑自己那个文件多花不了多少时间）：
```bash
PYTHONPATH=.../src python -m pytest tests/unit/test_你的腿.py \
  tests/unit/test_v0_8_auth.py tests/unit/test_v1_4_token_expiry.py \
  tests/unit/test_dcd_20261004_auth_limits.py -q
```

---

## 3. 怎么读这个仓的代码（三条约定）

1. **行号会漂**——这仓长期有并行写者（多个 Claude worktree + db 分支）。
   **引用一律写 `文件:行号（ref@commit）`**，本批用 `origin/master@9c6e4af`。
   看到行号对不上时，**以函数名为准**。
2. **⛔ 不要把工作区行号与对象库行号混用**。要核对发布线某一行：
   ```bash
   git show origin/master:src/autoforge/af_api.py | sed -n '536p'
   ```
3. **注释里常有裁定记录**（`裁定 2026100X §一 Qx.y`）——那是历史决策，
   **不要当普通注释跳过**，也不要随手改。它们往往是门禁判据的依据。

---

## 4. 交付格式（每批一段，追加到 `docs/db_task_receipt.md`）

```markdown
## 批 N <标题>
- 判据腿：<文件>（N 条；修前红→修后绿；**改了哪几条既有断言、为什么**）
- 改了哪些文件：<逐个列，标注是否在 §2.2 禁区清单里>
- 必红既有测试：<先跑的原始输出> → <修法> → <修后输出>
- 门禁输出：gates.sh 完整输出 + 退出码
- 台账同批：--write 的 diff（若报了漂移）；CRLF round-trip 验证（--ignore-cr-at-eol --numstat）
- 测试 pin：跑了哪几个 test_*_gate.py，输出
- 顺序污染自检：§2.5 那条命令的输出
- 新增 except-pass：0（若 >0 说明为什么）
- 我没做的事：<明确列出>
```

**三条硬要求**：

1. **改了既有测试断言方向的，必须单独说明改前改后各是什么**——PM 要能复核这个翻转是否合理
   （例：DB-05 把"钉 d4 不跑"翻转成"钉 d4 跑"，那是缺陷修好后的正向证据，不是放宽）。
2. **⛔ 不许为了让测试绿而放宽断言。** 红了就停下来写 blockers。
3. **一笔 commit 一件事**。commit message 用 `fix(scope):` / `test(scope):` / `feat(scope):` 前缀。
   ⛔ 不要做 `chore(release): bump` ——那是 PM 的活。

---

## 5. 路线图索引（按时间顺序，都要读）

| 文件 | 内容 | 你必须读哪节 |
|---|---|---|
| [`db_roadmap_batch4_identity_20261026.md`](db_roadmap_batch4_identity_20261026.md) | **本批：身份层一次治理**（6 道工序 5 批） | **全文** |
| [`db_roadmap_batch3_20261025.md`](db_roadmap_batch3_20261025.md) | 第三批 DB-07（G6 主体透传） | §0.1-0.4、§1.2-1.6 |
| [`db_roadmap_batch2_20261024.md`](db_roadmap_batch2_20261024.md) | 第二批 DB-05/DB-06 | §0（隔离度判据怎么读） |
| [`db_roadmap_20261024.md`](db_roadmap_20261024.md) | 第一批 DB-01~04 | **§1.1-1.9 全文**（纪律源头） |
| [`db_task_receipt.md`](db_task_receipt.md) | 历史交付回执 + PM 复核记录 | PM 复核那几段（看 PM 挑过什么刺） |
| [`缺口登记_实测自动化_20261023.md`](缺口登记_实测自动化_20261023.md) | 缺口册 G1-G24 | §126 起第二批/第三批/第四批（G11-G24） |

**第四批的内部顺序**（详见路线图 §8）：

```
批 0 堵四把刀（必保）
  ↓
批 1 D5 af_apply 补 owner（必保；与 DB-07 同函数须同笔）
  ↓
批 2 存储层迁移：明文键→摘要 + principal_kind（必保；要停机，PM 约窗口）
  ↓
批 3 治理面看得见 / 批 4 RBAC 收口 / 批 5 /pair 链路（可挪后）
```

**额度不够就保批 0 + 批 1**——那是"今天就能被利用"的洞和数据正确性，其余是治理完备性。

---

## 6. 卡住怎么办

**卡住超 30 分钟**：写 `docs/db_roadmap_blockers.md`，格式：

```markdown
## <任务号> <一句话标题>
- 文件 + 卡在哪一行：
- 试过什么：
- 需要 PM 裁什么：
```

然后**换下一个任务**。⛔ 不要在一个任务上耗，也不要靠改断言收场。

---

## 7. 自检清单（每笔 commit 前过一遍）

```
□ git merge-base --is-ancestor HEAD origin/master 退出码 0？（输出贴回执）
□ gates.sh 完整输出贴了？退出码 0（不是 2）？
□ 碰了 §2.2 禁区文件吗？碰了就停下。
□ 新增 except.*pass 了吗？加了就重写。
□ 改了 src/autoforge/ 下的文件 ⇒ --write 台账跑了吗？CRLF 修了吗？与源码同一笔吗？
□ 改了 src/autoforge/ 下的文件 ⇒ 对应的 test_*_gate.py 跑了吗？（§2.4）
□ 判据腿用 monkeypatch 了吗？（§2.5）顺序污染自检跑了吗？
□ 改了既有断言方向吗？改前改后各是什么，写进回执了吗？
□ commit message 里没有令牌值/授权码值？
□ 一笔 commit 只做一件事？
```
