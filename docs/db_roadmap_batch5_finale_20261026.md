# 豆包（db）执行路线图 · 第五批：身份层收尾（2026-10-26）

> **PM：Claude（本会话）· 开发：豆包（db）**
> 前置：第四批（身份层一次治理）批 0-5 已交付，PM 复核后由 **PM 完成返工 5.1**（见下 §0），
> 遗留三块活派给 db。本批是第四批的**收尾**，不是新治理——治理模型已定、门已绿、代码已合。
>
> ⚠️ 开工先读 [`db_START_HERE_20261026.md`](db_START_HERE_20261026.md)（环境+纪律），
> 再读本文件。**基线核验（§0.6 新纪律）第一步都不能省。**

---

## 0. PM 已完成的返工 5.1（db 不必重做，但必须知道改了什么）

第四批复核发现「批 5 之后门禁 5 处红 + 全量 13 failed」。PM 判断这批返工**机械、验证密集、
跨 9 个文件互相咬合**，重派给 db 反而更容易错位，故 PM 直接做完，要点：

| # | 返工项 | 落点 | 性质 |
|---|---|---|---|
| ① | 批 4 的 `_caller_is_governance` 过不了 `check_owner_subject` 判据 B（门只认"体内直接比较"或"helper 名带 subject＋实参含路径主体与主体引用"） | `af_api.py`：治理面语义并入 `_assert_agent_subject_owner(agent_id, caller_subject, caller_kind)`，两个 agents 端点恢复调用它；`_caller_is_governance` 删除 | **语义不变**（owner∪developer 过、普通 agent 403），只改形状让门认得 |
| ② | P0-9 恒定时间比较被批 2 的摘要索引顺手拆掉（env 令牌 O(1) 明文查找、revoked 集合查找） | `af_auth.py authenticate`：revoked 恢复逐条 `compare_digest`；env `_tokens` 恢复逐钥 `compare_digest`；issued 保留摘要索引（160-bit 随机值，安全） | 恢复登记过的安全修复 |
| ③ | 批 5 新吞异常 `af_api.py:1664` | 改 `except OSError` + 具名码留痕（留痕失败不阻断 404 回执） | §2.3"只减不增" |
| ④ | except_pass 基线行号（SSE 心跳同一枚站点 1388→1443 漂移） | `scripts/check_except_pass.py` 基线重冻结＋注释链 | 净数 16=16 |
| ⑤ | 四份台账 --write 重生成 + CRLF round-trip | 信任边界/进程模型/可观测性/发布线 | §1.3.1 |
| ⑥ | `GET /pair` 进信任边界 §三 登记；§三 手工段 24 枚具名码行号机械对齐（不动点迭代配对，宁缺勿错配）；§五 `af_api-ok-false-sites` 9→10（D2 登录 fail-closed 新增一枚） | `docs/信任边界清单.md`、`docs/可观测性清单.md` | §6.6 的账 |
| ⑦ | `test_corrupt_state_write_bar.py` 两腿按摘要口径翻转（批 2 漏翻转，PM 的 grep 指引漏扫——`issued_path` 不含字面 `issued_tokens`，规格 §3.5 检索已补） | `tests/unit/test_corrupt_state_write_bar.py` | 断言翻转 |
| ⑧ | 门 pin 对齐：rows 95→97、anon 10→11、write 37→38、ungated/register 14→15、ok_true 32→35、depends 98→102 | `tests/unit/test_trust_boundary_gate.py`、`test_observability_gate.py` | §1.9 |

**db 开工第一件事**：`git pull` 后跑一遍
```bash
PYTHONPATH="E:/NAS/AutoForge/.claude/worktrees/busy-meninsky-653c24/src" bash gates.sh
```
**必须 exit 0**。不绿 ⇒ 停下来写 blockers（说明 PM 的返工在你这儿红了——可能是我漏了什么，这比硬开工值钱）。

---

## 1. DB-08 · 登录「兼容分支」的残身清理（批 0 D2 的前端欠账，PM 定性修正过一次）

### 1.1 现状（PM 现读——⚠️ 本节初稿定性错了，已更正）

批 0 把 `POST /api/auth/login` 的未注册兼容分支改成 fail-closed（`af_api.py:1151-1159`）：
未注册管理员时返回 **401 + `{"ok": false, "need_register": true}`**。

PM 初稿说"前端零跟进、首启断链"——**读细了，不对**。两棵树的首启其实都有 `has-admin` 探测：

