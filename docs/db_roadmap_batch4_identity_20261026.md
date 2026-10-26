# 豆包（db）执行路线图 · 第四批：身份层一次治理（2026-10-26）

> **PM：Claude（本会话，已改任 PM）· 开发：豆包（db）**
>
> ## ⚠️ 新 agent 第一件事：先读 [`db_START_HERE_20261026.md`](db_START_HERE_20261026.md)
>
> 那份是**前置环境全套**：工作目录、`PYTHONPATH` 前缀、`GATES_PYTHON=python`、
> 五份台账清单的同批纪律、CRLF round-trip、判据腿顺序污染、禁区文件表、
> 交付格式、自检清单。**本路线图假定你已经读过它**，不再重复那些内容。
>
> 前置：第三批 DB-07 已派（`docs/db_roadmap_batch3_20261025.md`），可与本批**并行或串行**——
> DB-07 改 `af_mcp.py`/`af_apply.py`，本批批 1 也改 `af_apply.py`，**两批会在同一函数相遇**。
> 若并行：DB-07 加 `authenticated_subject` 形参，本批批 1 给 `save_payload` 补 `owner`，
> 两者改的是同一处的两件事，**必须同一笔 commit**（见 §0.5）。

---

## 0. 基线与纪律

### 0.1 基线声明（全文适用）

本规格**所有 `文件:行号` 一律取自 `origin/master@9c6e4af`**（＝发布标签 `v1.13.5`）。
本 worktree（`db/task-01`）源码四文件 `af_api.py` / `af_auth.py` / `af_apply.py` / `af_store.py`
与 `origin/master` **零差异**（现读 `git diff --stat origin/master HEAD -- <四文件>` 空输出），
故本 worktree 的行号与发布线一致。**但主 checkout 不是**——它的 HEAD 是 `b4c6ddb`，
`git merge-base --is-ancestor b4c6ddb origin/master` 退出码 1，落后发布线 61 笔。

⇒ ⛔ **不要读主 checkout 的代码来核对本规格**，也不要在主 checkout 上验判据腿。

### 0.2 本批为什么存在（一次治理，不再返工）

出资人裁定（2026-10-26）：三组令牌——**用户**（登录 WebUI、签发管理 agent 令牌、管理自动化）、
**agent**（MCP 编自动化）、**开发者 agent**（管理面 + 运维面）——**独立存放、权限清晰、不能越权**。
同时要求两件事易用：

1. 开发者在 `http://192.168.2.200:8787/governance` 点一下就签发开发者 agent 令牌，**明文只现一次**；
2. 用户在 mimo 输入 agent 名 → 点生成 → **复制一段配对链接**给 agent → agent 自行完成配对。

本批把历史上零散修过的 auth 面（三级身份制、admin 档、owner 通道、配对 bootstrap、长期码）
**收敛成一张权限矩阵**，并把上面两件事的链路做通。**目标是不再有第三批返工。**

### 0.3 已核实的发布线缺陷（本批要修的，逐条带证据）

| # | 缺陷 | 发布线证据 | 严重度 |
|---|---|---|---|
| D1 | 授权码铸造只挂 `_write`，**任何 write 令牌可自助铸 180 天长期码**，再用它走 `af_save` 快速通道**直接部署、绕过人审** | `af_api.py:1380` `@app.post("/api/user/auth-code", dependencies=[Depends(_write)])`；`:1386-1387` 把**明文码**放响应里；`af_auth.py:59-77` `LONGCODE_TTL_DAYS_DEFAULT = 180.0`；`af_mcp.py:247-252` `submit_pending` 后紧接 `approve_pending(reviewer="auth_code:…")` 并标 `deployed_via` | **阻断级**：绕的是人审这个最终闸 |
| D2 | 未注册管理员时，**任意非空凭据换 `subject="owner"` 全权限令牌**；而 `af_api.py:506` 让 owner 面视同持 admin ⇒ 未注册部署上任何人都能跑系统更新、删归档 | `af_api.py:1149-1159`；`:502-507` owner 名单通道 | **阻断级** |
| D3 | HTTP 导入面**不传 owner** ⇒ 一枚 write 令牌可以 `strategy=overwrite` 覆盖任意他人归档，且覆盖后新记录 owner 是空串 | `af_api.py:669-672`（`_svc` 只做错误映射，不注入主体）；`af_service.py:2265-2271 import_store(..., owner: str = "")`；`af_store.py:864-866 if owner:` ⇒ owner 空时守卫不生效 | 高 |
| D4 | `af_set_tags`（MCP）与 `/api/graphs/tags`、`/api/graphs/enable\|disable`（HTTP）**无归属判定** ⇒ 可改任意归档的标签与启停 | `af_mcp.py:847`、`:861`；`af_api.py:651-661`；`af_service.py:373-376 set_graph_tags` 覆盖式写 | 高 |
| D5 | `af_apply` 入队的 payload **无 `owner`** ⇒ 审批后归档 owner 是空串，前端组标题空、所有权隔离对它不生效 | `af_apply.py:199` `save_payload = {"ir": ir_payload, "name": staged.get("summary","")}`；`:203-204 submitted_by="af_apply"`；`:394` 同。对照：`af_mcp.py:263` 的 `_t_save` **已传** `authenticated_subject` | 高（＝ 册里 G20） |
| D6 | `issued_tokens.json` 的**键就是明文令牌**，`revoked.json` 也是明文清单 ⇒ 任何能读 `{store_root}/.auth/` 的进程握有全部活令牌 | `af_auth.py:539-573` `_persist_issued`，`:572 data[token] = entry`；`:334 _persist_revoked`；`:347-378 authenticate` 用 `hmac.compare_digest` 比内存键 | 高 |
| D7 | `rename_subject` **无保留名校验、无碰撞检测** ⇒ owner 把 agent 改名成 `owner`/`shared` 即提权；改成已存在的名字则静默合并两族令牌 | `af_auth.py:491-501`；端点侧 `af_api.py:1435-1448` 只校验非空 | 高（提权路径） |
| D8 | `GET /api/auth/me` 对**任何**令牌回 `role: "admin"` ⇒ 配对 agent 会以为自己是管理员 | `af_api.py:1179-1185`，`:1185 return {"user": {"username": …, "role": "admin"}}` | 中 |
| D9 | `subjects()` 只回 `subject` + 合并 `scopes`，治理页看不到 TTL / 令牌种类 ⇒ 出资人截图里那笔"分不清哪个是人的、哪个是 agent 的"糊涂账 | `af_auth.py:389-394`；治理页 `ui/src/views/GovernanceView.vue:159-169` 还要**手填明文令牌串**才能撤销 | 中 |
| D10 | mimo 配对文案写"生成 **6 位**配对码"，后端早已是 8 位 | `ui-user-mimo/src/components/PairingModal.vue:70`；`af_auth.py:617-624 _rand6`（函数名历史遗留，实际 8 位） | 低 |
| D11 | 用户登录令牌 24h **固定** TTL，不续期 ⇒ 每天要重登 mimo，且过期时报"令牌已过期"让人误以为是目标 agent 过期（＝ 册里 G22） | `af_auth.py:184 ISSUED_TTL_S = 86400.0`、`:418-419`；`af_api.py:1170-1177 logout` 无 scope 门 | 中 |

