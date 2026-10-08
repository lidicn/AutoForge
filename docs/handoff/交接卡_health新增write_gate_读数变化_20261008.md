# 交接卡：AF `/api/health` 新增 `write_gate` —— 给 DB / MA 的读数变化说明

| 项 | 值 |
|---|---|
| 里程碑 | 裁定 20261008 `20261008-AF两件与MA四回执-裁定.md` §一 **裁 B**（新键承载真值）+ **Q2 是** |
| 完成日期 | 2026-10-08 |
| 状态 | ✅ AF 侧已交付（仓内判据 16 腿绿 + 门禁绿）；契约表登记那一半按裁定归 DCD/DB 侧同步 |
| 影响面 | **纯增键**：`GET /api/health` 多一个 `write_gate`；旧键 `readonly` 的取值与语义一字未动 |

---

## 1. 变了什么（一句话）

`readonly` 从 v1.x 起就是**硬编码 `True`**，它是"这个服务层可以被只读部署"的身份声明，
不是运行期档位。现场已经出现过 `health` 报 `readonly=true` + 写面打 `200` 的矛盾读数，
有人把它读成"部署成功"。裁定驳回"把 `readonly` 接真值"（已有读方按它写判读，可写部署上
突然读到 `false` 会误报警），改成**旧键语义不动、新键 `write_gate` 承载真值**——与
`ma/device-health` 加别名不反向改名同族。

`write_gate` 回答的问题很窄：**本面受闸的写入口现在收不收写**（HTTP 写端点、MCP 真机下发）。
它不管权限/作用域那一道闸（`live_allow`、Tier-0 设备守卫），也不管归档类写入（`save_graph`
本来就不在这道闸里）。

## 2. 三档取值与实测读数（本机真跑，非推导）

| 取值 | 含义 | 成立条件 |
|---|---|---|
| `open` | 写入口放行 | 装配期没降级，**且**租约没被别的进程持有（本进程自己持着不算） |
| `blocked` | 写入口会拒 | 两条来源任一：装配期抢不到单写者租约 ⇒ 写端点 `_readonly_guard` 回 503；或运行期租约被**别的进程**持有 ⇒ `live_run` 里 `_single_writer_check` 抛 503 |
| `no_lease` | 无从判断 | 没有 store 根可探、或探测本身失败，且装配期没给过降级标志 |

同一台实例、同一时刻，只改装配期的 `readonly`：

```
readonly=False -> {"ok": true, "readonly": true, "write_gate": "open",    "store_ok": true}
readonly=True  -> {"ok": true, "readonly": true, "write_gate": "blocked", "store_ok": true}
本进程持锁(=正常 serve) -> {"readonly": true, "write_gate": "open"}
无 store 可探          -> {"write_gate": "no_lease"}
```

**`readonly` 四行全是 `true`，而 `write_gate` 三档都在变**——这两格现在各说各话、且都说对了，
这正是裁定 B 要的形状。

## 3. 对端该怎么判（要改的那一行）

```python
body = GET /api/health
gate = body.get("write_gate")        # 老版 AF 没有这一格 -> None
if gate == "blocked":
    ...   # 这台实例的写入口现在是关的，别再往它发写请求
elif gate == "open":
    ...   # 放行
elif gate is None:
    ...   # 旧 AF：这一格不存在，保持你原来的判读，不要退化成"能写"
else:
    ...   # "no_lease"：AF 自己没看到闸。不许读成 open，也不许读成 blocked
```

两条纪律：

- **别拿 `readonly` 判运行期档位**，它只回答"这个服务层有没有只读部署的能力面"。
- **`no_lease` 不是健康也不是降级**。它和 `linkage.state="unwired"` 是同一族设计：故意不落在
  两态里，就是为了防止"没看"被读成"看过且没事"。

兼容窗口是安全的（纯增键）：新 AF + 旧 DB = 旧判读照旧、多一格没人读；旧 AF + 新 DB =
`get("write_gate")` 拿到 `None`，按上面第三档分支处理即可。所以**推送顺序不必强约束**，
但对端一旦开始用它判写，建议同批把"键不存在"当报警而不是当 `open`。

## 4. AF 侧当场钉住这条口径的判据

`tests/unit/test_serve_lease_single_writer.py` 新增 7 腿（同文件旧 9 腿不动）：

- `test_control_writable_serve_reads_open_while_readonly_stays_true` —— CONTROL，并断言旧键
  仍是 `True`（裁定 §一 驳回 A）；
- `test_boot_degraded_serve_reads_blocked_and_the_write_face_refuses` —— 主判据：health 报
  `blocked` **且** `POST /api/live/run` 真回 503，两边同读一道闸；
- `test_lease_taken_after_boot_still_reads_blocked` —— 启动后才被抢锁：装配期标志说 open，
  探测说 blocked ⇒ 必须读 blocked（`gate == []` 同时证明根本没碰设备）；
- `test_nothing_to_probe_reads_no_lease_not_open` / `test_this_processes_own_lease_reads_open`；
- `test_write_gate_is_driven_by_the_flag_not_a_constant` —— 反空洞：同函数同 store 必须给两个
  不同读数；
- `test_health_route_hands_the_gate_flag_to_the_readout` —— 结构腿：`af_api` 的 `svc.health(...)`
  必须把 `readonly` 递进去（不递就是分叉复活）。

负向自证（临时副本树，不碰工作树）：

| 变异 | 变红的腿 |
|---|---|
| 把 `af_api` 里的 `readonly=readonly` 摘掉 | 结构腿 + 启动降级那腿（2 红） |
| 把 `write_gate()` 掏空成恒返回 `open` | 启动降级 / 锁后被抢 / 反空洞（3 红） |

复现：

```bash
GATES_PYTHON=<本机带依赖的 python> bash gates.sh
<同一枚 python> -m pytest -q tests/unit/test_serve_lease_single_writer.py
```

## 5. 本轮 AF 未做的事（别在对端等）

- **契约表登记**（裁定 Q2 是：`ok`/`readonly`/`write_gate` 一族进契约表、明确区分"身份声明"与
  "运行期档位"两类键名）——裁定原文写明"契约表由 DCD/DB 侧同步"，AF 不代登记。
- **WebUI 还没展示这一格**。本轮只交付读数出口；把 `write_gate` 铺到监护视图头部需要与
  §二（冲突内省 fail-closed）那次指示一起排，避免两个"降级指示"各长一处。
- MCP 面 `af_health` 工具返回值里同样有 `write_gate`，但那一面走**探测档**（MCP 没有装配期
  `readonly` 可递），所以生产 serve 持锁时 MCP 会读成 `blocked`——那是这个面的真值
  （它的真机下发确实被 `_single_writer_check` 拒），不是 bug。

—— AutoForge 开发 · 2026-10-08
