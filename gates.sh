#!/usr/bin/env bash
# 质量门禁本地入口 —— 复制到仓库根，`chmod +x gates.sh`
#
# 适用：AutoForge、doubao-butler（没有 GitHub remote，CI 无从挂起）。
# 有 remote 的两家请用 templates/ci/gates.yml。
#
# 退出码就是结论：0 过 / 1 有未获批违规 / 2 环境或配置不对。
# 验收单里贴这条命令的**完整输出**，不接受「跑过了」。

# R-WO-GATE-002：本脚本用了 bashism（${PIPESTATUS[0]}、pipefail）。非 bash（dash/sh）
# 下 AST 红/AST 绿/冒烟红三态会塌成一态、import 冒烟门静默不跑。第一道门之前先自检：
# 非 bash 立即 exit=126（不复用 1=设计红，以免把"闸自己坏了"藏进"红是设计"）。
if [ -z "${BASH_VERSION:-}" ]; then
  echo "gates.sh: 需要 bash（当前 shell 无 BASH_VERSION）；dash/sh 会让 \${PIPESTATUS[0]} 塌三态。请用 bash gates.sh。" >&2
  exit 126
fi

set -uo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
# 冒烟解释器：本地跑就用自己的 venv；要在容器里跑，改成
#   docker exec <容器> sh -c 'cd /app && homesdk-gates . --config .gates.toml'
PYTHON="${GATES_PYTHON:-python3}"

if ! "$PYTHON" -c "import homesdk.gates" 2>/dev/null; then
  echo "homesdk 未安装。先执行："
  echo "  $PYTHON -m pip install -e E:/NAS/homesdk        # 开发机"
  echo "  # 或 pip install /vol1/1000/docker/libs/homesdk/dist/homesdk-0.1.0-py3-none-any.whl"
  exit 2
fi

if [ ! -f "$REPO/.gates.toml" ]; then
  echo "缺少 $REPO/.gates.toml —— 从 E:/NAS/AgentOps/gates/<仓名>.gates.toml 复制一份再改名。"
  exit 2
fi

echo "══ AST 门禁（不含冒烟）═══════════════════════════════════════"
"$PYTHON" -m homesdk.gates "$REPO" --no-smoke
ast_rc=$?

echo
echo "══ 计数棘轮（全量总数对登记上限）══════════════════════════════"
# 为什么单独立一条：AST 门判的是「新增」。有人往 .gates-baseline.txt 里追加指纹时，
# 新增永远是 0，存量却在悄悄肥化。棘轮比的是**全量总数**对登记上限，堵的就是这条缝。
# 提示文案里不要用反引号：双引号内的反引号会被 bash 当命令替换真的执行（本轮实测踩到）。
if [ ! -f "$REPO/.gates-tally.txt" ]; then
  echo "缺 $REPO/.gates-tally.txt —— 先跑一次「$PYTHON -m homesdk.gates \"$REPO\" --no-baseline --no-smoke」，把全量计数登记成「总数 # 日期 说明」。"
  exit 2
fi
cap=$(awk '$1 ~ /^[0-9]+$/ {print $1; exit}' "$REPO/.gates-tally.txt")
total=$("$PYTHON" -m homesdk.gates "$REPO" --no-baseline --no-smoke 2>&1 \
        | sed -n 's|.*新增/未获批 \([0-9]\+\) 条.*|\1|p' | tail -1)
if [ -z "$cap" ] || [ -z "$total" ]; then
  echo "棘轮不判绿：解析不到计数（上限=${cap:-空} / 全量=${total:-空}）。输出格式变了就该红，不该沉默。"
  exit 2
fi
echo "全量违规 $total 条 / 登记上限 $cap 条"
tally_rc=0
if [ "$total" -gt "$cap" ]; then
  echo "棘轮红：总数从 $cap 涨到 $total。要么修掉，要么在「.gates-tally.txt」写明为什么必须上调——上调本身要评审。"
  tally_rc=1
elif [ "$total" -lt "$cap" ]; then
  echo "棘轮提示：总数降到 $total，请把「.gates-tally.txt」的上限同步下调（只准降 = 防肥化）。"
  tally_rc=1
fi