- mimo `LoginView.vue:30`：`fetch('/api/auth/has-admin')` → 无管理员 → 显示注册页（`:63` 调 register，成功自动登录）
- `ui/` `LoginView.vue:21`：`api.hasAdmin()` → 同款（`:83` 「首次使用 · 设置账号」）

⇒ **正常首启路径没断**。`need_register` 那枚 body 前端确实没人读，但它不是断链，是第二道冗余。

**真正的缺陷是兼容分支的残身三件**：

| # | 残身 | 位置 | 问题 |
|---|---|---|---|
| a | `af_api.py:1143-1147` docstring 还写着「向后兼容：如果还没有管理员用户（未注册），**保持旧的任意非空凭据可登录行为**，避免升级后老用户被锁在外面」 | 后端 | **批 0 改了行为没改 docstring**——文档描述的行为（发全权限令牌）已经不存在，读它的人会以为刀还在 |
| b | 两棵信任边界清单 `POST /api/auth/login` 登记行的「理由」段还写着「没有管理员时那枚兼容分支**给的是全权限令牌**，同上一条一起**等裁**」 | 前端账 | 行为已改（fail-closed），理由过期；且"等裁"状态变了——D2 已裁并已修，该改成"已收口"口径 |
| c | `need_register` body 前端不读 | 两棵树 | 低优先——`has-admin` 探测在先，它只在竞态窗（探测时没有、登录时已被别人注册完）露出；前端把这条 401 当通用登录失败显示，不致命，但**误导**（用户看到"登录失败"其实是"账号已被占用"） |

### 1.2 改法

1. **a**：`af_api.py:1143-1147` docstring 按现行为重写（未注册 ⇒ 401 fail-closed，指向注册端点）。
2. **b**：两棵树 `docs/信任边界清单.md` §三 的 login 行「理由」段同步改口径——**这是 §1.3.1 的"手工段同批"**，
   与 af_api 改动同一笔；C 判据会核依据文件真实存在。
3. **c**（可选项，额度不够就停）：两棵树 login 失败时若响应带 `need_register: true`，
   文案改「账号尚未注册/刚被注册，请刷新后注册或登录」，不做状态机重构。
4. **判据腿**：`grep -rn "need_register" src/autoforge/af_api.py` 的行为腿后端已有
   （`test_auth_governance_batch0.py` 腿 3 钉 401+body）——本项**主要是账与文案**，
   新增腿一条：docstring 反向断言（旧句「任意非空凭据可登录」字样在 af_api 里必须 0 命中，
   仿既有"明文样例凭据出 docstring"那族扫描腿）。

---

## 2. DB-09 · 治理页「签发开发者令牌」卡（需求 2 的前端半边）

### 2.1 现状（PM 现读）

后端 `POST /api/user/agent-token`（批 4 交付，`af_api.py:1567` 起）：owner 专属、
`principal_kind="developer"`、ttl=0、全 scope、**响应里带明文令牌仅此一次**——**功能完整**。

`grep -rn "agent-token\|issueAgentToken" ui/src` **0 命中**——出资人的需求 2
「开发者在 `http://192.168.2.200:8787/governance` 点一下生成、明文一次性出现」**后端就绪、前端没接**。
治理页现在只有"列主体/撤销"两件事（批 3 做的），没有签发入口。

### 2.2 改法（`ui/src/views/GovernanceView.vue`）

1. 新增「签发开发者 Agent 令牌」卡：输入 agent 名（必填）→ 按钮 → 调
   `POST /api/user/agent-token` `{agent_name}`。
2. **成功后一次性展示**：明文令牌显示在只读框里 + 「复制」按钮 + 醒目警示
   「**仅此一次可见，关闭后无法再次查看**」（出资人原话"一次性出现"）。
   展示后令牌**不进任何 store、不写 localStorage**——复制走剪贴板，刷新即失。
3. 签发成功后 `load()` 刷新主体表（新 developer 行会带 `principal_kind` 列，批 3 已接）。
4. 错误路径：403（非 owner 面）显示后端 detail；空名前端就拦。
5. `api/client.ts` 加 `issueAgentToken(name)` 一个方法（`ui/` 的 api 面按既有 fetch 封装形状）。

### 2.3 判据腿