**不在本批修（已裁）**：D6 的 GitHub 历史纠正（出资人 2026-10-25 裁"github 上没必要纠正"，
那枚泄露令牌已作废，现读 `/mcp` 打它得 403）。

### 0.4 三组令牌终态（本批交付的形状）

| | 用户 | agent | 开发者 agent |
|---|---|---|---|
| 谁用 | 出资人（浏览器） | 豆包 / DeepSeek++ / claude-code 等 | 豆包 / Claude |
| 在哪拿 | mimo / `ui/` 登录 | mimo 配对链接 | `/governance` 签发 |
| `principal_kind` | `login` | `agent` | `developer` |
| subject | `owner`（固定） | agent 自报名 | 用户填的 agent 名 |
| scopes | `read,write,live` + owner 通道⇒admin | `read,write,live`（**永不 admin**） | `read,write,live,admin` |
| TTL | 空闲续期 + 绝对上限（见 §5 批 2） | **无**（`ttl_s=0`） | **无** |
| 明文 | 只在签发响应一次 | 只在 redeem 响应一次 | **只在 `/governance` 显示一次** |
| 能删谁的自动化 | 全部 | **只删自己的** | 全部 |
| 能删谁的令牌 | 全部 | **只撤自己**（走 `POST /api/auth/logout`） | 全部 agent 令牌（不能撤 owner） |

⛔ **`admin` 永不经配对自动获得**（这条自 2026-10-23 三级身份制裁定起就是铁律，本批不松动）。
⛔ **`developer` 令牌不得签发 `developer` 令牌**（否则自我放大）。
⛔ **`developer` 令牌不拿 `_OWNER_SUBJECTS` 身份**——它要能动别人的 agent，
走显式的 `requires_governance()` 依赖，不借 owner 的主体名（见 §5 批 4）。

### 0.5 与 DB-07 的交接（若两批并行）

DB-07 改 `af_apply.py` 的 `apply()` 加 `authenticated_subject` 形参；本批批 1 要改同一函数的
`save_payload` 补 `owner`。**两者必须落在同一笔 commit**，否则会出现"传了 subject 但 payload
没 owner"或反之的半成品。若 db 串行做，先做 DB-07 再做批 1，交接点在回执里写明
"DB-07 已合入，批 1 从 af_apply.py 的哪一行起改"。

### 0.6 新纪律（本批第一次用，此后每批都适用）

> **任何以"发布线就是这样"为立论前提的结论，必须先跑
> `git merge-base --is-ancestor <commit> origin/master`，退出码必须为 0。**
> 结论一律写 `ref@commit`（推荐写标签 `v1.13.5`），缺者视为未核实。
> 抽取行号只用 `git show ref:path` 或在**该 commit 的检出**里读，⛔ 不把工作区行号与对象库行号混用。

**为什么立这条**：本批立项前，一份评审报告读了主 checkout（`b4c6ddb`，落后发布线 61 笔），
得出"`_admin` 不存在""owner 通道不存在""agent 令牌会 24h 过期"等结论，5 条核心指控里 4 条作废。
代价是一整轮返工。**这是基线错误的典型形态，不是粗心。**

---

## 1. 批 0：堵四把今天就能用的刀

**目标**：不牵动身份模型，把 D1/D2/D3/D4 四个"今天就能被利用"的洞堵上。

### 1.1 改动清单

| # | 洞 | 改法 | 落点 |
|---|---|---|---|
| ① | D1 授权码铸造绕过人审 | `POST /api/user/auth-code` 从 `_write` 收到 **owner 面**（新依赖 `requires_owner()`，判 `subject ∈ _OWNER_SUBJECTS`）；`DELETE /api/user/auth-code/{code}` 同 | `af_api.py:1380`、`:1404` |
| ①' | 同上·明文返回 | 铸造响应**仍返回明文**（用户要拿去用），但**只在响应里出现一次**，前端已如此展示（`ui-user-mimo/src/views/AuthCodesView.vue`）。⛔ 不要改成"只回掩码"——那会让码没法用 | `af_api.py:1387` 不动 |
| ② | D2 未注册换 owner 令牌 | `af_api.py:1151-1159` 的兼容分支改成 **fail-closed**：未注册管理员时**拒登录**并回 `{"ok": False, "need_register": True}`，让前端跳注册页。⛔ 不要保留"任意非空凭据" | `af_api.py:1151-1159` |
| ③ | D3 导入不传 owner | `af_api.py:669-672` 传 `owner=info.subject`（与 MCP 面 `af_mcp.py:203-211` 同口径） | `af_api.py:672` |
| ④ | D4 标签/启停无归属 | `set_graph_tags`（`af_service.py:373-376`）与 `set_automation_enabled`（`:558-566`）加**归属判定**：`record.owner` 非空且 ≠ 当前主体 ⇒ 403；`owner` 为空 ⇒ 仅 owner 面可改。MCP 侧 `af_set_tags`/`af_enable_by_tag` 自动继承（它们调同一批 service 函数） | `af_service.py:373-376`、`:558-566` |

