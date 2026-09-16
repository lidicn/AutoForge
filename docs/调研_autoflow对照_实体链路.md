# 调研报告 · 前身 autoflow 对照 AutoForge：实体/设备目录链路

> 日期：2026-09-15　范围：`E:\NAS\autoflow`（前身）↔ `E:\NAS\AutoForge`（本项目）
> 触发疑问：「依赖 MA 的 Entity ID 不靠谱，用户→Agent→MA 一来一回拿 entity_id / friendly_name，太折腾」

---

## 0. 一句话结论

**你的直觉是对的，而且比你想的更严重——这不是"折腾"，是本项目真实存在的架构缺口。**

- AutoForge 本仓库全部代码里，`device_catalog` / `catalog` / `resolve_entity` / `friendly_name` 出现 **0 次**（实测 `search_content` 全 `src/` 零命中）。
- AutoForge 的 16 个 MCP 工具、约 30 个 HTTP 端点里，**没有任何一个能回答「这个中文设备名对应哪个 entity_id」**。
- 但它**已经具备底层能力**：`af_adapters/ha.py:112` 的 `HATransport.all_states()` 能拉全量状态——只是**没暴露**。
- 前身 autoflow 把这一层**做进了网关自身**：`device_catalog` 缓存 + 4 个 MCP 工具 + 域状态契约表，Agent 连同一个 MCP 就能拿真实 ID 直接写 DSL，**一步到位、零跨服务往返**。

所以本质上：AutoForge 把一个「它自己就能做、且底层已铺好路」的能力，**外判给了 MA**。

---

## 1. 现状核对：AutoForge 侧的实体链路（实测）

### 1.1 MCP 工具面（`src/autoforge/af_mcp.py:156-351`）

16 个工具，**零实体查询**：

| 分类 | 工具 |
|---|---|
| 闸门 | `af_build` / `af_compile_spec` / `af_simulate` |
| 归档 | `af_save` / `af_list_graphs` / `af_get_graph` / `af_graphs_by_tag` / `af_diff` |
| 治理 | `af_conf` / `af_set_tags` / `af_enable_by_tag` / `af_export_store` / `af_import_store` |
| 真机 | `af_live_run` |
| 元 | `af_health` / `af_whoami` |

**关键**：`af_build` 的唯一相关参数是 `known_entities: list[str]`（`af_mcp.py:172`）——
它**要求调用方自己先准备好一份实体清单**，却不提供获取这份清单的手段。

### 1.2 HTTP 端点（`src/autoforge/af_api.py`）

约 30 个端点（health / graphs / store / build / sim / conf / metrics / diff / spec / faults / sessions / live / auth），
**无 entities / resolve / catalog 类端点**。

### 1.3 底层能力已具备但未暴露

```112:131:src/autoforge/af_adapters/ha.py
    def all_states(self) -> dict[str, tuple[str, dict[str, Any]]]:
        """拉取全量状态：`{entity_id: (state, attributes)}`；失败返回空 dict。"""
```

`all_states()` 已经能拿到 `entity_id → (state, attributes)`，而 `attributes` 里就有 `friendly_name`。
**解析 friendly_name 所需的原材料全在手上，只是没做成工具。**

### 1.4 项目自己的文档已经承认了这个洞

```39:48:docs/交接卡_吊灯挂灯同步与friendly_name.md
## friendly_name → entity_id：**不能解析**

AF **没有任何** friendly_name → entity_id 的解析能力：
...
**结论**：AF 必须直接写 entity_id（如本卡所给），**无法**用 friendly_name 反查 entity_id，也返回不了正确状态。friendly_name→entity_id 的解析能力在 **memory-agent**（设备目录 `get_entity_catalog` + 身份层 `IdentityReconciler`），不在 AF。
```

### 1.5 结果：真实链路长这样

```
用户说人话
   ↓
Agent  →(跨服务/跨鉴权/跨网络)→  MA：给我「客厅吊灯」的 entity_id + friendly_name
   ↓
Agent 拿 ID 回来写 IR
   ↓
AutoForge：build / sim / save（只能用裸字符串精确匹配）
```

`examples/ir/case01_day_light.json` 用的还是占位 ID（`binary_sensor.study_motion` / `light.study_main`），
印证了「IR 只认裸 entity_id 字符串」这一事实。

---

## 2. autoflow 的做法（源码证据）

### 2.1 四个实体工具（`src/autoflow_gateway/mcp_server.py`）