echo
echo "══ undefined-name 门禁（标准库 AST，零依赖）═══════════════════"
# 审计 REG-3：这一类（用了没定义的名字）在既有门禁体系里无人看守，而它能让整套测试
# 连收集都跑不起来。只依赖标准库——本机禁 pip install，要装包的门禁等于没有门禁。
"$PYTHON" "$REPO/scripts/check_undefined_names.py" "$REPO/src"
name_rc=$?
"$PYTHON" "$REPO/scripts/check_undefined_names.py" "$REPO/tests"
tests_name_rc=$?

echo
echo "══ 主题白名单门禁（ADM 契约表为唯一真源）══════════════════════"
# 契约表 §五「代码里出现的 topic 必须在本表登记（未登记的判红）」、§六「加主题 = 改本表」。
# 没有基线放行一说：未登记主题 = broker ACL 与代码口径不一致，属上线风险，不属风格问题。
"$PYTHON" "$REPO/scripts/check_topic_whitelist.py" "$REPO/src/autoforge"
topic_rc=$?

echo
echo "══ 包标记门禁（grimp 递归的前提交互，锁 CI/本机同口径）══════════"
# 实测缺陷：`.gitignore` 的 `_*.py` 连带吃掉 `__init__.py`（`_`+`*`=`__init__`+`.py`），
# src/autoforge/af_closedloop/__init__.py 在盘上躺了十几天、从未入库。grimp 对没有包标记的
# 目录不递归 ⇒ runner 上架构门禁只分析 86 个模块、本机 96 个，CI 门比本机门弱。
# 判据取 git 索引（不是磁盘 walk）：磁盘上文件在、本地永远检不出，只有索引口径两边一致。
"$PYTHON" "$REPO/scripts/check_pkg_markers.py" "$REPO/src"
pkg_rc=$?

echo
echo "══ 状态源扇出门禁（换 runtime.states 必须四个消费方同步）════════"
# Runtime.__post_init__ 只在构造时把 states 交给 instances/scheduler/executor；构造之后再换源
# 就靠调用点手抄四行。少抄一行不报错、不崩，只让那一方继续读旧状态源（canary 漂移检测读到空
# InMemoryStateProvider 就是第七轮审计那一族），且现有测试对"少一行"没有一条会变红 ⇒ 判据进门禁。
"$PYTHON" "$REPO/scripts/check_states_fanout.py" "$REPO/src"
fanout_rc=$?

echo
echo "══ 参数注入门禁（带默认值的关键参数不许静默漏传）════════════════"
# §二之十六 / §二之十八 各抓到一处：`live_run`/`health` 的 `store`、`build_app` 的 `readonly`
# 都带默认值 ⇒ 调用点少递一个关键字参数**不报错、不崩**，只把那条入口面的 Tier-0 设备保护、
# 存储健康读数、只读闸门静默退化成"没有这道闸门"。默认值把漏传变成静默降级，所以判据落在
# 调用边界上静态判，不靠人记得"两面都要递"。`clock` 有意不收：它的默认值是仿真锚点、是设计。
"$PYTHON" "$REPO/scripts/check_param_injection.py" "$REPO/src"
store_rc=$?

echo
echo "══ 工具名单门禁（MCP 工具名只有一个注册表）═══════════════════════"
# `af_mcp.TOOLS` 是工具名的唯一真源。盘"TOOLS→caps 之外还有没有第二份名单"时抓到两处：
# `af_orchestrator.observe()` 按名调 `af_live`（注册名其实是 `af_live_run`，`_call_safe` 把异常
# 吞成 `{"ok": False}` ⇒ 这条路永远不响），`af_runtime_ext.mcp_tools()` 另抄一份五字典型名单且
# 从未接线（其中 `af_approve_proposal` = Agent 自批提案，与裁定 20261002 §三 ④A 正面冲突）。
# 两处都不报错、不崩，只会让该红的不红 ⇒ 判据静态判；读不到 TOOLS 时 exit 2，不做假绿。
"$PYTHON" "$REPO/scripts/check_tool_names.py" "$REPO/src"
tool_rc=$?