### 1.2 ⚠️ 三个现读约束

1. **② 会改前端登录流程**。`ui/` 的 `LoginView.vue` 与 mimo 的 `LoginView.vue` 都要处理
   `need_register`：跳注册页而不是报"密码错误"。**生产当前是已注册状态**（`/api/auth/has-admin`
   现读 `{"ok":true,"has_admin":true}`），所以这条改动**不影响线上登录**，只影响"全新部署首次启动"。
2. **③ 会红既有测试**。`tests/unit/` 里凡是直接调 `svc.import_store(store, bundle, strategy)`
   不带 owner 的用例，改后若断言 200 而现在因归属判 403，**按它的失败消息修测试**，
   ⛔ 不许为了让绿放宽断言（第一批 §1.3.1 纪律）。先把 `pytest -k "import" -q` 跑出来贴进回执。
3. **④ 的"空 owner 仅 owner 面可改"需要一个前置**：批 1 的 D5 修完，`af_apply` 产出的归档才有 owner。
   本批先按"空 owner ⇒ owner 面可改"实现，**语义上安全**（最严），不会因为 D5 未修而漏。

### 1.3 判据腿（先写，必须红）

新建 `tests/unit/test_auth_governance_batch0.py`：

| # | 场景 | 期望 |
|---|---|---|
| 1 | 非 owner 的 write 令牌调 `POST /api/user/auth-code` | **403** |
| 2 | owner 令牌调同一端点 | 200（且响应含明文码） |
| 3 | 未注册管理员的部署上，任意非空凭据调 `POST /api/auth/login` | **401** + body 带 `need_register: true`，**不返回 token** |
| 4 | 已注册部署上，错误密码调登录 | 401（不回归） |
| 5 | 非归属人调 `POST /api/store/import`（overwrite 他人归档） | **403** |
| 6 | 归属人调同一端点 | 200 |
| 7 | 非归属人调 `POST /api/graphs/{name}/tags` | **403** |
| 8 | 非归属人调 `POST /api/graphs/{name}/enable` | **403** |
| 9 | owner 令牌调 7/8 | 200（owner 面 bypass） |
| 10 | 空 owner 的归档被非 owner 面改 | **403**（fail-closed，不因 D5 未修而漏） |

腿 1/3/5/7/8/10 是正面证据；2/4/6/9 是护栏。

### 1.4 验收

```bash
PYTHONPATH=".../src" python -m pytest tests/unit/test_auth_governance_batch0.py -q
PYTHONPATH=".../src" python -m pytest tests/unit -q -k "auth or import or tag or enable or login"
PYTHONPATH=".../src" python -m pytest tests/unit -q -k "draft or canary or watch"   # 顺序污染自检
GATES_PYTHON=python bash gates.sh                                                 # 贴完整输出
```

### 1.5 ⛔ 不许碰

`af_mcp.py`（DB-07 的活）、`af_apply.py`（批 1 的活，本批只读）、
`docs/信任边界清单.md`（本批**不动路由档位之外的概念**，但②③④会让清单自动段行号漂
⇒ 按 §1.3.1 同批 `--write` 重生成，⛔ 不要手改自动段）。

---

## 2. 批 1：D5 —— `af_apply` 补 owner（G20）

**目标**：让 `af_apply` 入队的条目带归属，否则所有权隔离对它整体失效、前端组标题空。

### 2.1 改动

```python
# af_apply.py:199（发布线行号）
save_payload = {
    "ir": ir_payload,
    "name": staged.get("summary", ""),
    "owner": owner,          # 新增：由 apply() 的 authenticated_subject 供给
}
```
- `apply()` 加形参 `authenticated_subject: str | None = None`（DB-07 也在加这个，
  **同一处合并**），体内 `owner = authenticated_subject or ""`。
- `:394` 的 `apply_group` 路径：**本批不动**（DB-07 已裁 group 不透传，保持）。
- ⚠️ `tests/unit/test_af_premiere.py:226` 有一条签名钉腿断言
  `sig.parameters["premiere_code"].default is None`——它只钉 premiere_code，
  你加参数不破坏它，但⛔ 不要顺手改它。

### 2.2 判据腿

新建 `tests/unit/test_af_apply_owner_backfill.py`：

| # | 场景 | 期望 |
|---|---|---|
| 1 | `apply(ref, stage="save", authenticated_subject="alice")` | 入队 payload 含 `owner: "alice"` |
| 2 | `apply(ref, stage="save")` 不传 subject | payload 无 owner 键或为空（向后兼容） |
| 3 | 审批该条目 | 落盘归档 `record["owner"] == "alice"` |
| 4 | `af_mcp._t_apply` 带 `current={"subject":"bob"}` 调 | payload owner == "bob"（依赖 DB-07 已合入） |
| 5 | `apply_group` 路径 | 行为不变（护栏，钉住"没动 group"） |

### 2.3 验收

```bash
PYTHONPATH=".../src" python -m pytest tests/unit/test_af_apply_owner_backfill.py -q
PYTHONPATH=".../src" python -m pytest tests/unit -q -k "apply or premiere or mcp or save or pending"
PYTHONPATH=".../src" python -m pytest tests/unit -q -k "owner or apply"
GATES_PYTHON=python bash gates.sh
```