| 行 | 工具 | 作用 |
|---|---|---|
| `:97` | `autoflow_resolve_entity(name, area="", top_n=8)` | **自然语言设备名 → 候选 entity_id**（写 DSL 前必调） |
| `:116` | `autoflow_list_entities(domain, area, keyword, limit=50, offset=0)` | 全屋实体目录·过滤浏览·分页 |
| `:142` | `autoflow_refresh_catalog(full, domain, area)` | 一次性拉 HA 全量落本地缓存（之后毫秒返回） |
| `:665` | `autoflow_get_entity_state(entity_id)` | 实时读单实体状态 + 缓存兜底 |

外加 `:710 autoflow_delegate_to_memory_worker(task, context_json)` 与 `:736 autoflow_ask_llm(prompt)`
—— 注意这两个是**可选的**、双向对称的委派，**不是实体解析的依赖**。

### 2.2 解析质量：不是简单模糊匹配

```1809:1895:src/autoflow_gateway/gateway.py
    def resolve_entity(self, name: str, area: Optional[str] = None,
                       domain: Optional[str] = None, top_n: int = 5) -> Dict[str, Any]:
        '''自然语言设备名 → Top-N 候选 entity_id（受控选择，消灭 LLM 凭记忆写错 ID）。
        排序优先级（confidence）：
          high   : state.resolve 精确别名/映射命中，或 friendly_name 完全相等
          medium : friendly_name 子串匹配（越靠前、字符串越短越优）
          low    : entity_id 子串匹配
        area 为「优先提示」而非硬约束：优先返回该区域候选；若该区域无匹配则放宽到全局...
```

每个候选返回：`{entity_id, friendly_name, domain, area, state, possible_states, matched_by, confidence}`。

**几个设计细节值得抄**：

1. **不过滤域**：同一设备名可能对应 `light.x` / `switch.y` / `cover.z`，全返回让 agent 自己挑，
   逼它看 `domain + friendly_name` 决定，「不要预设它是 light 还是 switch」。
2. **`possible_states`**：直接告诉 agent「这个实体能在哪些状态间切换」，省去猜 `on/off` vs `open/closed`。
3. **area 是提示不是硬约束**：区域解析失败自动放宽全局，避免「设备未分配区域」把正确设备整段排除。
4. **`_resolve_best` 绝不静默猜域**（`gateway.py:1897`）：只在**无歧义**时自动采纳（恰好 1 个候选 / top 置信度=high），
   否则返回 `None`，**迫使 agent 显式调 `resolve_entity` 从候选中选择**——避免「书房吊灯→select（错）」这类静默错配。
5. **防 DoS 纪律**：`entity_id` 形态字符串**不做**全目录模糊扫描（编造型 ID 不会命中友好名，
   模糊扫描只会造成 N 次 O(目录) 串行阻塞）；resolve 结果带缓存。

### 2.3 目录存储与缓存（`src/autoflow_gateway/state.py`）

```109:125:src/autoflow_gateway/state.py
    def get_device_catalog(self) -> Dict[str, Any]:
        return self._load("device_catalog", {"version": 1, "freshness": "", "entities": {}})
```

- 落盘 `data/<env>/state/device_catalog.json` + `entity_mapping.json`（中文别名→entity_id）。
- **mtime/size 缓存**：同请求内 O(N) 次读盘 → 命中即瞬时（防串行阻塞 DoS）。
- `freshness` 时间戳透明回报，agent 知道数据新旧。

### 2.4 区域解析必须走 websocket（`src/autoflow_gateway/lib/ha_client.py:131`）

HA 的 **REST 不暴露 area/device 注册表**，autoflow 走 `config/entity_registry/list` 等三条 websocket 命令抓取，
并处理「HA 大量实体 `area_id` 为空、区域挂在 device 上」的兜底链：

```
entity.area_id → 否则 entity.device_id → device.area_id
```

> 这个坑 AutoForge 若要自己实现解析，**必然要踩**：只靠 `/api/states` 的 `attributes.friendly_name` 拿不到房间。
> （MA 的 `IdentityReconciler` 就是在补这块，见记忆 `88214461` / `60878043`。）

### 2.5 域状态契约表（`src/autoflow_gateway/lib/affordance.py`）

一张静态表把常见 HA 域的「可能状态 + 可调服务 + 关键参数」硬编码，
让 agent 写 flow 立刻有依据，不用猜，也不会踩 `select` 的 422：

```21:40:src/autoflow_gateway/lib/affordance.py
DOMAIN_AFFORDANCE = {
    "switch": {
        "states": ["on", "off", "unavailable", "unknown"],
        "services": {"turn_on": {}, "turn_off": {}, "toggle": {}},
        "note": "布尔开关。unavailable=离线，不可等同 off。",
    },
    "light": {
        "states": ["on", "off", "unavailable", "unknown"],
        "services": {
            "turn_on": {"brightness_pct": "0-100", "brightness": "0-255", ...},
            ...
```