| # | 场景 | 期望 |
|---|---|---|
| 1 | 源码形状腿：GovernanceView 含「agent-token」调用 + 一次性警示文案 + 剪贴板复制 | 字符串在位且**不是死代码**（参照 mimo ui-contract 的变异写法：把行注释掉必须红） |
| 2 | client.ts 有 `issueAgentToken` 且路径字面与后端 `/api/user/agent-token` 一致 | `tests/unit/test_ui_api_paths_gate.py` 自动对撞（ui 调用点计数会 +1 ⇒ **同批改钉值**，§1.9） |
| 3 | 令牌不进 localStorage：源码里不得出现 `setItem(...token...)` 形状 | 反向断言 |

### 2.4 验收

```bash
cd ui && npx vue-tsc --noEmit && npm run build   # BUILD_EXIT=0
PYTHONPATH=".../src" python -m pytest tests/unit/test_ui_api_paths_gate.py -q
GATES_PYTHON=python bash gates.sh
```

---

## 3. DB-10 · `/pair` 的 CSP 纵深（PM 复核发现，防点击劫持）

### 3.1 缺陷（PM 现读，成立）

批 5 `GET /pair` 的 `Accept: text/html` 分支返回**匿名可达的 HTML 页**，页内打印
**8 位配对码**（`af_api.py` `api_pair_link_open` 的 html 段）。响应头只有
`Referrer-Policy: no-referrer`，**没有 `X-Frame-Options` / CSP `frame-ancestors`**。

攻击形状：恶意页面把 `/pair?k=<链接>` iframe 进去——链接在谁手里谁才打得开，所以威胁面主要是
**码的泄露**（第三方站无法直接读渲染文本，但浏览器插件/历史嗅探等旁路会拿到"页面截图级"内容）。
8 位码 + 链接键同屏展示，等于把两个凭据一起放进了可被框架的页面。

### 3.2 改法（最小纵深）

`api_pair_link_open` 的 HTML 分支响应加两枚头（JSON 分支一并加，成本为零）：

```python
headers = {
    "Referrer-Policy": "no-referrer",                    # 既有
    "X-Frame-Options": "DENY",                           # 新增
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
}
```

⚠️ CSP 必须放行页内的 `style='…'` 内联样式（页是内联样式写的），所以 `style-src 'unsafe-inline'`
是**刻意保留的最小口子**，⛔ 不要顺手删也不要扩到 `default-src`。

### 3.3 判据腿（加进 `tests/unit/test_pair_link_flow.py`，现 10 条 ⇒ 12 条）

| # | 场景 | 期望 |
|---|---|---|
| 11 | `Accept: text/html` 打 `GET /pair?k=<有效>` | 响应头含 `X-Frame-Options: DENY` 与 CSP（含 `frame-ancestors 'none'`） |
| 12 | 默认 JSON 分支 | 同样两枚头在位 |

### 3.4 验收

```bash
PYTHONPATH=".../src" python -m pytest tests/unit/test_pair_link_flow.py -q
GATES_PYTHON=python bash gates.sh
```

---

## 4. 交付格式

`docs/db_task_receipt.md` 追加，每项按第四批 §7 的同一形状（判据腿/文件/必红翻转/门禁输出/
CRLF 验证/顺序污染/我没做的事）。**三条硬要求原样有效**：断言翻转逐条说明、
台账与 pin 两套都更、每批开头贴 `merge-base` 输出。

## 5. 顺序与时间盒

| 顺序 | 任务 | 预估 | 依赖 |
|---|---|---|---|
| 先 | **DB-10**（最小，一个函数加两枚头） | 小 | 无 |
| 中 | **DB-09**（纯 ui/ 前端） | 中 | 无 |
| 后 | **DB-08**（两棵树登录+mock 同步） | 中 | 无 |

三项互不重叠（`af_api.py` 一处、`ui/` 一处、`ui-user-mimo/` 一处），可并行也可串行。
**额度不够保 DB-08**——它补的是出资人主面（mimo）的首启断链；DB-09 需求 2 前端其次；DB-10 是纵深。

**卡住超 30 分钟**：写 `docs/db_roadmap_blockers.md`，换下一个。

## 6. ⛔ 不许碰

- 第四批已交付的治理语义（`principal_kind` 判定、`_assert_agent_subject_owner` 形状、
  owner 通道、`SCOPES` 常量）——**本批零后端语义改动**（DB-10 只加响应头）。
- `af_api.py` 除 `api_pair_link_open` 的 headers 一处外不动。
- 门禁脚本、基线文件、`scripts/migrate_issued_tokens_digest.py`。
- 生产 `.auth/` 文件、NAS 配置、部署（发版与停机迁移由 PM 约窗口）。