### 2.4 ⛔ 不许碰

`af_service.py`（58 个锚点，本批不需要改它）、`af_executor.py`、`af_canary.py`、
`tests/unit/test_af_premiere.py:226` 那条签名钉腿。

---

## 3. 批 2：存储层一次迁移（明文键 → 摘要 + `principal_kind`）

**目标**：把 D6（明文键）和"三组令牌可分辨"（D9 的一半）一次做完。**出资人已裁接受重启。**

### 3.1 改法（三个落点，一次改完）

**① 键 → SHA-256 摘要**

`af_auth.py`：
- 新增 `_digest(raw: str) -> str = hashlib.sha256(raw.encode()).hexdigest()`（`af_auth.py` 顶部，
  `hmac` 已 import，补 `hashlib`）。
- `_persist_issued`（`:539-573`）：`data[token] = entry` ⇒ `data[_digest(token)] = entry`。
- `_load_issued_file`（`:504-`）：读回来的键是摘要，**内存 `self._tokens` 仍以明文为键**
  （`authenticate` 全内存比对，见 `:347-378`）⇒ 装载时用摘要做索引，`TokenInfo` 加一个
  `digest` 字段或在装载时反向建表。**取前者**：`_load_issued_file` 建
  `{digest: TokenInfo}`，`authenticate` 先用 `_digest(raw)` 查这张表，命中即返回，
  **不再线性扫**（顺带修掉现在的 O(n) 逐钥 `compare_digest`）。
  ⚠️ env 侧令牌（`AF_ACCESS_TOKENS`，`:216 _load_env`）**不落盘、不在这个文件里**，
  走原来的内存路径，不要把它也塞进摘要表。
- `revoke`（`:380-387`）：`self._revoked.add(token)` ⇒ `self._revoked.add(_digest(token))`；
  `_persist_revoked`（`:334`）落盘摘要清单。
- `revoke_by_subject`（`:475-489`）与 `_rewrite_issued`（`:575-616`）：同批改摘要。

**② 契约不变（这一点必须写进回执）**

`POST /api/auth/revoke` 的**请求体仍是明文令牌串**（`af_api.py:1101-1105` `RevokeBody.token`），
响应、状态码、错误语义全不变；治理页那个"粘贴令牌撤销"的输入框（`ui/src/views/GovernanceView.vue:159-169`）
**不用改**。入参是"调用方手里那枚明文"，存储层换摘要不影响它。

**③ 值加 `principal_kind`**

⛔ **不叫 `kind`**——`AuthCode` 已占用 `kind`（取值 `long`/`short`，`af_auth.py:895-903`、`:997`），
同名不同义会让 `issued_tokens.json`、治理页、审计日志三处歧义。
- `TokenInfo`（`:162-174`）加 `principal_kind: str = "agent"`（**默认落在最窄档**）。
- `issue_for_agent`（`:398-429`）加形参 `principal_kind: str | None = None`，
  **只有签发点能定它**，⛔ 不接受调用方从请求体传入（与 P0-13 的 `authenticated_subject` 同一铁律）。
- 三族签发口径：
  - 登录/注册（`af_api.py:1133/:1153/:1163`）⇒ `principal_kind="login"`
  - 配对兑换（`af_auth.py:891`）⇒ `principal_kind="agent"`
  - 本批批 4 新增的开发者签发端点 ⇒ `principal_kind="developer"`
- **存量迁移规则**（写进迁移脚本）：`subject ∈ {owner, shared}` ⇒ `login`；
  其余 ⇒ `agent`；**`developer` 只新发，不回溯**（回溯等于给存量令牌提权）。

### 3.2 迁移脚本（一次性，单独一个文件）

新建 `scripts/migrate_issued_tokens_digest.py`：

1. **前置检查**：`--dry-run` 默认开，打印"将改写 N 条、其中 login X 条 / agent Y 条"，
   必须显式 `--apply` 才写。
2. 读 `{store_root}/.auth/issued_tokens.json`，对每个明文键算摘要，值补 `principal_kind`。
3. `revoked.json` 同批（键是明文数组 ⇒ 逐项摘要）。
4. 用 `af_auth` 的 `_atomic_write_text`（`:113`）写，**写前先备份**到 `.auth/backup-<ts>/`。
5. ⛔ 文件损坏/形状不对 ⇒ **拒写并退出非零**（`_persist_issued:552-556` 已是这个口径，照抄）。
6. ⛔ 脚本内**不打印任何令牌明文或摘要值**，只打印计数（沿用审计纪律）。

### 3.3 部署顺序（出资人已裁接受重启）

```
(i)   备份 .auth/（脚本自己做）
(ii)  停服务或进只读降级（_readonly_guard 已有，af_api.py:546）
(iii) python scripts/migrate_issued_tokens_digest.py --apply
(iv)  重启服务
(v)   冒烟：任取一枚在用令牌打 /api/auth/me 得 200；打 /api/auth/revoke 得 revoked:true；
       重启后该令牌打 /mcp 得 401（撤销仍生效 ⇒ 摘要链路通）
```

⚠️ **做不到零停机**：`TokenRegistry` 只在 `__init__` 读盘一次（`af_auth.py:210`），
HTTP 与 MCP 是**两个进程**各持一份内存副本。不停机就会出现"一个进程读新格式、另一个读旧格式"。
**这一条要写进回执，不要声称零停机。**

### 3.4 判据腿

新建 `tests/unit/test_issued_tokens_digest_migration.py`：

