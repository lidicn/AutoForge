# ADM 现网窗口工单（AF 侧）

> 出品：AutoForge 席位 ｜ 起草：2026-10-13 ｜ 路线图版本取法：`grep -n "路线图版本" E:\NAS\关键决策部\roadmaps\AF-联动路线图.md | head -1`（⛔ 本文不抄 `rev` 号，抄了就会被下一次修订作废）
> 执行人：**出资人本人**，或出资人**逐次点名**的执行人。⛔ AF 席位不执行任何 docker 命令、⛔ 不开合并窗、⛔ 不碰 NAS 容器。
> 用途：把路线图 AF 名下**只剩"没人跑"那一半**的格子，排成一条可一次跑完的窗次序列。每格都写「命令原文／判据形状／落盘位置／缺哪一半」，⛔ 本文不预先写任何 `PASS`。

---

## 〇、先说承重前提（本席现读，会被下一次修订作废的两条）

1. **在役镜像的源码面落后于本仓 HEAD**：本席此前把容器内 `/app/src` 那层精确钉到过一枚旧提交（见执行记录 §二之一百四十一／§二之八十一 那两格的取证），所以 **本文窗 B 里那三枚新键在镜像里读不到**。⇒ **窗次顺序不是可选的**：先做**窗 A（部署）**，窗 B 才有东西可读；先跑窗 B 会得到 `ABSENT`，那**不是缺陷读数**。
2. **链主那把尺的现网半边吃的是名册字段，⛔ 不读 AF 的健康面**（`E:\NAS\homesdk\scripts\verify_adm_linkage.py:251-253` 面：盘上腿查源码那行 `INSIGHTS_TOPIC`，现网腿查 `探针证据\现网点名表.md` 那块 `LIVE:` 的 `verdict`／`ts`／`source`／`cmd`）。⇒ **只有窗 C 那块 `LIVE:` 填了，尺子那几格才会动**；光部署不填名册，`两列全绿 0/5` 会原样复现。

---

## 一、统一前置（每次开窗先跑，收窗再跑一遍）

```
docker inspect autoforge-api --format '{{.State.StartedAt}}'
```

★ 状态证明按裁定 20261013 §二 的**更正版**（原形"被读文件 `mtime` 不变"已作废——持续重冻的文件 `mtime` 必然变，会把真·只读探针误判成越线）：

```
# 同一条只读命令首尾各跑一遍，对目标键的取值算 sha8，要求两值全等：
docker exec autoforge-api python -c "import json,hashlib,urllib.request as u; d=json.load(u.urlopen('http://127.0.0.1:8000/api/health'))['linkage']['inbound']; print(hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest()[:8])"
# 文件 mtime 只作记录，⛔ 作作废条件
docker exec autoforge-api stat -c %Y /app/src/autoforge/af_mqtt_bridge.py
```

两格读数（`StartedAt` 同一 ＋ 首尾 sha8 全等）齐 ⇒ 这批动作按**只读型**论；任一格不齐 ⇒ 当场停手并按**写向型**上报，⛔ 继续跑。

---

## 二、窗 A：部署窗（让本仓 HEAD 进在役镜像）

| 项 | 内容 |
|---|---|
| 目的 | 把 `af_mqtt_bridge.py` 的 `inbound` 三键（`insights_subscribed`／`insights_in`／`insights_rejected`）送进在役层，使窗 B 可读 |
| 前置 | ★ **本席⛔ 出境**：本地 `master` 领先远端若干枚（取法 `git fetch origin master && git rev-list --count origin/master..HEAD`）——**push 需要 owner 逐次点头**，且只走 `git push origin master:main` 那一条线（推后 `git ls-remote origin main` 自证等于本地 HEAD） |
| 执行 | 由出资人按既有部署通道操作（NAS 侧口径见本仓规范：无 node、`-f docker-compose.api.yml`、换 inode 要 `--force-recreate`）；⛔ 本文不代拟 docker 命令 |
| 验收（只读） | `docker exec autoforge-api grep -c "insights_subscribed" /app/src/autoforge/af_mqtt_bridge.py` ⇒ **≥1**；并核本仓镜像层与 HEAD 那枚提交的对应关系（按 md5 对撞，⛔ 按"版本相同"推断） |
| 缺哪一半 | 这一窗⛔ 不等任何裁定——它只等 owner 的点头与窗口 |

---

## 三、窗 B：现网只读点名（四格 ＋ G-AF-08 只读那两条）

命令原文与判据形制的**唯一真源**是投递件 `E:\NAS\关键决策部\inbox\20261013-AF-路线图rev9四格盘上复现与现网点名命令包-登记【已受理】.md` 的 **§五（表格）＋ §十（落码后新增的那条与三档判据）**；本文只补两件事：