echo
echo "══ MCP 参数↔schema 门禁（消费的参数必须已声明，声明的必须被消费）════"
# 安全审计包（fp-authcode-bruteforce 加重情节 3）盘出的形状：`dispatch()` 早先从不把
# `arguments` 与 `inputSchema` 对账 ⇒ "schema 未声明却可用"。首跑实测：31 个工具里 6 个键
# 被 handler 消费却没声明，其中 `allow_bulk` 是**爆炸半径护栏的绕过位**，同时挂在
# `af_save`/`af_enable_by_tag`/`af_import_store` 三个写面上——`tools/list` 看不见它，调用却生效。
# 现在 `dispatch()` 拒未声明顶层键（声明即契约），本门钉这条新契约会烂掉的两向：
# 消费未声明（拒绝后变成**静默失效**，比原来更隐蔽）、声明未消费（对调用方撒谎）、
# schema 缺 `properties`（那道拒绝在该工具上静默关闭）。参数读取判不出 = exit 2，不跳这条。
"$PYTHON" "$REPO/scripts/check_mcp_arg_schemas.py"
mcp_args_rc=$?

echo
echo "══ 状态源 fail-closed 门禁（snapshot() 不许静默省略未知实体）══════"
# 第七轮审计的 key_finding：`af_ir/expr.py` 的 `and`/`or` 走 all()/any() **短路**，没被求值的
# 那一支永远不会去读快照 ⇒ "缺失留给运行时发现"在 fail-open 一侧根本不成立：仿真软失效不执行、
# 生产照另一支执行，同一条 IR 两个相反结论。当时用四对多实现契约测试钉住口径，但契约测试各自
# 只认自己那几个类——新增一个忘了 raise 的 provider，现有测试一条都不会红 ⇒ 射程内的实现静态判；
# 读不到 `StateProvider.snapshot()` 锚点时 exit 2，不做假绿。
"$PYTHON" "$REPO/scripts/check_snapshot_policy.py" "$REPO/src"
snap_rc=$?

echo
echo "══ 出向 MQTT 写者门禁（事件只许一条生产者，载荷必经 _envelope）══"
# §二之二十二 盘出的形状：那条"逐字段对契约"的测试一直绿，但它测的是 publish_fired/publish_failed 的
# 直接调用路径，而生产唯一发事件的路径是 observe_terminal()——后者多发一个契约表 §1.2 没列的 node_id。
# 两条路各测一头 ⇒ 真实载荷与契约行不一致而**没有一条测试红过**。本门把"下一个写者"钉住：
# 出向 MQTT 只能从 af_mqtt_bridge 走、事件只能由 observe_terminal 产生、载荷必须经 _envelope()。
"$PYTHON" "$REPO/scripts/check_mqtt_writers.py" "$REPO/src"
writers_rc=$?

echo
echo "══ 入向订阅门禁（只订 ma/insights，动态主题要先过禁订族）══"
# 契约表 §1.3 护栏 + 计划 第 1 步 ④：收件箱是 DB 的，AF 不替 DB 说话。今天这条只有运行时判定 +
# 行为测试，而行为测试只认识已知入口——新加一个不查 FORBIDDEN_SUBSCRIPTIONS 的 subscribe() 一条都不会红。
# 本门钉"下一个订阅入口"：订阅口只在桥里、动态主题当场过守卫、收件箱族写死就红。
"$PYTHON" "$REPO/scripts/check_mqtt_subscriptions.py" "$REPO/src"
subs_rc=$?

echo
echo "══ UI↔路由契约门禁（前端调的路径+方法必须真在路由表里）══════"
# §二之二十七 记账时盘出的形状：`ui/` 没有 vitest，UI 侧判据是 vue-tsc + vite build + 真浏览器读数，
# 前两条只证"能编译"。路径是手抄字符串——服务端改名/删路由/GET 换 POST，前端照编译照 build，
# 只有真点一次才 404/405。本门钉跨层契约：每个调用点的路径都要命中一条参与匹配的路由
# （SPA 兜底 `GET /{full_path:path}` 与 `POST /mcp` 排除在外，否则任何错路径都被兜底接住＝假绿），
# 且**每个**调用点都必须解析得出来——解析不出是 exit 2，不是"跳过这条"（早期正则版就是这样谎报 0）。
# `--all` 而不是只指 `ui/src`：本仓有三棵第一方 UI 树，另两棵（`ui-user`/`ui-user-mimo`，用户端
# ForgeSight）调的正是 `/automations*`、`/user/agents*`、`/auth/*` 那一批。只扫开发面板那棵树时，
# 反向读数把 33 条报成"UI 从未调"，其中 17 条其实是射程外的活接口（§二之三十一 盘点）。
# 登记表外多出一棵形状像 UI 的树 ⇒ 同样 exit 2，不让"漏一棵树"以绿行过关。
"$PYTHON" "$REPO/scripts/check_ui_api_paths.py" --all
ui_api_rc=$?