| # | 场景 | 期望 |
|---|---|---|
| 1 | `_persist_issued` 后读文件 | **键是 64 位 hex**，值里无明文 |
| 2 | `authenticate(明文)` | 仍能命中（摘要链路通） |
| 3 | `revoke(明文)` 后读 `revoked.json` | 内容是摘要，不是明文 |
| 4 | 撤销后 `authenticate(同一枚)` | 返回 None |
| 5 | 登录签发的令牌 `principal_kind` | `"login"` |
| 6 | 配对签发的令牌 `principal_kind` | `"agent"` |
| 7 | 存量记录（无 `principal_kind`，subject=`owner`）装载 | 推断为 `login` |
| 8 | 存量记录（subject=agent 名）装载 | 推断为 `agent` |
| 9 | 迁移脚本 `--dry-run` | **不写文件**，只打印计数 |
| 10 | 迁移脚本遇损坏文件 | 退出非零，**原文件未被改** |
| 11 | ⛔ 安全腿：`issue_for_agent` 不接受调用方传 `principal_kind="developer"` 自助提权 | 抛错或忽略并记审计 |

腿 11 是本批最重要的一条——**签发点是唯一能定身份的地方**。

### 3.5 必红的既有测试（先跑出来贴回执，再按失败消息修）

- `tests/unit/test_v0_8_auth.py:70`：`assert json.loads(revoked_path.read_text()) == ["secret"]`
  ——它**钉住了 revoked.json 的明文表示**。改摘要后必红，按新口径改成断言摘要，
  **并在改动处写清为什么改**（PM 要能复核这个翻转）。
- 任何直接读 `issued_tokens.json` 键的测试/脚本，`grep -rn "issued_tokens" tests/ scripts/` 逐处确认。

### 3.6 验收

```bash
PYTHONPATH=".../src" python -m pytest tests/unit/test_issued_tokens_digest_migration.py -q
PYTHONPATH=".../src" python -m pytest tests/unit/test_v0_8_auth.py -q        # 必红的那条修完再跑
PYTHONPATH=".../src" python -m pytest tests/unit -q -k "auth or token or revoke or issue"
GATES_PYTHON=python bash gates.sh
python scripts/migrate_issued_tokens_digest.py --dry-run                    # 贴输出
```

### 3.7 ⛔ 不许碰

`af_api.py` 的端点档位（本批只改存储与字段，不改路由）、
`docs/信任边界清单.md`（档位没变 ⇒ 清单不用动；若 `_load_issued_file` 的行号漂了按 §1.3.1 处理）、
`AF_ACCESS_TOKENS` 的 env 装载路径（`:216 _load_env`，那是另一族，不落盘）。

---

## 4. 批 3：治理与审计面（看得见）

**目标**：把 D9/D8 结掉——治理页能分清三族、看到 TTL，`/api/auth/me` 不再对 agent 说自己是 admin。

### 4.1 后端

| 改动 | 落点 |
|---|---|
| `subjects()` 返回加 `principal_kind` + `expires_at`（**不回令牌明文**） | `af_auth.py:389-394` |
| `/api/auth/me` 的 `role` 按 `principal_kind` 回：`login` ⇒ `admin`、`developer` ⇒ `developer`、`agent` ⇒ `agent` | `af_api.py:1179-1185` |
| ⚠️ mimo/`ui/` 有用 `role === 'admin'` 判权限的地方，**同批改**（`grep -rn "role" ui/src ui-user-mimo/src`），否则前端会把 agent 显示成管理员或反过来挡所有者 | 两棵前端树 |

### 4.2 `ui/` 治理页改造（`ui/src/views/GovernanceView.vue`）

1. **删掉**"AF 访问密钥"明文输入框（`:113-130`）。理由：登录签发的 owner 令牌已覆盖全部治理功能
   （所有端点只要 read/write/live，admin 四点有 owner 通道）；留着它＝诱导把长期 A 类密钥贴进浏览器。
   ⚠️ `ui/` 发布线**已有登录页**（`ui/src/views/LoginView.vue`，该轮 +237 行）与 401 处理
   （`ui/src/api/client.ts:43-46`）⇒ 删框不会把人锁在外面。
2. **已注册令牌主体表加两列**：`principal_kind`（登录态 / Agent / 开发者）、`TTL`（到期时间或"不过期"）。
3. **每行加「撤销」按钮**（现在要去手填明文令牌串，`:159-169`）——调 `POST /api/auth/revoke`，
   body 传该行的**摘要或 subject**（二选一，PM 建议：owner 面撤销走
   `revoke_by_subject`，入参从"明文"改成"subject"，**这是新增能力不是变更**——
   现有的明文入参腿保留，`test_v0_8_auth.py:168` 那条不红）。
4. ⛔ 表格**不显示令牌明文/摘要**（摘要也不显示——它能用来比对，但不是给人看的东西）。

### 4.3 判据腿

| # | 场景 | 期望 |
|---|---|---|
| 1 | `GET /api/auth/subjects` | 每条含 `principal_kind` + `expires_at`，**无令牌字段** |
| 2 | agent 令牌打 `GET /api/auth/me` | `role == "agent"` |
| 3 | owner 令牌打同一端点 | `role == "admin"`（不回归） |
| 4 | 前端契约：治理页表格渲染三列 | `tests/unit/test_ui_api_paths_gate.py` 的 mimo/ui 调用点计数可能变（§1.9：台账与 pin 两套都要更） |
| 5 | 静态腿：治理页源码**不再有** `localStorage.getItem('af_token')` 的输入框写入路径 | `ui-contract` 或新腿 |

### 4.4 验收

```bash
PYTHONPATH=".../src" python -m pytest tests/unit -q -k "auth or subjects or ui_api_paths"
PYTHONPATH=".../src" python -m pytest tests/unit/test_ui_api_paths_gate.py -q
cd ui && npx vue-tsc --noEmit 2>&1 | tail -5     # ui/ 改了的类型检查
GATES_PYTHON=python bash gates.sh
```

---

## 5. 批 4：RBAC 收口 + 所有权隔离 + 令牌寿命

> ⚠️ **本批是五批里唯一会让 `docs/信任边界清单.md` 与门禁测试同时红的一批**，必须单独、小步、可定位。