1. **跑之前先做窗 A**，否则 `INSIGHTS_SUBSCRIBED` 那行会是 `ABSENT`。⚠️ `ABSENT` 的如实写法＝「镜像层未含本枚」，⛔ 写「AF 代码缺失」、⛔ 写「取数面未建」（那一档已由 §二之一百四十四 收口）；
2. **每格读数的落点**是链主名册那块 `LIVE:` 的四字段（形制约束照抄名册 §二：块首 `LIVE: <腿名>` ＋ `verdict`／`ts`／`source`／`cmd`；`source` 必须指到 `探针证据\` 下一枚真读数件，且那件要过证据尺 `READ:` ＋ `VERDICT: PASS` ＋ ≥80 B）。⛔ 不把盘上读数填进现网列、⛔ 不给等窗口的腿预先写 `PASS`——这两条禁令是名册自己写的。

| 腿名 | 这一格今天缺的是哪一半 | 谁的手 |
|---|---|---|
| `af_subscribes_insights` | 取数面**已建**（三键已入库），缺的是一次容器内只读读数＋那块 `LIVE:` | 执行人（窗 B） |
| `af_pending_queue` | 命令两条一直在，缺同数对撞的现读 | 执行人（窗 B） |
| `af_ma_query_adapter` | 镜像层在役的两条只读命中；运行期 round-trip 另属窗 D 之外的事 | 执行人（窗 B） |
| G-AF-08 判据①②（`/api/system/update/check`） | 非 404 ＋ `has_update`／`local_commit`／`latest_commit` 三键齐 ⇒ 那半条才成立 | 执行人（窗 B） |

---

## 四、窗 C：受控写向（⛔ 只读例外罩不住，三件必须单独点头）

| 件 | 为什么要写 | 判据 |
|---|---|---|
| `af_emits_channel_error` | `channel_error` 只在应答投递路的返回体里，产读数就要 POST | 返回体含 `"channel_error": true` 且 `inbox_key_missing` 才逐字 `PASS`；只读版（报 `AUTOFORGE_INBOX_KEY` 的字节数、⛔ 不落值）只能证通道前提，⛔ 不写成 `PASS` |
| G-AF-08 判据③（脏树拒执负控） | 要往部署树放一个未收编改动 | ★ 本席盘上现读**这一格盘上就不成立**（守卫 0 命中、脚本走 tar 备份后无条件 `reset --hard`）⇒ 落点在在途文件 `scripts/host_update.sh`，⛔ 本席不改在途文件（挂 #145，等登录线/在途落定） |
| `e2e_insight_to_draft`（G-AF-04） | 四跳要 MA 真发一条 `ma/insights(<insight_id>)` ＋ AF 运行期可达 | 四跳逐跳带 `ts`＋末行逐字 `VERDICT: PASS`＋件 ≥80 B；任一跳缺＝该件不成立。证据件 `af-ma-insight-to-draft.md` 现读仍不在位 |

---

## 五、窗 D：凭据面（AF 名下 3 枚真值／6 处落点，⛔ 本席不碰值）

裁定 20261013 §三 的两条口径直接管这一窗：**① 同值簇整簇同批轮换（只换一枚＝假轮换）；② 凡「类＝真值」且长度 ≥20 一律入册**（已提交 ⇒ 已上 GitHub ⇒ 一律视为已泄露，⛔ 逐枚辩"活/死"）。

| 步 | 谁的手 | 内容 |
|---|---|---|
| 1 | **出资人** | 按名册下发轮换（值只走一次性交接，⛔ 不经本文、⛔ 不经任何投递件／回执／仓内文件） |
| 2 | **AF 席位（本席）** | 轮换落地后，把 `docs/handoff/` 那 6 处的**明文改成键名引用**（⛔ 本文抄落点不抄值：两处文件、行锚按投递件与链主 rev 的 sha8＋路径为准）。⚠️ 如实声明：**就地打码≠清零**——版本史／转写／日志里仍留有前快照，要"清零"就得动历史（不可逆），那一步要**单独点头＋评审口径**，⛔ 本席不自决 |
| 3 | **AF＋MA 两席同批** | 其中两枚同时躺在 MA 的快照树脚本面 ⇒ 单仓 scrub 清不掉活口，必须同批（链主 rev 10 已登这一条） |

---

## 六、⛔ 不属于本工单的事（免得窗口里临时加戏）

- **⛔ 装包、⛔ 重启、⛔ 抬任何账本数字**（`.gates-tally.txt`／`.gates-baseline.txt`／`.atomic-write-baseline.txt`）——门读数只在仓内变，不在窗口里变；
- **⛔ 代填链主那棵树**（`E:\NAS\ADM链主\探针证据\`）与**⛔ 改路线图一行**（`:6` 写明 AF 只读）；
- 新裁定 §四 的「**沙箱型**」（唯一名前缀、只写容器 `/tmp`、跑完按前缀自清、交两行状态读数）**不解锁本工单任何一格**——本文每一格读的都是**在役容器的对外脸**，仍属只读型；沙箱型解锁的是"跑一段码只写 `/tmp`"那一类（对应 run37 那种门档），⛔ 借它给 AF 侧扩权。

---

## 七、本席这一侧已经交付的（⛔ 本文不是"整条线在等窗口"）

`ma` 适配器四 action＋静默空值判据、`insight_proposals` 待批队列与 `/api/insights/*`、`ma/presence`＋`ma/device-health` 订阅与落盘队列、入向三枚 `context.*` 平键、`/api/health` 的 `write_gate` 与 `linkage` 面、洞察入向三键（#148）、发布线一致门（#146）、四格命令包（投递件 §五／§十）——**均在 HEAD**。剩下的 AF 名下格子，逐格都能指到本文某一窗或某个他席在途文件。