echo
echo "══ 联动桥依赖门禁（paho：声明处 / 交付面 / CI 面 三面一致）══════"
# §二之二十六 盘出：paho 在 AF 整条依赖链里**一处声明都没有**——homesdk 把它放在自家 `[mqtt]` extra
# （"装它是对调用方的要求"），两个镜像装的又是裸 wheel，AF 的 `.[api,ha]`/`.[dev]` 也不含它。开发机
# 一切正常，只因那份解释器手动装过 paho。后果不是"少一个用例"而是镜像里桥原理上连不上：
# `AUTOFORGE_MQTT=1` ⇒ serve 抛 `MqttUnavailable` 拒绝启动（还是发生在停机窗里）；不开 ⇒ 计划
# 第 1/2 步的验收（`status=online`、抓到一条 fired）永远取不到数，而两千多条测试全绿
# （桥的测试用 duck-typed client，压根不 import paho ⇒ §二之二十二 那一族的依赖版）。
# 依赖声明的形状静态可判 ⇒ 进门禁，不靠"下次记得装"。
"$PYTHON" "$REPO/scripts/check_mqtt_runtime_dep.py" "$REPO"
dep_rc=$?

echo
echo "══ 有界缓存注册表门禁（TTL 与硬上限成对，且回收要有测试钉住）══════"
# 第六轮审计 §三 把"新增有界缓存必须同时给 TTL 与硬上限，并在测试里断言纯写不读也被回收"记成
# "约定 + 门禁可见"，但约定的两半里当时没有任何能判红的东西（只活在审计正文里）。裁定 20261004 §一 3
# 选 B（注册表式）而不是 C（统一基类）：Python 里"有界"往往长在键空间或调用方，不在容器自身，
# 天真静态口径首跑命中 76 个增长容器、真两条腿齐全的只有 2 个 ⇒ 硬扫只会得到两条永久红 + 一张豁免表。
# 所以本门不猜"有没有界"，只核对 `af_bounded_caches.py` 说没说实话：两条腿的名字要在模块里、
# 测试 id 要真被 pytest 收集、新增容器要登记或就地带理由豁免、基线只减不增。
"$PYTHON" "$REPO/scripts/check_bounded_caches.py" "$REPO/src/autoforge"
cache_rc=$?

echo
echo "══ import 冒烟（解释器：$("$PYTHON" -V 2>&1)）════════════════════"
# 单跑冒烟：只走 `import` 子进程，慢但一次性看清。
# 注意：这条在开发机上的红多半是「依赖没装齐 / 本地副本不完整」，
#       结论以容器内跑出来的为准（见 README 第三节）。
"$PYTHON" -m homesdk.gates "$REPO" --only-smoke 2>&1 | sed 's/^/  /'
smoke_rc=${PIPESTATUS[0]}

echo
if [ $name_rc -ne 0 ] || [ $tests_name_rc -ne 0 ]; then
  echo "结论：undefined-name 门禁红（src=$name_rc / tests=$tests_name_rc）。这类名字在运行期就是 NameError，没有『基线放行』这一说——补 import 或删掉误用。"
  exit 1
fi
if [ $topic_rc -ne 0 ]; then
  echo "结论：主题白名单门禁红（exit=$topic_rc）。先在 ADM 主题契约表登记，再改代码；不存在『基线放行』。"
  exit $topic_rc
fi
if [ $pkg_rc -ne 0 ]; then
  echo "结论：包标记门禁红（exit=$pkg_rc）。1=有包目录的 __init__.py 没入库，CI 上 grimp 不递归、架构门禁比本机少分析模块；2=拿不到 git 索引。"
  exit $pkg_rc
fi
if [ $fanout_rc -ne 0 ]; then
  echo "结论：状态源扇出门禁红（exit=$fanout_rc）。换 runtime 的 states 必须同时写 instances/scheduler/executor 四条；少写的那方会继续读旧状态源，而这类改法没有任何测试会红。"
  exit $fanout_rc
fi
if [ $store_rc -ne 0 ]; then
  echo "结论：参数注入门禁红（exit=$store_rc）。签名里有 \`store\`/\`readonly\` 的函数，调用点必须把它递过去；确实不需要（如纯仿真面、默认值就是设计）就地写 \`# param-injection: exempt(理由)\`——漏传不报错，只会让那道闸门静默少装一面。"
  exit $store_rc