### 5.1 新依赖 `requires_owner()` 与 `requires_governance()`

⛔ **不要用"给 scope 再加一档"的方式实现 developer**——`SCOPES`/`_scope_ok`（`af_api.py:256-271`）
是全局契约，加一档会波及全部 94 条路由与信任边界清单。

```python
def requires_owner():        # 判 subject ∈ _OWNER_SUBJECTS，与 admin 的 owner 通道同源
def requires_governance():   # owner 面 或 principal_kind == "developer"
```

- `requires_owner()` 用于：授权码铸造/删除（批 0 已改）、登录未注册分支、用户账号管理。
- `requires_governance()` 用于：**agent 令牌管理**（删/改他人 agent）、
  以及将来任何"管理面"端点。⛔ **不给 `developer` 令牌 `_OWNER_SUBJECTS` 身份**——
  它借 owner 主体名就能过 admin 门（`:506`），那就与主人不可区分了。

### 5.2 端点收口表

| 端点 | 现在（发布线） | 改成 | 理由 |
|---|---|---|---|
| `POST /api/auth/revoke` | `_write`（`:1101`） | **保持 `_write`** + 体内加"只能撤自己或 owner 撤任何人" | 自理通道必须留给 agent（否则 agent 无法自撤销）；`POST /api/auth/logout`（`:1170`）无 scope 门已能撤自己，**保留不动** |
| `DELETE /api/user/agents/{id}` | `_write` + `_assert_agent_subject_owner`（`:1421`、`:1429`） | **`requires_governance()`** + **保留主体绑定**（⛔ 删了它 `check_owner_subject` 判据 B 红） | 开发者 agent 要能删任一 agent 令牌 |
| `PATCH /api/user/agents/{id}` | `_write` + owner 绑定（`:1435`、`:1443`） | **`requires_governance()`** + 保留绑定 | 同上 |
| `POST /api/user/agent-token` | **不存在（新增）** | `requires_owner()` | 出资人需求 2：开发者在 `/governance` 签发开发者令牌 |
| `DELETE /api/automations/{name}` | `_admin`（`:1633`） | **保持**（已收口） | — |
| `POST /api/automations/{name}/enable\|disable\|archive\|unarchive` | `_write`，无归属（`:1617-1631`） | `_write` + **归属判定**（service 层已加，见批 0 ④） | 批 0 已做 service 判定，本批只确认端点不用改 |
| `POST /api/watch/start\|stop`、`/api/system/update` | `_admin`（`:1017/:1079/:1249`） | **保持** | 已收口；`developer` 令牌天然带 admin scope 可过 |

### 5.3 `rename_subject` 三条校验（堵 D7 提权路径）

`af_auth.py:491-501` 加：

1. `new_name ∉ _OWNER_SUBJECTS` ⇒ 拒（**放在服务层**，守住所有调用点，不只端点）
2. `new_name != "unknown"`（`_AGENT_LIST_RESERVED` 的成员，`af_api.py:284`）⇒ 拒
3. `new_name` 不得与现有其他 subject 重名 ⇒ 拒（防静默合并两族令牌）

前两条在 `rename_subject`，第三条放服务层或端点都可（PM 建议服务层，一次守住）。

### 5.4 令牌寿命（D11 / Q11）

出资人裁：**接受改**（原话"我建议改"）。

- 用户登录令牌：**空闲续期**（剩余 < 50% 才续）＋ **绝对上限**（owner 7 天）。
  ⚠️ 跨进程可见性必须写进设计：续期落盘后，**MCP 进程的内存副本不知道**
  （`__init__` 才读盘，`af_auth.py:210`）。做法：续期只改 HTTP 进程侧内存 + 落盘，
  并接受"MCP 面在重启前仍按旧 expires_at 判"——这个不一致窗口 ≤ 7 天，
  且**过期方向是"更早失效"而非"更晚失效"**，fail-closed，可接受。
  ⛔ 不要做成"每请求续期"——`_persist_issued` 是读全量→改写→整表写（`:539-573`），
  每请求一次全文件重写会叠在热路径上。
- agent / developer 令牌：**不过期**（`ttl_s=0`，现行不变）。

### 5.5 判据腿

新建 `tests/unit/test_auth_governance_rbac.py`：

| # | 场景 | 期望 |
|---|---|---|
| 1 | agent 令牌调 `DELETE /api/user/agents/{id}` | **403**（不能删别人） |
| 2 | developer 令牌调同一端点 | 200 |
| 3 | owner 令牌调同一端点 | 200（不回归） |
| 4 | agent 令牌调 `PATCH /api/user/agents/{id}` 把自己改名成 `owner` | **403**（保留名） |
| 5 | owner 把 agent 改名成 `owner` | **403**（服务层拦，不只端点） |
| 6 | owner 把 agent 改名成已存在的 subject | **403**（碰撞） |
| 7 | agent 令牌调 `POST /api/auth/revoke` 撤**自己** | 200 |
| 8 | agent 令牌调 `POST /api/auth/revoke` 撤**别人** | 403 |
| 9 | `POST /api/user/agent-token` 用 agent 令牌调 | **403** |
| 10 | `POST /api/user/agent-token` 用 developer 令牌调 | **403**（不可自我放大） |
| 11 | `POST /api/user/agent-token` 用 owner 令牌调 | 200 + 返回 `principal_kind="developer"` + scopes 含 admin |
| 12 | 登录令牌空闲续期：连续两次请求只触发一次落盘 | 看文件 mtime 只变一次 |
| 13 | 登录令牌超绝对上限（7 天） | 即使活跃也过期 |

### 5.6 ⚠️ 门禁三连（本批的生命线）

```bash
# 1. 先重生成清单自动段（改档位必然漂）
python scripts/check_trust_boundary.py --write
# 2. 再逐条绿
python scripts/check_trust_boundary.py
python scripts/check_owner_subject.py          # 判据 B：/api/user/agents/{…} 必须有主体绑定
python scripts/check_owner_subject.py --write  # 若报漂移
GATES_PYTHON=python bash gates.sh
```