且 `GLOBAL_STATES = ["unavailable", "unknown"]` 强制「任何实体都可能离线」，
提醒写 flow 必须分支处理——**这与 AutoForge G2 的 `unavailable` 软失效语义天然对齐**。

### 2.6 闸门联动：解析结果直接进校验

`gateway.py:8592 _check_entities_known` 把「DSL 引用的实体」与设备目录比对，
**引用目录外实体直接判 FAIL**——解析与校验闭环，agent 无法拿编造 ID 蒙混。

---

## 3. 逐条对比

| 维度 | AutoForge（现状） | autoflow（前身） | 谁更好 |
|---|---|---|---|
| **实体事实来源** | 无。外包给 MA（跨服务） | 网关自带 `device_catalog`，缓存于自身 | **autoflow** |
| **调用往返** | 用户→Agent→MA(鉴权+网络)→回 AF = **2 跳** | Agent→网关同一 MCP = **0 跳** | **autoflow** |
| **鉴权边界** | 两套令牌（AF 的 + MA butler_token），两套白名单 | 一套 MCP 身份码就够 | **autoflow** |
| **视图一致性** | MA 有 `IdentityReconciler` 合并/别名逻辑，AF 只认裸字符串 → **两边视图可能不一致** | 解析与执行同源同一目录 | **autoflow** |
| **上下文成本** | MA `entity_catalog` 一次可能几十 KB～600KB（MA v0.9 记忆实测 667KB），易撑爆上下文 | `list_entities` 默认 `limit=50` 上限 200 + 强制分页 | **autoflow** |
| **透明性** | 无「是否截断」反馈 | `matched_count/returned/truncated/next_offset` 透明回报 | **autoflow** |
| **离线降级** | MA 不可达 → 整个流程卡死 | `get_entity_state` 实时失败自动回退目录缓存并标注 | **autoflow** |
| **写 flow 契约知识** | 无（agent 自己猜域/状态） | `affordance.py` 域契约表自带 | **autoflow** |
| **安全闸** | ✅ 静态扫描 20 项 + 三道真机闸 + scope 门，**更严谨** | lint + 确认闸 + mode 分层 | **AutoForge** |
| **IR/自研 Runtime** | ✅ 7 节点 6 边、vhass 时间旅行、emit/on event | 编译到 Node-RED flow（本项目已弃用） | **AutoForge** |
| **持久化/多写者** | ✅ v0.9 flock + 租约仲裁 | 无 | **AutoForge** |
| **工程化** | ✅ 双环境绿、交接卡制度、版本路线图 | 巨石模块（gateway 9172 行）、95 历史失败 | **AutoForge** |
| **schema 单一真相源** | 手写 `inputSchema`（16 处） | FastMCP 从签名生成；ACP 从 MCP 投影 + 守卫测试 | **autoflow** |
| **自省** | `af_whoami` 已有 | `autoflow_whoami` 返回**当前 mode 实际可调用工具清单**（文档不写死） | autoflow 略胜 |

**总账**：AutoForge 在「内核严谨度」上全面领先（这是它该保留的资产）；
但 autoflow 在「**把实体事实做成产品内建能力**」这一点上明显更强，而 AutoForge 恰恰把这块外判了。

---

## 4. 可直接借鉴清单（按性价比排序）

### ★★★ 必做：内建实体目录 + 4 个查询工具

**这是直接消灭你痛点的那一项。** AutoForge 底层（`all_states()`）已铺好路。

建议落点：

| 新增/改 | 内容 |
|---|---|
| `src/autoforge/af_catalog.py` [新] | `DeviceCatalog`：从 `HATransport.all_states()` 拉全量 + 落盘缓存（`{root}/catalog.json` + `freshness`）；`resolve(name, area, domain, top_n)`（high/medium/low 置信度 + `matched_by` + `possible_states`）；`list_entities(domain, area, keyword, limit, offset)`（强制分页 + 透明截断回报）；`get_state(entity_id)`（实时 + 缓存兜底） |
| `af_mcp.py` [改] | 新增 4 个工具：`af_resolve_entity` / `af_list_entities` / `af_refresh_catalog` / `af_get_entity_state` |
| `af_api.py` [改] | 同 4 个只读端点（供 WebUI 控制台设备面板用） |
| `af_service.py` [改] | 4 个纯逻辑函数（HTTP / MCP / CLI 同源） |
| `af_cli.py` [改] | `forge entities list/resolve/refresh` |
| `af_adapters/ha.py` [改] | 加 websocket 注册表抓取（area/device），**或**第一版先接受「无 area 解析」 |