fi
if [ $tool_rc -eq 2 ]; then
  echo "结论：工具名单门禁读不到注册表（exit=2）。\`af_mcp.TOOLS\` 的锚点形状变了，本门此刻无从判定——报『干净』就是假绿，先把脚本里的解析口径对上真实注册表。"
  exit $tool_rc
fi
if [ $tool_rc -ne 0 ]; then
  echo "结论：工具名单门禁红（exit=$tool_rc）。按名调 MCP 工具只能用 \`af_mcp.TOOLS\` 里的名字；\`TOOLS\` 之外再抄一份字典名单（哪怕是仿真/未来的）就是第二真源。确实不是工具名就地写 \`# tool-name: exempt(理由)\`。"
  exit $tool_rc
fi

if [ $mcp_args_rc -eq 2 ]; then
  echo "结论：MCP 参数↔schema 门禁读不出参数形状（exit=$mcp_args_rc）。TOOLS 五元组、handler 具名函数、inputSchema 字典字面量是判据的地基；handler 把 \`args\` 整包转发给 helper 也在这一档——本门此刻无从核对，报『干净』就是假绿。"
  exit $mcp_args_rc
fi
if [ $mcp_args_rc -ne 0 ]; then
  echo "结论：MCP 参数↔schema 门禁红（exit=$mcp_args_rc）。\`dispatch()\` 现在按声明拒未声明的顶层键：消费未声明＝参数被拒后**静默失效**，声明未消费＝\`tools/list\` 对调用方撒谎，缺 \`properties\`＝那道拒绝在这个工具上根本没装。故意留一侧就地写 \`# mcp-args: exempt(理由)\`。"
  exit $mcp_args_rc
fi

if [ $snap_rc -eq 2 ]; then
  echo "结论：状态源 fail-closed 门禁读不到锚点（exit=2）。\`StateProvider.snapshot() -> Snapshot\` 的声明形状变了，本门此刻无从判定射程——报『干净』就是假绿，先把脚本里的锚点口径对上真实契约。"
  exit $snap_rc
fi
if [ $snap_rc -ne 0 ]; then
  echo "结论：状态源 fail-closed 门禁红（exit=$snap_rc）。返回 \`Snapshot\` 的状态源必须对未知实体 \`raise UnknownEntity(…)\`：\`and\`/\`or\` 短路 ⇒ fail-open 的实现永远读不到缺的那一支，仿真与生产会对同一条 IR 给出相反结论。故意 fail-open 且已裁定就地写 \`# fail-closed: exempt(理由)\`。"
  exit $snap_rc
fi

if [ $writers_rc -eq 2 ]; then
  echo "结论：出向 MQTT 写者门禁读不到锚点（exit=$writers_rc）。\`FIRED_TOPIC\`/\`FAILED_TOPIC\`/\`_envelope()\`/\`observe_terminal()\` 是三条判据的地基——改名或挪走会让本门静默全绿，先把口径对上真实模块再说干净。"
  exit $writers_rc
fi
if [ $writers_rc -ne 0 ]; then
  echo "结论：出向 MQTT 写者门禁红（exit=$writers_rc）。出向消息只允许 \`af_mqtt_bridge\` 一个写者、事件只在 \`observe_terminal()\` 里产生、载荷必须经 \`_envelope()\`：绕过任何一条，对端收到的就是没人验过的形态（\`ts\` 口径、\`ref\` 语义、QoS、发布失败留痕都在桥里）。破例要裁定，就地写 \`# mqtt-writers: exempt(理由)\`。"
  exit $writers_rc
fi

if [ $subs_rc -eq 2 ]; then
  echo "结论：入向订阅门禁读不到锚点（exit=$subs_rc）。\`INSIGHTS_TOPIC\`/\`FORBIDDEN_SUBSCRIPTIONS\`/\`subscribe_topic()\`/\`handle_message()\` 是本门判据的地基——主题改名或禁订族挪走会让本门静默全绿，先把口径对上真实桥再说干净。"
  exit $subs_rc
fi
if [ $subs_rc -ne 0 ]; then
  echo "结论：入向订阅门禁红（exit=$subs_rc）。AF 的耳朵只该有一只：订阅口只在 \`af_mqtt_bridge\` 里、动态主题要在同一函数体内先过 \`FORBIDDEN_SUBSCRIPTIONS\` 判定、收件箱族不许写死成订阅实参（契约表 §1.3 护栏 / 计划 第 1 步 ④）。确实要开新入向主题先在 ADM 主题契约表登记，破例就地写 \`# mqtt-subscriptions: exempt(理由)\`。"
  exit $subs_rc