⛔ **清单与代码必须同一笔 commit**（第一批 §1.3.1 教训：分两笔 ⇒ B 腿红）。
⛔ **`docs/信任边界清单.md:179` 那行台账"红 2 枚／待补"是过期读数**（代码 `af_api.py:1429/:1443`
早已带主体绑定）——本批重生成时顺手订正，并在回执里说明这一行为什么变了。

### 5.7 必红既有测试（先跑贴回执）

- `tests/unit/test_v0_8_auth.py:168-169`：用（非 owner 的）令牌打 `/api/auth/revoke` 断言 200
  ⇒ 本批加了"只能撤自己"，**若那条用的不是自己的令牌就会红**。逐行追溯它的令牌来源，
  按失败消息修，⛔ 不放宽断言。
- `tests/unit/test_trust_boundary_gate.py` / `test_owner_subject_gate.py` 的 pin 数会变
  （路由数 94 不变，但档位分布变）⇒ 按 §1.9 同批更 pin。

---

## 6. 批 5：`/pair` 新链路（配对简化）

**目标**：出资人需求 3——用户在 mimo 输入 agent 名 → 点生成 → **复制一段配对链接** →
agent 拿到链接自行完成配对。砍掉"agent 发起配对触发 WebUI 弹窗"这一环。

### 6.1 链接形态（出资人示例 `http://192.168.2.200:8787/pair?12748357`，本批按安全强化）

⛔ **不用 8 位纯数字做链接凭据**。理由（PM 量化过）：数字码熵 10^8，
匿名 redeem 限流 10/min ⇒ 单 IP 撞一次约 1.9 万年，**够用**；但链接是**要被复制转发的**，
且新增的 `GET /pair` 若只吃 1000/min 的 app 级限流，就成一个**免费 oracle**
（不消耗码、不触发 redeem 限流、并行铺 IP 无成本）。

**定案**：链接带 **128 位随机串**（`secrets.token_urlsafe(16)`），8 位数字码只留给人手输。

```
http://192.168.2.200:8787/pair?k=<43 位 urlsafe 随机串>
```

`PairCodeStore` 的 `PairCode`（`af_auth.py:741-753` 一带）加一个 `link_key` 字段
（与 `code` 一对一，同一 TTL、同一一次性语义）。⛔ 不要让 `link_key` 单独可续期。

### 6.2 `GET /pair?k=` 端点（新，匿名）

**内容协商**（PM 定案，理由：人会手滑点开链接，只回一坨 JSON 会被当成故障）：

- `Accept: application/json`（curl / agent）⇒ 回自描述 JSON：
  ```json
  {
    "ok": true,
    "agent_name_hint": "书房助手",
    "expires_at": "2026-10-26T15:09:00+08:00",
    "ttl_s": 600,
    "redeem": {
      "method": "POST",
      "url": "http://192.168.2.200:8787/api/mcp/pair/redeem",
      "body": {"code": "<8位数字码>", "agent_name": "<填 Agent 自己的名字>"}
    },
    "after_pairing": {
      "mcp_url": "http://192.168.2.200:8787/mcp",
      "auth_header": "Authorization: Bearer <redeem 返回的 token>",
      "warning": "token 只在 redeem 响应体里出现一次，请立即保存到你的客户端配置"
    }
  }
  ```
  **设计意图**：agent 不需要预先知道任何端点——链接里带了 redeem 地址、参数名、后续怎么用。
- `Accept: text/html`（人点开）⇒ 一屏静态说明（码、剩余时间倒计时、redeem 方法、一个复制按钮），
  **不需要前端构建、不需要登录态、不做任何确认/批准动作（只读展示）**。
  ⚠️ 响应必须带 `Referrer-Policy: no-referrer`（码会进浏览器历史）。

**限流（两条维度，缺一不可）**：

1. 复用 `bootstrap_redeem_limiter`（10/min + 超限锁 300s，`af_auth.py:694-698`）。
2. **加按码维度**的键（`key:{k}`）——现在 `_bootstrap_limit`（`af_api.py:374-383`）只有 `ip:{client}`
   一维。同一枚 link_key 被不同 IP 试，只有 IP 维度拦不住。

**失败计数落盘**：现在限流器是进程内存（`af_auth.py:1122-1123` 注释自认"不落盘、不跨重启"）
⇒ **重启即清零**。在 `.auth/` 下加一个计数文件（用现成 `_atomic_write_text`），
否则容器重建就把这层防线重置了。

### 6.3 mimo 侧改造（`ui-user-mimo/`）

1. `PairingModal.vue:70` 文案「生成 **6 位**配对码」⇒「生成配对链接」（后端早已 8 位，D10）。
2. 配对弹窗改成：**输入 agent 名 → 点「生成配对链接」→ 显示可复制的链接整串**
   （不再显示裸 8 位码 + 「在 Agent 端填入」那套——那是旧流程的文案）。
3. 调 **新的 owner 面端点**（见 6.4）生成 link_key。
4. ⛔ **不要删 SSE 腿**（`/api/mcp/pair-request` + `openPairStream`）——它服务于"agent 主动请求配对"
   的老路径，本批只是新增一条"用户先出码"的路。两条并存，链路不同但共用 `redeem_pair_code`。
5. ⚠️ 调用点变了 ⇒ 按 §1.9 同批更 `tests/unit/test_ui_api_paths_gate.py` 的 mimo 计数。

### 6.4 新端点：`POST /api/user/pair-link`（owner 面）