**关键增益**：Agent 连 AutoForge 的 MCP 就能「查设备→拿真 ID→写 IR→build→sim→save」全链路闭环，
**不再需要 MA 参与**。MA 回归它的本职（语义/身份/历史/习惯），AF 只管「执行所需的实体事实」。

### ★★★ 闸门联动：把 `known_entities` 默认接到目录

现状 `af_build(ir, known_entities=...)` 要求调用方自备清单（`af_mcp.py:172`）。
改造后：`known_entities` 缺省时**自动取本机目录全集**——「实体存在性校验」（G2 第 11 项）从"可选"变成"默认开启"。

### ★★ 域状态契约表（抄 `affordance.py`）

移植到 `af_affordance.py`，附加到 `af_resolve_entity` / `af_list_entities` 的返回里。
**与 AutoForge 已有语义天然对齐**：`GLOBAL_STATES=unavailable/unknown` ↔ G2 的「实体不存在走 `on_error` 软失效」。

### ★★ 防 DoS 三纪律（抄设计思路，不抄代码）

1. `entity_id` 形态字符串**不做**模糊扫描；
2. resolve 结果缓存 + 目录 mtime 缓存；
3. 目录查询强制分页 + `truncated/next_offset` 透明回报。

### ★ 其他小项

- **`af_whoami` 增强**：返回「当前 scope 实际可调用的工具清单」（文档不写死工具表，防过期）；
- **schema 单一真相源**：现在 16 个工具手写 `inputSchema`，可加守卫测试「`TOOLS` 与 `_t_*` 签名一致」；
- **ACP 双向委派**：autoflow 有 `delegate_to_memory_worker` / `ask_llm` 对称双向；
  AutoForge 目前只有单向 metrics 回灌 MA（v0.5.0），可考虑补「AF→MA 记忆检索」的反向调用。

---

## 5. 建议的最小落地路径（不破坏现有内核）

```
第 1 步（隔离、低风险）：新增 af_catalog.py（纯派生，只读 HA REST /api/states）
        → 不碰 IR、不碰 scanner、不碰 Runtime，零回归风险
第 2 步：af_service 加 4 个纯函数 + af_mcp 加 4 个只读工具（scope=None，无需鉴权）
        → Agent 即可「查设备→写 IR」，痛点当场消失
第 3 步：af_build 的 known_entities 缺省接目录全集（实体校验默认开启）
第 4 步（可选）：websocket 注册表 → area/device 解析，补齐「房间维度」
        → 这一步 MA 的 IdentityReconciler 更强，可考虑 AF 只做「精确/子串解析」，
          房间/身份合并继续留给 MA，两边**职责不重叠**（AF=执行事实，MA=语义身份）
```

**与 MA 的新分工建议**：

| 谁 | 回答什么 |
|---|---|
| **AF（新）** | 「『书房吊灯』的 entity_id 是哪个？它有几种状态？现在是什么状态？」（**执行事实**，毫秒级） |
| **MA（保持）** | 「谁在家？这家人作息如何？『吊灯』和『挂灯』历史上怎么联动过？」（**语义/身份/历史**） |

这样 MA 的 `get_entity_catalog` 就不再是 AF 的**前置依赖**，而是一个**可选增强**——
MA 挂了，AF 照样能查设备写自动化。**这才是你想要的解耦。**

---

## 6. 附：autoflow 其他值得一提之处（非本次主线）

| 项 | 说明 |
|---|---|
| ACP 双向委派 | `delegate_to_memory_worker` / `ask_llm` 与对端 `delegate_to_autoflow` 对称；委派不绕安全护栏 |
| 三面板 × mode 双因子 | `/mcp`(normal) / `/mcp-white`(expert) / `/mcp-admin`(developer)，path+mode 双重判定能力 |
| `device_guard.py` | 设备保护注册表（Tier-0 强制人审 / Tier-1 记审计）——比 AF 的实体 ACL 更细 |
| 提案 source 徽章 | 「编译产物(可信) / 手写(需审)」审阅时一眼区分 |
| 经验库三件套 | `experience.py`（实体共现/DSL 模式采集）+ `error_knowledge.py`（失败归因→修复建议）+ `template_lib.py` |
| 文档反过期纪律 | 「工具清单不写进文档，让 agent 调 `whoami` 实时取」——AutoForge 的 README 工具表已经出现过表述不一致，值得借鉴 |

**反面教材（不要学）**：`gateway.py` 9172 行巨石模块、`restore_snapshot` 整实例还原事故、
95 个历史测试红未清——这些是 AutoForge 已经做得更好的地方，别回退。
