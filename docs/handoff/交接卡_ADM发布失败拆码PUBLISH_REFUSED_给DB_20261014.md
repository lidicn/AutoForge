# 交接卡：ADM 发布失败拆码 `ADM_ERR_PUBLISH_REFUSED` —— 给 DB 的处置方向变化（预告＋现读）

| 项 | 值 |
|---|---|
| 里程碑 | 裁定 `decisions/20261010-AF与DB与DPP十件-裁定.md` §二 **Q3 甲**（AF 拆码发版三问里的"reasons[] 同步 DB"那格） |
| 完成日期 | 2026-10-14（本卡＝交件动作；变更本身**尚未上线**，见 §2） |
| 状态 | 🔨 预告卡：库侧已进树，AF 消费半边卡在 homesdk 0.3.3 未发版 ⇒ **本卡现在不要求 DB 改判别**，要求的是把"处置方向相反"这一格提前登记 |
| 影响面 | ADM 出向失败读数：`inbox_publish` 返回体 `code` 字段 ＋ retained status 的 degraded 原因（契约表 §7.2 那一族） |
| 座位 | AF（AutoForge，`E:\NAS\AutoForge`）→ DB（doubao-butler） |

---

## 1. 一句话

`PUBLISH_REFUSED` 的处置方向和 `BROKER_UNREACHABLE` **相反**：前者是"broker 活着、连接着、但拒收了这一条"，
重启它一点用也没有；后者才是"去重启／去重连"。今天两件事在 AF 侧发的是**同一枚码**，
拆码之后会分成两枚——DB 若按旧方向处置新码，动作不报错、只是**白做**。

## 2. 现读（2026-10-14 本机，⛔ 推导）

三面对撞，逐面都带命令原文，DB 可冷跑复现：

- **库侧源码面（已进树，第七枚）**：
  `grep -n 'ADM_ERR_[A-Z_]*: Final' /e/NAS/homesdk/src/homesdk/adm/errors.py` ⇒ 7 枚，`:17 ADM_ERR_PUBLISH_REFUSED`；
  `ADM_ERRORS` frozenset 在 `:25`（枚数只按 `len(ADM_ERRORS)` 现读，与裁定 §二 Q1 钉的口径一致）。
  `grep -n LINKAGE_PAUSED` 同文件 ⇒ **0 命中**（第八枚，属 0.3.3 待进树，本卡不涉及）。
- **本机已装 wheel 面（AF 此刻能 import 的东西）**：`python -c "import homesdk;print(homesdk.__version__)"` ⇒ **0.3.2**；
  对同一份已装 `homesdk/adm/errors.py` 计数 `ADM_ERR_PUBLISH_REFUSED` ⇒ **0 命中**。
  ⇒ **AF 今天⛔ 能发这枚新码**（import 不到），所以本卡是预告不是变更通告。
- **AF 消费面（今天实际发出去的码）**：`src/autoforge/af_mqtt_bridge.py` 三枚站点写的是同一枚旧码——
  `:512` `"code": ADM_ERR_BROKER_UNREACHABLE`（代理未记账分支）、`:627` `self.mark_degraded(ADM_ERR_BROKER_UNREACHABLE)`、
  `:633` `"code": ADM_ERR_BROKER_UNREACHABLE`（import 在 `:36`）。模块内 `PublishRefused`（`:363`）的 docstring 自述了为什么塌成一枚：
  paho 没连上时不抛异常只回 `rc`，而 rc 的语义 AF 不猜 ⇒ 全给 `BROKER_UNREACHABLE`，原值进 message。

## 3. 0.3.3 窗后会长成什么样（届时另发正式卡）

- 传输侧失败按根因分叉：**未连接／broker 不可达** → `ADM_ERR_BROKER_UNREACHABLE`（方向＝重连／重启）；
  **已连接却被拒**（`rc≠0`：ACL／权限／topic 不在写名单）→ `ADM_ERR_PUBLISH_REFUSED`（方向＝查凭据写权限与 topic 白名单，**⛔ 重启 broker**）。
- 落码点在三处（`af_mqtt_bridge.py:512`／`:627`／`:633`，加 `:36` 的 import；裁定 §二 Q2 乙写的是"一处取码 `:627/:633`"，
  本枚复测按现读多点名一枚 `:512`——它是"代理不联网却走到真发"的兜底分支，同族同码），返回体形状、`published`／`topic`／`error`／`trace_id` 四键**都不动**——
  变的只有 `code` 的取值集合，所以 DB 侧不需要改解析，只需要改**处置表**。

## 4. DB 侧现在该做与不该做

| | 内容 |
|---|---|
| ⛔ 现在别做 | 把判别改成"只认 `ADM_ERR_PUBLISH_REFUSED`"。AF 今天还在发旧码，改早了是**静默失效**（读数读成"没失败"），正是契约 §7.3 要消灭的那一族 |
| ✅ 现在可做 | 按**码集合**判失败（两枚都算"发布失败"，记账不动），只在**给 owner 的提示语**上预留分叉：`PUBLISH_REFUSED` → "检查该凭据对这条 topic 的写权限／topic 是否在白名单"，⛔ "重启 broker" |
| ✅ 收窗信号 | 见 §5 的三条命令，任一读到新码在场即视为变更已上线，本卡转为正式变更卡 |

## 5. 触发条件／复核人／复核动作（三件套，可机械核）

- **触发条件**：homesdk 0.3.3 wheel 落地（发版归 DCD/NAS，裁定 §二 Q2 乙）**且** AF 消费半边提交（任务 #93／#80 那一格）。
- **复核人**：AF 每批开工时跑一次；变更上线当天由 AF 另发正式卡，DB 不靠猜。
- **复核动作**（命令原文，逐条跑）：

```bash
# ① wheel 面是否含新码（现读 0 命中 ⇒ 未上线）
python -c "import homesdk,os,glob;t=open(glob.glob(os.path.join(os.path.dirname(homesdk.__file__),'adm','errors.py'))[0],encoding='utf-8').read();print(homesdk.__version__, t.count('ADM_ERR_PUBLISH_REFUSED'))"
# ② AF 消费点是否已换码（现读两枚仍写 BROKER_UNREACHABLE）
grep -n "mark_degraded(ADM_ERR\|\"code\": ADM_ERR_" src/autoforge/af_mqtt_bridge.py
# ③ 库侧名册枚数（现读 7）
grep -c "ADM_ERR_[A-Z_]*: Final" /e/NAS/homesdk/src/homesdk/adm/errors.py
```

## 6. 依据与出处

- 裁定：`decisions/20261010-AF与DB与DPP十件-裁定.md` §二 Q3 甲（"契约 §7.2 那一行是登记不是通告，DB 的处置逻辑需要一张明确交接卡"）；同族先例 `20261007 §五 戊A`（改读数面同步 DB，本仓已有一张同类卡 `docs/handoff/交接卡_MCP异常路径JSON信封_读数变化_20261008.md`）。
- 库侧真源：`/e/NAS/homesdk/src/homesdk/adm/errors.py`（只读，AF ⛔ 动一字节）。
- 回执：`receipts/20261014-AF-20261010AF与DB与DPP十件-回执.md` §二（本卡的交件动作在回执里登记为 §二 Q3 的落点）。