```python
@app.post("/api/user/pair-link", dependencies=[Depends(requires_owner())])
def api_pair_link_create(body: PairLinkBody) -> dict[str, Any]:
    """用户在 WebUI 生成配对链接（link_key + 8 位码，同一 TTL、同一一次性语义）。"""
```
- 复用 `request_pair_code`（`af_auth.py:855` 一带）**同一个函数**，⛔ 不发明第二套签发。
- 返回 `{link, code, expires_at, ttl_s}`；`link` 由 `UI_USER_PREFIX`/部署地址拼
  （⛔ 不要硬编码 `192.168.2.200`——`ui-user-mimo/src/api/env.ts` 已有三档取值的先例，照抄）。
- ⚠️ 仍受 `pair_store.is_accepting()` 约束（`af_api.py:1463`）：owner 暂停接受配对时生成应被拒，
  **与匿名 bootstrap 面同一个口径**。

### 6.5 判据腿

新建 `tests/unit/test_pair_link_flow.py`：

| # | 场景 | 期望 |
|---|---|---|
| 1 | owner 令牌调 `POST /api/user/pair-link` | 200 + 返回 link（含 43 位 link_key）+ code（8 位）+ ttl |
| 2 | agent 令牌调同一端点 | **403** |
| 3 | 匿名 `GET /pair?k=<对>` | 200 + JSON 含 redeem 指引 |
| 4 | 匿名 `GET /pair?k=<错>` | 404（⛔ 不要回"码不存在 vs 已过期"的区分——那是 oracle） |
| 5 | 同一 link_key 第 11 次请求（同 IP） | **429** |
| 6 | 同一 link_key 被 3 个不同 IP 各试 5 次 | **429**（按码维度生效，证明不只 IP 维度） |
| 7 | redeem 成功后，再 `GET /pair?k=<同一个>` | 404（一次性） |
| 8 | `Accept: text/html` 的 `GET /pair` | 200 + `text/html` + 带 `Referrer-Policy: no-referrer` |
| 9 | HTML 页不含任何"确认/批准"表单控件 | 静态腿（只读展示） |
| 10 | 重启后失败计数仍在（读计数文件） | 计数不归零 |
| 11 | link_key 长度 ≥ 128 bit 熵 | 静态腿：`len(link_key) >= 32`（urlsafe base64） |
| 12 | 端到端：用户生成链接 → agent 用链接 redeem → 拿到令牌 → 打 `/api/auth/me` | 全通，`principal_kind == "agent"`，scopes **不含 admin** |

腿 12 是本批的验收腿，必须真跑通。

### 6.6 ⚠️ 信任边界清单登记（不登记即红）

`GET /pair` 是**新增匿名端点** ⇒ 必须进 `docs/信任边界清单.md` §三 非鉴权面登记表，
写清 `处理器 / 依据 / 理由`，且**依据必须指向仓内真实文件**（新建的
`tests/unit/test_pair_link_flow.py` 就是现成依据）。自动段同批 `--write`。

⛔ **不要把它登记成"有守卫"**——`_bootstrap_limit` 是限速不是鉴权，
按清单既有口径（`POST /api/mcp/pair/redeem` 那行）读成 anon。

---

## 7. 交付格式（在 `docs/db_task_receipt.md` 追加，每批一段）

```markdown
## 批 N <标题>
- 判据腿：<文件>（N 条；修前红→修后绿；**改了哪几条既有断言、为什么**）
- 改了哪些文件：<逐个列，标注是否在禁区清单里>
- 必红既有测试：<先跑的原始输出> → <修法> → <修后输出>
- 门禁三连：check_trust_boundary / check_owner_subject / gates.sh 的完整输出 + 退出码
- 台账同批：--write 的 diff（若报了漂移）
- 新增 except-pass：0（若 >0 说明为什么）
- 顺序污染自检 + 风险自检的输出
- 我没做的事：<明确列出>
```

**三条硬要求**（从前三批的教训来）：

1. **改了既有测试断言方向的，必须单独说明改前改后各是什么**，PM 要能复核翻转是否合理。
2. **台账 regen 与测试 pin 是两套**（§1.9）——改了 `src/autoforge/` 下任何文件，
   除了 `--write` 台账，**还要跑对应的 `test_<那个门>_gate.py`**。
3. **每批开头先跑一次 `git merge-base --is-ancestor HEAD origin/master`**，退出码 0 才开工
   （§0.6 新纪律），把输出贴进回执。

---

## 8. 时间盒与顺序

| 顺序 | 批 | 预估 | 依赖 | 额度不够怎么办 |
|---|---|---|---|---|
| 1 | 批 0（堵四把刀） | 中 | 无 | **必保**。今天是可被利用的 |
| 2 | 批 1（D5 owner） | 小 | DB-07 若并行须同笔 | **必保**。是批 2 迁移的校验前提 |
| 3 | 批 2（摘要迁移 + kind） | 中大 | 批 1 | 必保；**迁移要停机，提前跟 PM 约窗口** |
| 4 | 批 3（治理面看得见） | 中 | 批 2 | 可挪后（纯展示） |
| 5 | 批 4（RBAC 收口） | 中大 | 批 2 | 可挪后（会红门禁，需单独窗口） |
| 6 | 批 5（/pair 链路） | 中大 | 批 2 | 可挪后 |

**卡住超 30 分钟**：写 `docs/db_roadmap_blockers.md`（文件路径 + 卡在哪一行 + 试过什么），换下一个。

---

## 9. PM 自留（不派）

| 项 | 原因 |
|---|---|
| 批 2 的**停机窗口协调** | 要停 NAS 上的服务，PM 与出资人约时间 |
| 批 4 改档位后的**门禁清单复核** | PM 逐行核 `--write` 的 diff 是否只吸收预期漂移 |
| 生产验证（`/api/health` 版本、`/api/auth/has-admin`、冒烟） | PM 做（只读 GET，不写生产） |
| `E:\NAS\autoflow` 的 12h 滑动续期**不作为设计对照** | 那是 session cookie 模型，与 AF 的 bearer 不同族；本规格的续期方案按 §5.4 自证 |