fi

if [ $ui_api_rc -eq 2 ]; then
  echo "结论：UI↔路由契约门禁读不到锚点，或有调用点解析不出（exit=$ui_api_rc）。三种情形：\`ui/src/api/client.ts\` 不在预期位置、\`src/\` 下扫不到参与匹配的路由、某个 \`request(…)\` 的路径/方法静态读不出来（变量拼路径、引号不闭合、认不出的动词）。前两种是射程塌了，第三种是这条调用点**从没被看过**——都把门变成『没有发现』而不是『没有问题』。改成静态可读的写法，或就地写 \`# ui-api: exempt(理由)\`。"
  exit $ui_api_rc
fi
if [ $ui_api_rc -ne 0 ]; then
  echo "结论：UI↔路由契约门禁红（exit=$ui_api_rc）。前端每抄一条路径都得能在 \`af_api.py\` 的路由表里落到一条真路由上，方法也要对得上：服务端改名/删路由/换方法时，vue-tsc 与 vite build 都照样绿，只有用户点一次才 404/405。确实要调路由表外的口子（外链、代理）就地写 \`# ui-api: exempt(理由)\`。"
  exit $ui_api_rc
fi

if [ $dep_rc -eq 2 ]; then
  echo "结论：联动桥依赖门禁读不到锚点（exit=$dep_rc）。pyproject、桥、两份 Dockerfile、工作流任一处不在预期位置，或桥里已经找不到 \`from homesdk import mqtt\`——射程前提变了，本门此刻无从判定，报『干净』就是假绿；桥若真被删/换机制层入口，要**当场决定本门去留**，不能让它静默全绿。"
  exit $dep_rc
fi
if [ $dep_rc -ne 0 ]; then
  echo "结论：联动桥依赖门禁红（exit=$dep_rc）。paho-mqtt 要**声明在一处、装到三个面**（交付面 \`docker/Dockerfile.api\`、CI 面 \`docker/Dockerfile.test\` 与 \`.github/workflows/ci.yml\`）：只在本机装过不算修。镜像里没它 ⇒ 窗内开 \`AUTOFORGE_MQTT=1\` 时 serve 抛 \`MqttUnavailable\` 拒绝启动，不开 ⇒ 桥永远不上线而两千多条测试照绿（它们用 duck-typed client，不碰真库）。"
  exit $dep_rc
fi

if [ $cache_rc -eq 2 ]; then
  echo "结论：有界缓存注册表门禁读不出（exit=$cache_rc）。三种情形：\`src/autoforge/af_bounded_caches.py\` 不在预期位置或两张表不再是纯字面量字典、注册指向的测试文件 pytest 收集失败、扫描器在 \`src/autoforge\` 下读到 0 个增长容器。三者都是射程塌了——此刻本门无从判定，报『干净』就是假绿。"
  exit $cache_rc
fi
if [ $cache_rc -ne 0 ]; then
  echo "结论：有界缓存注册表门禁红（exit=$cache_rc）。注册表说的那条腿必须真在模块里（\`cap\`/\`ttl\`/\`trim\` 逐个核对）、指向的测试必须真被收集、新增增长容器必须进 \`BOUNDED_CACHES\`（两条腿 + 一条『纯写不读也被回收』的测试）或在那一行写 \`# bounded-cache: exempt(理由)\`；基线名单只减不增。约定来自第六轮审计 §三，落法来自裁定 20261004 §一 3 B。"
  exit $cache_rc
fi

if [ $ast_rc -ne 0 ]; then
  echo "结论：AST 门禁红（exit=$ast_rc）。修，或在 .gates-baseline.txt 里逐条写明放行理由。"
  exit $ast_rc
fi
if [ $tally_rc -ne 0 ]; then
  echo "结论：计数棘轮红（全量 $total / 上限 $cap）。基线只准减少——要么把新增的修掉，要么在评审里说明为什么必须上调上限。"
  exit 1
fi
if [ $smoke_rc -ne 0 ]; then
  echo "结论：冒烟红（exit=$smoke_rc）。先在容器里复跑一次再定性——见 README 第三节。"
  exit $smoke_rc
fi
echo '结论：门禁干净。注意 import 通过不等于服务能起，验收仍要 compose ps + HTTP。'
exit 0
