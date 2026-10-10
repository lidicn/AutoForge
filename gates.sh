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
# 退出码单独不够：依赖门禁**崩掉**时 Python 也退 1（第十五轮 F16 实测 `RecursionError` ⇒ RC=1），
# 与"判出违规"同形。把输出交给分类器分三档，崩＝无从判定（RC=2），不许按真红去补基线。
ast_out=$("$PYTHON" -m homesdk.gates "$REPO" --no-smoke 2>&1)
ast_raw_rc=$?
printf '%s\n' "$ast_out"
ast_class=$("$PYTHON" "$REPO/scripts/classify_homesdk_run.py" --rc "$ast_raw_rc" <<< "$ast_out")
ast_class_rc=$?
printf '%s\n' "$ast_class"
ast_rc=$ast_class_rc

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
echo "══ 原子写站点门禁（固定名 .tmp 不许是新增形状）════════════════════"
# 安全审计那份 zip 的 out_of_scope 14 个单元里盘出的另一族：`af_store._atomic_write` 的
# docstring 把 P1-18 的修法写得明白（随机 tmp 名 + fsync + 目录 fsync），可这条纪律只落在了
# af_store 自己头上。AST 盘 src 全集实测 16 站：一半以上仍是"固定名 tmp + 裸 write_text +
# os.replace"，而 `af_persist.save` 的 docstring 还写着"原子替换：崩溃时不会留半截文件"。
# 坏的形状不是慢一点而是**静默丢数据**：`PersistStore.claims()` 明确允许两个进程在租约到期后
# 驱动同一条实例 ⇒ 两边写同一个 {id}.json.tmp ⇒ 交错内容被最后一次 replace 装上 ⇒ 读侧对校验和
# 失败的记录是跳过，那条活着的实例记录就此消失、不报错也不告警。本批修 4 站（af_persist 的实例
# 记录、af_api 的启停写→store.resave_raw、af_store 的标签删除、af_catalog 三站+洞察队列助手），
# 其余 9 站进 .atomic-write-baseline.txt 逐条写理由（只减不增）。扫不到站点 = exit 2，不跳这条。
"$PYTHON" "$REPO/scripts/check_atomic_write_sites.py"
atomic_rc=$?

echo
echo "══ 出站护栏门禁（第一方出站不许直连 urlopen）══════════════════════"
# 第十四轮审计 F15：白名单护栏本身是齐的（host_of() 判 netloc 的 @ 凭证注入，_WhitelistRedirector
# 在跟 3xx 之前重校验 Location），问题是**有代码绕开它**——af_catalog / af_live / af_metrics /
# af_registry 四处各自直连 urlopen，用的是默认 opener：跟 3xx 且不重校验。af_metrics 那一处带
# Bearer 凭据，一次 3xx 就能把凭据转到白名单外的主机。bandit 的 B310 只报了 2/4：另两处是
# (opener or urllib.request.urlopen)(...) 与 self._opener = opener or urlopen ——被调者是布尔
# 表达式 / 函数对象被当值交出。本门按 AST 认这两种间接形状，注释里写 urlopen 不算命中。
"$PYTHON" "$REPO/scripts/check_outbound_guard.py"
outbound_rc=$?

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
echo "══ 计划表口径门（docs/plan 那份表的 ✅ 必须落在门的认领读数上）══"
# §六 那条登记的原文是"其余 ✅ 行未经逐行复测"：上一批人工对过一次表（会话族三行改成
# `后端 ✅ ／ UI ✗`），但"文档比代码乐观"这一族漂移没有任何机械接缝——面板拆了、路由改名了，
# ✅ 还留在原处，vue-tsc 与 pytest 都不知道那份表说了什么。本门把那次人工对表变成每批的判据，
# 服务端路由表与反向未认领名单两个集合都从上面那条 UI↔路由门**现取**（不建第二份名单）。
"$PYTHON" "$REPO/scripts/check_plan_ui_claims.py"
plan_claims_rc=$?

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
echo "══ CI 解释器口径门（ci.yml 手抄钉值一致 / 满足包声明 / 与镜像 base 的差要显式认领 / README 口径表对撞真源）══"
# 第六轮审计 ARCH-03 复测（§二之九十七）：ci.yml 的四个 Python 作业把解释器**手抄了四遍**
# （:16、:36、:57、:90 全是 "3.11"），包声明是 pyproject.toml:10 的 requires-python = ">=3.11"，
# 两份镜像 base 是 3.14（docker/Dockerfile.api:10、docker/Dockerfile.test:13），而 README 快速开始原本
# 明写「需要 Python 3.14+」。四枚手抄没有任何东西保证同步；包下限抬一次（例如为了跟镜像对齐）而钉值不跟上，
# CI 会继续绿并给出一条没被验过的交付环境。而"CI 向镜像对齐还是镜像向 CI 对齐"属交付/验证口径 ⇒ 本门不替
# 主人拍板，判四条形状：钉值彼此相等、钉值被包声明允许、与镜像 base 的差挂着锚点核对得住的登记、
# README 那张口径表逐格等于它自己点名的真源现读值（裁定 20261011《十三问》§3 Q4.1 裁「以 requires-python
# 为单一真源，⛔ 不得只改一头」）。真仿真那条链在 CI 上恒 skip（pyproject.toml:84 的 addopts
# 默认不加载 pytest-homeassistant 插件），所以本门不假装 CI 验过仿真——那一条在 docker 面上。
"$PYTHON" "$REPO/scripts/check_ci_interpreter.py" "$REPO"
interp_rc=$?

echo
echo "══ 依赖下界门（每一枚依赖都带约束 / 在册下界逐枚 extra 同值 / 依据指得到裁定与读数）══"
# 裁定 20261011《十三问》§3 Q4.3 裁「仿真依赖钉版本：做」。pytest-homeassistant-custom-component 原本在
# sim 与 dev 两枚 extra 里各抄一遍、两处都没有版本约束 ⇒ 同一条 pip install -e ".[dev]" 在 CI（钉 3.11）
# 与镜像 base（3.14）两面上解析出的不是同一版（PyPI 元数据现读：>=3.11 面最新 0.13.109、>=3.14 面 0.13.371，
# 隔 262 个发布），而无下界时某次重建还能一路回溯到更老的不兼容版本。本门不判该钉多少、更不加下界之外的
# 上界（那属 Q4.1 明写"对齐后再谈"的第二半），只判三条形状：无裸名依赖、在册下界在它出现的每一枚 extra
# 里同值、登记的依据文案指得到裁定与一个盘上真在的锚点。
"$PYTHON" "$REPO/scripts/check_sim_dep_floor.py" "$REPO"
simdep_rc=$?

echo
echo "══ 随附 wheel 门禁（盘上单枚 / 四个引用面同名 / 字节等于权威登记 / 指路文档不复制摘要）══"
# 第六轮审计 ARCH-04 复测（§二之九十八）：`docker/homesdk/` 那枚私有 wheel 随仓提交，报告的"缺校验和
# 清单／缺来源说明"两格已被裁定 20261007 §六 Q1 裁 A 接住（仓内随附＋文件名钉死＋权威 sha，字节真值在
# `tests/unit/test_mqtt_compose_env.py` 的常量与库侧 `dist/VERSIONS.txt`）。复测挖出的是另一条缝：那条
# 字节判据的 wheel 路径**只从 `Dockerfile.api` 的 COPY 行取**，于是 `ci.yml` 的三条 `pip install`、
# `Dockerfile.test` 的 COPY、compose 的注释提及都不在对账范围——把 CI 那一面指到目录里另一枚旧 wheel，
# 测试算的仍是交付面那一枚 ⇒ CI 装的与镜像装的脱钩而 CI 照绿。与刚收的 ARCH-03 同族：手抄的引用面各报
# 各的绿。换 wheel 走不走 DCD 不由本门拍板（它只判"当前这枚是不是那枚"），来源与构建流程写在
# `docker/homesdk/README.md`；那份文档**不许钉第三份 64 位摘要**——副本会过期，只指路。
"$PYTHON" "$REPO/scripts/check_vendored_wheel.py" "$REPO"
wheel_rc=$?

echo
echo "══ 信任边界门禁（清单逐条对撞 / 匿名面默认拒绝 / 登记可核对 / 过期登记 / 安装器接线）══"
# 第六轮审计 ARCH-05（§二之九十九）：报告说"缺一份可核对的信任边界清单，信息分散在 60 多个路由装饰器里"，
# 建议从 build_app() 的路由表生成清单打进 CI、新端点必须显式声明 scope、默认拒绝。落码按那条建议做，但
# 真源取的是**运行期路由表**而不是装饰器条数：scope 藏在 requires(scope) 返回的闭包自由变量里，静态文本
# 读不出来；更要紧的是 af_api.py 里四条匿名 POST（/api/build、/api/bind、/api/sim、/api/spec/compile）
# 声明的 dependencies=[Depends(_readonly_guard)] 是**单写者租约**、不鉴权——按"有没有守卫"读文本会把这
# 四条读成有防护，这正是安全第三轮 F-10「看起来设了权限」那一族。本门判五件事：清单与现读逐条一致（新增、
# 消失、档位变、依赖变、处理器改名都红）；落在 anon／optional／bearer-in-handler 的第一方端点必须逐条在册
# （默认拒绝）；登记里的处理器要等于现读、依据要指得到仓内真实存在的记录；已经收紧的过期登记必须删；
# src/ 里能往 app 上挂端点又不属于 af_api.py 的文件要写明入口与"未挂载"断言，一旦 AST 读到调用者即红
# （今天 af_runtime_plugins.install_api 与 af_conflict_runtime.install_api 都是裸挂载且无调用者——
# 报告只点了前一个，复测把后一个也量进射程，那份表里含 POST reset 与 DELETE unlock）。
# 匿名面**该不该**匿名不在本门射程：那是部署与安全口径，归 DCD（见 §二之九十三 已递的那件）。
"$PYTHON" "$REPO/scripts/check_trust_boundary.py" "$REPO"
boundary_rc=$?

echo
echo "══ 进程模型门禁（生命周期站点默认拒绝 / 每类上限只减不增 / serve 与 watch 收尾形状）══"
# 第六轮审计 ARCH-06（§二之百）：报告说这仓「三套互不相识的同步原语 ＋ 无 supervisor ＋ 无优雅停机」，
# 并且「没有等价物锁住『共享可变状态的归属』」。前两句有格已过期（全树 signal.signal 命中数从 0 变成 1，
# af_cli.py:631 那枚是 §二之九十三 落的 SIGTERM→既有 finally；serve 也早有 try/finally 停桥），后一句成立：
# 报告那张清点表靠读代码还原，行号已在漂（af_service.py:851/1549→:853/:1551、af_cli.py:1435→:1451），
# 还漏了 af_service.py 自己的 3 枚 FileLock。本门把三件事变成可 diff 的资产：生命周期站点（Popen／os.kill／
# signal.signal／atexit…十四种形状）逐枚必须在 docs/进程模型清单.md 写明"谁拉起·谁收·收不到会怎样"，
# 每类计数只减不增，同步原语 33 枚与 global 改写到的 9 枚共享名逐行对撞现读；外加 D/E 两枚形状锚点，
# 把 serve 停桥与 watch 收尾那两条已有的优雅路径钉住——改坏了立刻红，而不是等下一份审计再发现。
# 「子进程归谁重启」「serve 要不要信号钩子」「af_live 那三枚 global 要不要加锁」三问不自决，递 DCD（清单 §六）。
"$PYTHON" "$REPO/scripts/check_process_model.py" "$REPO"
process_rc=$?

echo
echo "══ 可观测性门禁（留痕站点逐行对撞 / 具名码默认拒绝 / 无码上限只减不增 / 欠账必须在册）══"
# 第六轮审计 ARCH-07（§二之一百零一）：报告说「只有 41% 的失败路径有日志」「无结构化错误码」
# 「无追踪关联」「日志级别混用」。现读三句都要改口：留痕站点 143 枚／28 个文件，其中带具名码的
# 只有 18 枚（约 12.6%）；trace_id 在**主题信封**侧有 6 个站点／3 个文件，HTTP 请求侧确实是 0；
# 级别混用不是主观判断而是可对撞的分布（WARNING 88 ∶ INFO 7）。本门把"留痕"变成可 diff 的资产：
# §二 四张表逐行对撞现读，§三 新码不落账即红（默认拒绝），§四 每级别无码站点只减不增，
# §五 欠账数字变了而账没改即红，D 那条把访问日志掩码 filter 的形状钉成锚点（第一期落地的，别再弄丢）。
# 分级口径此前全仓 0 处成文（关键词现读），本批写进清单 §1.6；「HTTP 侧要不要 request id」「信封
# trace_id 要不要升成契约」两问不自决，递 DCD（清单 §七）。
"$PYTHON" "$REPO/scripts/check_observability.py" "$REPO"
obs_rc=$?

echo
echo "══ 有界缓存注册表门禁（TTL 与硬上限成对、回收要有测试钉住、只写不读判死）════"
# 第六轮审计 §三 把"新增有界缓存必须同时给 TTL 与硬上限，并在测试里断言纯写不读也被回收"记成
# "约定 + 门禁可见"，但约定的两半里当时没有任何能判红的东西（只活在审计正文里）。裁定 20261004 §一 3
# 选 B（注册表式）而不是 C（统一基类）：Python 里"有界"往往长在键空间或调用方，不在容器自身，
# 天真静态口径首跑命中 76 个增长容器、真两条腿齐全的只有 2 个 ⇒ 硬扫只会得到两条永久红 + 一张豁免表。
# 所以本门不猜"有没有界"，只核对 `af_bounded_caches.py` 说没说实话：两条腿的名字要在模块里、
# 测试 id 要真被 pytest 收集、新增容器要登记或就地带理由豁免、基线只减不增。
# 第五判（判据 E，稳定性审计 §六 P1 的落地）管的是另一件事：**基线冻的是"有没有界"，冻不掉
# "这份数据根本没人读"**。按名字在全仓数读取点（Load 上下文，剔除赋值/删除目标与 `x.append(…)`
# 的方法名本身），一个未登记的容器只有写、全仓读不到 ⇒ 判红。口径故意选"全仓按名字"而不是
# "按文件/按持有者"：后者会把 `af_vhass/harness.py:269`、`af_executor.py:792` 这类跨文件合法读
# 判成假红；同名遮蔽的漏判已各钉一条测试并把两向反例交给 DCD（§二之四十七）。
"$PYTHON" "$REPO/scripts/check_bounded_caches.py" "$REPO/src/autoforge"
cache_rc=$?

echo
echo "══ IR 运行时扩展键白名单硬门（写进节点的运行时键必须在白名单里）════"
# 裁定 20261005-AF-ir_non_reversible是否升schema 判 B（明确豁免 + schema 白名单注释），并自己写明
# "B 能成立的前提"是这条断言：白名单只是注释、约束力弱于 A，所以代码侧键集合
# （`RUNTIME_ONLY_FIELDS ∪ {NON_REVERSIBLE_KEY}`）必须与 `ir.schema.json` 的 `node.$comment` 逐键相等，
# 且写入点源码里出现未登记的下划线键即判红。此前它只在 `.github/workflows/ci.yml` 有一步，
# `gates.sh` 里没有 ⇒ 本机跑 `gates.sh` 得到绿、CI 得到红，正是本仓反复登记的"该红的不红"一族。
"$PYTHON" "$REPO/scripts/check_ir_runtime_keys.py"
ir_keys_rc=$?

echo
echo "══ 门禁装配覆盖门（\`check_*.py\` 必须真被某条链跑到，远端有的本机也得有）══"
# §二之四十六 盘出：裁定 20261005 要求的那条 IR 运行时键硬门当时只写在 ci.yml 的 quality-gates 作业里
# （`bash gates.sh` 的下一步、前置一模一样），`gates.sh` 里没有 ⇒ 本机绿、远端红。"装配不对称"这件事
# 本身没有任何东西会判红：脚本在盘上、CI 在跑、作业是绿的，只有本机开发者看不见它。同族的先例是
# 包标记门（为一次 .gitignore 事故立的复发门）。本门同时拦"写了没接"（盘上有、谁都不跑）和
# "豁免过期"（豁免表里的脚本工作流已经不引用）。
"$PYTHON" "$REPO/scripts/check_gates_coverage.py"
coverage_rc=$?

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
  echo "结论：包标记门禁红（exit=$pkg_rc）。1=有包目录的 __init__.py 没入库，CI 上 grimp 不递归、架构门禁比本机少分析模块；2=拿不到 git 索引，**或**索引读得出却一个 \`*.py\` 都没有（射程塌了——那不是『都入库了』，是本门无从判定）。本链**不带** \`--allow-degraded\`：那条磁盘口径（审计 §六 P3 要的无 git 降级路径）只在显式要它的沙箱里生效，且读数自称 DEGRADED / 索引半边未验，绝不当本门的绿。"
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

if [ $atomic_rc -eq 2 ]; then
  echo "结论：原子写站点门禁读不出射程（exit=$atomic_rc）。三种情形：扫描目录不存在、某个 .py 解析失败、\`src/\` 下一个 \`os.replace\` 站点都没扫到。三者都让本门从『判定形状』退化成『没有发现』——报『干净』就是假绿，先把锚点口径对上真实代码。"
  exit $atomic_rc
fi
if [ $atomic_rc -ne 0 ]; then
  echo "结论：原子写站点门禁红（exit=$atomic_rc）。新增的 \`os.replace\` 站点要么走 \`af_atomic.atomic_write_text\`（随机 tmp 名 + fsync，P1-18 那条已经修过的路），要么进 \`.atomic-write-baseline.txt\` 并逐条写理由：固定名 \`x.tmp\` 遇上第二个写者就是互相截断，而 \`PersistStore\` 的租约设计**明确允许**两个进程先后驱动同一条实例——截断后的记录校验和不过，读侧直接跳过，等于那条实例静默消失。基线只减不增；确实要留这一站就地写 \`# fixed-tmp: exempt(理由)\`（理由为空也判红）。"
  exit $atomic_rc
fi

if [ $outbound_rc -eq 2 ]; then
  echo "结论：出站护栏门禁读不出射程（exit=$outbound_rc）。三种情形：扫描目录不存在、某个 .py 解析失败、\`src/\` 下裸 urlopen / guarded_open / build_opener 三类站点一个都没扫到。最后一种不是干净——那说明出站整体换了库或收口点改了名，本门已经盯不住任何东西，报『干净』就是假绿。"
  exit $outbound_rc
fi
if [ $outbound_rc -ne 0 ]; then
  echo "结论：出站护栏门禁红（exit=$outbound_rc）。三种形状：① 直连 \`urlopen\`（含 bandit 认不出的 \`(opener or urllib.request.urlopen)(…)\` 与把 \`urlopen\` 当值交出的赋值）——默认 opener 跟 3xx 且不对 Location 重校验，带凭据那一处会把凭据转到白名单外；改走 \`af_adapters.http.guarded_open(req, allowed_hosts=(host_of(自己的 base_url),), timeout=…)\`。② 新建 \`build_opener()\` 却不挂 \`_WhitelistRedirector\`/\`_NoRedirectHandler\`。③ 调 \`guarded_open\` 没传 \`allowed_hosts=\`。确实要留一站就地写 \`# outbound-guard: exempt(理由)\`（理由为空也判红）。"
  exit $outbound_rc
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

if [ $interp_rc -eq 2 ]; then
  echo "结论：CI 解释器口径门读不出射程（exit=$interp_rc）。六种形状：\`.github/workflows/ci.yml\` 读不到、\`jobs:\` 段里数不出任何作业、整份文件数不出一个 \`python-version:\` 钉值（改用矩阵或容器镜像就同步改本门口径，别让它静默全绿）、\`pyproject.toml\` 里没有 \`requires-python\` 或它的写法本门比较器认不出、两份 Dockerfile 任一处读不出 \`FROM python:\` base、\`README.md\` 读不出「### 解释器口径」那一节的任何三列数据行（口径表被删或改了形状）。都是射程塌了，此刻本门无从判定，报『干净』没有依据。"
  exit $interp_rc
fi
if [ $interp_rc -ne 0 ]; then
  echo "结论：CI 解释器口径门红（exit=$interp_rc）。三条各自可红：① 手抄不一致——ci.yml 各 Python 作业的 \`python-version:\` 钉值必须彼此相等（现仓四枚手抄），改一处忘三处时另外三个作业继续跑旧口径、每个作业各报各的绿；② 包声明不允许——钉值必须落在 \`requires-python\` 的允许区间内，把下限抬到 3.12／3.14 而钉值不动，CI 就是在一条包声明已不承认的环境上验交付；③ 漂移无人认领——钉值与镜像 base 不一致时必须在 \`INTERPRETER_DRIFT\` 里挂一格，理由要同时点到一个**本体带钉值**的作业和一个盘上真实存在的路径（\`路径:行号\` 的那一行也要真在），已经对齐了还挂着＝豁免过期，同样红；④ 口径表与真源脱钩——README 那张口径表的每一格必须等于它自己点名的那份真源现读值（改 \`pyproject.toml\`／\`ci.yml\`／任一份 Dockerfile 而忘改表，或把表里某一格整格删掉，都是红）。对齐成哪一个口径不由本门拍板：那是交付／验证口径，走裁定（第六轮审计 ARCH-03 ＋ 裁定 20261011 §3 Q4.1）。"
  exit $interp_rc
fi

if [ $simdep_rc -eq 2 ]; then
  echo "结论：依赖下界门读不出射程（exit=$simdep_rc）。四种形状：\`pyproject.toml\` 不在盘上或 \`tomllib\` 解析失败、没有 \`[project]\` 表、没有 \`[project.optional-dependencies]\`（extra 那一族整族不在了就该同步改本门口径）、或在册项点名的 extra 名字在那张表里读不出来。都是射程塌了，此刻本门无从判定，报『干净』没有依据。"
  exit $simdep_rc
fi
if [ $simdep_rc -ne 0 ]; then
  echo "结论：依赖下界门红（exit=$simdep_rc）。三条各自可红：① 裸名依赖——\`[project].dependencies\` 与每一枚 extra 里的每一条都要写成 \`名字>=X.Y\`，裸名就是"每次重建拿哪一版看运气"，而 CI 与镜像两面会各自解析出不同的一套；② 在册下界不符——\`SIM_DEP_FLOORS\` 登记的包在它出现的**每一枚** extra 里的 \`>=\` 都要逐字符等于登记值（同包两枚手抄改一枚忘一枚就是两套环境），整枚从 extra 里消失同样红（在册项不许静默蒸发）；③ 依据不合格——登记文案要非空、含「裁定」、并点到至少一个盘上真实存在的锚点。钉成多少、要不要再加下界之外的上界都不由本门拍板：上界会替交付面选环境，属裁定 20261011 §3 Q4.1 留到"对齐后再谈"的第二半。"
  exit $simdep_rc
fi

if [ $wheel_rc -eq 2 ]; then
  echo "结论：随附 wheel 门禁读不出射程（exit=$wheel_rc）。六种形状：\`docker/homesdk/\` 不在盘上、目录里一枚 \`.whl\` 都没有（没有真身可对账）、\`tests/unit/test_mqtt_compose_env.py\` 读不到或那行 \`AUTHORITATIVE_WHEEL_SHA256\` 不再是 64 位十六进制、\`ci.yml\`／\`Dockerfile.api\`／\`Dockerfile.test\` 任一必读引用面不在盘上，或其中读不到任何 \`homesdk-*-py3-none-any.whl\` 引用（安装面换了形状，本门没有口径来源）。都是射程塌了，此刻本门无从判定，报『干净』就是假绿——wheel 若真被搬走、或改成从制品库拉取，要**当场决定本门去留**并同步改口径，不能让它沉默全绿。"
  exit $wheel_rc
fi
if [ $wheel_rc -ne 0 ]; then
  echo "结论：随附 wheel 门禁红（exit=$wheel_rc）。四条各自可红：① A 目录多枚——\`docker/homesdk/\` 只许留一枚，多一枚就是「CI 挑那一枚、镜像挑这一枚」的物理前提，而两条链各自报绿；② B 引用面不同名——\`ci.yml\` 的三条 \`pip install\`、两份 Dockerfile 的 \`COPY\`＋\`pip install\`、compose 注释里出现的文件名必须全等于盘上那一枚；真源从盘上现取，门里没有第二份名单可抄；③ C 字节不符——盘上那枚的实算 sha256 要等于 \`tests/unit/test_mqtt_compose_env.py\` 里登记的权威值，同名不同字节就是「写着 0.3.x」与「跑的真是那一枚」脱钩，而改那一行只有一条路：新的 DCD 裁定；④ D 指路文档——\`docker/homesdk/README.md\` 要在册、要指得到两处真源，里面不许钉第三份 64 位摘要（会过期的副本），也不许提到目录里不存在的 wheel 名。"
  exit $wheel_rc
fi

if [ $boundary_rc -eq 2 ]; then
  echo "结论：信任边界门禁读不出射程（exit=$boundary_rc）。八种形状：① \`docs/信任边界清单.md\` 不在盘上——清单本身就是本门的产物；② 自动段的两枚标记被改写（表不在，对撞没有对象）；③④ 「非鉴权面登记」／「未挂载安装器登记」任一节缺失；⑤ \`import autoforge.af_api\`、\`build_app()\` 失败或读出 **0 条** 路由（装配换了形状）；⑥ 某条 APIRoute 读不出 \`dependant\`；⑦⑧ 两节里**整节**一条都解析不出（登记行形状变了），或 \`src/autoforge\` 里读不出任何挂端点的机制而安装器登记同时为空。都是射程塌了，此刻本门无从判定，报『干净』就是假绿——清单排版、装配形状、登记行写法任一变了，要**当场改口径**并重生成自动段（\`--write\`），不能让它沉默全绿。"
  exit $boundary_rc
fi
if [ $boundary_rc -ne 0 ]; then
  echo "结论：信任边界门禁红（exit=$boundary_rc）。五条各自可红：① A 清单漂移——自动段逐行对撞现读路由表，档位／鉴权依赖／处理器任一变了、清单多出已消失的行、现读多出没写的行都红（真源是 \`build_app()\` 的运行期路由表，不是 \`grep -c\` 的装饰器条数：条数把条件注册算成无条件，scope 藏在 \`requires(scope)\` 返回闭包的自由变量里，静态文本读不出来）；② B 默认拒绝——落在 \`anon\`／\`optional\`／\`bearer-in-handler\` 三档的第一方端点必须逐条在册，新匿名面不登记就红；③ C 登记不可核对——处理器要与现读一致（路径还在但里面换人了）、依据必须是仓内真实存在的文件、理由不许空／过短／写成占位词；④ D 只减不增——登记里那条已不在非鉴权档＝过期登记，没人核就等于没登记；⑤ E 安装器接线——有挂端点机制的文件必须在册，断言只认「未挂载／已挂载」两种形状，写「未挂载」却现读到调用者就是**接线已经发生、今天就是漏**（判调用读 AST 的 \`Call\` 节点，注释里出现名字不算——本批真修过这一格）。本门判『差有没有被认领』，不判匿名面该不该匿名、零凭据令牌该不该收窄：那半在 DCD。"
  exit $boundary_rc
fi

if [ $process_rc -eq 2 ]; then
  echo "结论：进程模型门禁读不出射程（exit=$process_rc）。九种形状：① \`src/autoforge/\` 整个目录不在盘上；② \`src/autoforge/af_cli.py\` 不在（D／E 两条收尾形状没有判定对象）；③ \`docs/进程模型清单.md\` 不在——清单本身就是本门的产物；④ 自动段两枚标记被改写；⑤⑥ 「生命周期站点登记」／「每类上限」任一节标题缺失；⑦ 树里有文件 \`ast.parse\` 不了；⑧ **全树读不出任何一枚生命周期站点**（连 \`os.kill\` 都没有＝扫描器坏了，不是代码干净）；⑨ 登记节或上限节整节一条都解析不出（行形状变了）。都是射程塌了，此刻本门无从判定，报『干净』就是假绿——清单排版、目录结构、登记行写法任一变了要**当场改口径**并重生成自动段（\`--write\`），不能让它沉默全绿。"
  exit $process_rc
fi
if [ $process_rc -ne 0 ]; then
  echo "结论：进程模型门禁红（exit=$process_rc）。五条各自可红：① A 自动段漂移——生命周期站点／同步原语站点／\`global\` 改写到的模块级共享名这三张表逐行对撞现读，计数行也在内，所以计数下降同样逼人回来重生成并下调上限（真源是 \`src/autoforge/\` 整棵的 AST，不是 \`grep -c\`：注释里出现 \`install_api\` 那种名字曾被散文踩过，本门只看 \`Call\` 节点）；② B 默认拒绝——新起的一枚 \`Popen\`、多发的一枚 \`os.kill\`、新装的信号或 \`atexit\` 钩子，必须先在清单 §三 写明「谁拉起·谁收·收不到会怎样」并指得到仓内记录，写「待补」即红，认领只认 AF／待裁／DCD 三种形状；过期登记（那一行的路径或行号已经读不出站点）必须删；③ C 每类上限只减不增——现读超过 §四 即红，先认领再抬上限；④ D serve 收尾形状——\`uvicorn.run\` 所在的 \`try\` 其 \`finally\` 必须还在停桥；⑤ E watch 收尾形状——装 SIGTERM 处理器的那个函数里必须有一个 \`finally\` 同时做 \`stop.set()\`／\`ticker.join(\`／\`coord.release()\`。D／E 是 §二之九十三 已经落地的两条优雅收尾路径的形状锚点：报告当时说『整个运行时没有停机钩子这个概念』，这两格把它从『某次改动的巧合』变成门。本门判『差有没有被认领、形状有没有被保住』，不判子进程归谁重启、serve 该不该补信号钩子、\`af_live\` 那三枚 \`global\` 要不要加锁：那三问在 DCD（清单 §六）。"
  exit $process_rc
fi

if [ $obs_rc -eq 2 ]; then
  echo "结论：可观测性门禁读不出射程（exit=$obs_rc）。六种形状：① \`src/autoforge/\` 不在盘上；② \`docs/可观测性清单.md\` 不在——清单本身就是本门的产物；③ 自动段标记被改写或 §三／§四／§五 任一节缺失；④ 树里有文件 \`ast.parse\` 不了；⑤ **全树读不出任何一枚留痕站点**（连 \`logger.debug\` 都没有＝扫描器坏了，不是代码干净）；⑥ 关联 id 面两侧同零（信封 \`trace_id\` 与 HTTP \`request_id\` 都读不出）——追踪这一格整面消失时，§2.3 的表会空着绿过去，那是假绿。都是射程塌了，此刻本门无从判定。"
  exit $obs_rc
fi
if [ $obs_rc -ne 0 ]; then
  echo "结论：可观测性门禁红（exit=$obs_rc）。五条各自可红：① A 自动段漂移——站点×级别分布、具名码站点、信封 trace_id、\`af_api.py\` 的 HTTP 面读数这四张表逐行对撞现读（真源是 \`src/autoforge/**/*.py\` 整棵的 AST，只认 \`Call\` 节点：注释与 docstring 里出现 \`logger.warning\` 不算站点）；② B 具名码默认拒绝——新码必须先在清单 §三 写明 level／级别语义／介入／依据／理由／认领，level 与现读级别不符即红（码是身份、级别是严重度，两者都要对上），依据指不到仓内文件即红，理由写成「略／待补」即红；③ C 每级别无码站点上限只减不增——现读超过 §四 即红，新增留痕要么带码要么先认领；④ D 访问日志掩码锚点——\`_ACCESS_LOG\` 模块级常量、\`install_access_log_token_mask\` 函数、以及 \`uvicorn.run\` 同函数内的那次安装调用，三处任一被摘掉就红（第一期那条凭据进访问日志的修复不许悄悄丢）；⑤ E 欠账必须在册——§五 那四格读数与现读对撞，数字变了而账没改即红。本门判『差有没有被认领、账有没有对得上』，不判 HTTP 侧要不要 request id、信封 trace_id 要不要升成对外契约、\`af_api.py\` 一行日志都不写这件事要不要现在补：那几问在 DCD（清单 §七）。"
  exit $obs_rc
fi

if [ $cache_rc -eq 2 ]; then
  echo "结论：有界缓存注册表门禁读不出（exit=$cache_rc）。五种情形：\`src/autoforge/af_bounded_caches.py\` 不在预期位置或两张表不再是纯字面量字典、注册指向的测试文件 pytest 收集失败、扫描器在 \`src/autoforge\` 下读到 0 个增长容器、判据 E 的读取点收集器数出 0 个读取点、未登记容器**全部**被判成死写（≥3 个）——另有收集器或判据直接抛异常两种，同样退 2。后两种是判据自身塌了：整棵树一个读取点都数不到、或所有容器同时'没人读'，都不是代码的问题，是口径断了——此刻本门无从判定，报『干净』就是假绿。"
  exit $cache_rc
fi
if [ $cache_rc -ne 0 ]; then
  echo "结论：有界缓存注册表门禁红（exit=$cache_rc）。注册表说的那条腿必须真在模块里（\`cap\`/\`ttl\`/\`trim\` 逐个核对）、指向的测试必须真被收集、新增增长容器必须进 \`BOUNDED_CACHES\`（两条腿 + 一条『纯写不读也被回收』的测试）或在那一行写 \`# bounded-cache: exempt(理由)\`；基线名单只减不增。判据 E 还会单独判红一种形状：容器只有写入点、**全仓读不到这个名字**（基线也冻不住它——基线认的是'有没有界'，不是'有没有人读'），处置四选一：真去读它 / 加封顶与裁剪 / 进注册表 / 就地写豁免理由。稳定性审计 BUG-01 删掉的就是这一族。约定来自第六轮审计 §三，落法来自裁定 20261004 §一 3 B，E 来自同一份审计 §六 P1。"
  exit $cache_rc
fi

if [ $ir_keys_rc -ne 0 ]; then
  echo "结论：IR 运行时扩展键门禁红（exit=$ir_keys_rc）。写进 IR 节点的运行时键必须同时在 \`ir.schema.json\` 的白名单注释与 \`af_irreversible.RUNTIME_ONLY_FIELDS\`（或 \`NON_REVERSIBLE_KEY\`）里：这五个键是铁律 #1 的**明确豁免面**，豁免范围由裁定 20261005 §三 逐条写死，白名单之外再加一个运行时键就是绕过铁律 #1——先申请裁定，不要就地加键。反向漂移（白名单列了代码没声明的）同样判红，因为它把豁免面虚报得比实际宽；锚点读不出（schema 注释不在、\`af_irreversible\` 导入失败）也走这一条，脚本此刻是抛异常退出而不是报『干净』。"
  exit $ir_keys_rc
fi

if [ $coverage_rc -eq 2 ]; then
  echo "结论：门禁装配覆盖门读不出（exit=$coverage_rc）。\`gates.sh\` 或 \`.github/workflows/\` 不在预期位置、\`scripts/\` 下一个 \`check_*.py\` 都没数到、\`gates.sh\` 里一个引用都没有、某处引用了盘上不存在的脚本（那是**死步骤**：那一行看着像在判，其实什么都没判）、工作流里数不出**任何一个 job**（判据⑤ 的两个锚点全从 YAML 现取，那一刻它没有射程）、或 \`gates.sh\` 里 \`echo \"…\"\` 那族行数掉到下限以下（判据 ⑥ 的口径已经不成立——文案改成 printf／heredoc／变量了，那一刻『没有未转义反引号』只是空集给的干净）。六种都是射程塌了，报『干净』没有依据。"
  exit $coverage_rc
fi
if [ $coverage_rc -ne 0 ]; then
  echo "结论：门禁装配覆盖门红（exit=$coverage_rc）。五种形状：① 盘上有某个 \`check_*.py\` 而 \`gates.sh\` 与任何工作流都不跑它（写了没接＝看起来像一道门，其实没人按）；② 工作流引用了它而 \`gates.sh\` 没有且不在 \`CI_ONLY_EXEMPT\`（远端响、本机不响——本机开发者拿的是半条链，正是 §二之四十五 撞到的那件）；③ 豁免表里那一格已经过期（工作流不再引用它）或理由为空；④ 豁免理由的两个锚点核对不住——反引号点名的作业**本体没在引用这个脚本**（『跑在别的作业里』这种空话、拉一个不相干的真作业当掩护都过不了），或没有给一个**盘上真实存在**的路径说清前置差落在哪（锚点是编的同样判红：本批就抓到一条已提交的理由引用了一个盘上从来没有过的文件名）；⑤ \`echo \"…\"\` 的文案里有**未转义反引号**——bash 会把反引号中间那段当命令替换**真的执行一遍**，跑不出来就替换成空串：开发者读到的是作者没写的那句话，而**退出码照旧对**，所以这一族不收成判据就会一直藏在『绿』里（§二之五十二 第一条猎物是本门自己接线时写的一句标题）。把门接进 \`gates.sh\`，或给豁免那一格写清**前置为什么不同**——作业名（job id 或 \`name:\` 显示名，两者同源）+ 一个盘上真在的路径，两个都要给；文案里的反引号必须逐个转义（前面加一个反斜杠）。"
  exit $coverage_rc
fi

if [ $ast_rc -eq 2 ]; then
  echo "结论：AST 门禁**没崩在结论上，是崩在了判定路上**（exit=2）。依赖门禁退出码 1 有两种完全不同的成因：判出违规，或它自己在某个文件上抛了栈。分类器在输出里抓到崩溃签名就是后者——那一刻 \`homesdk.gates\` 一个计数都没产出。此时**两件事都不许做**：① 按『真违规』去 \`--update-baseline\` 或往 \`.gates-baseline.txt\` 追加指纹（崩掉的门没有指纹可对齐，这一按下去留下的是『它绿了』）；② 把那个文件加进忽略名单绕开。缺陷在依赖里（\`homesdk/gates/scan.py\` 的 \`_numeric_literal\`/\`_dotted\`/\`_literal_secret\` 三个自递归函数无深度预算，第十五轮 F16；0.3.1 与 0.3.2 实测同样 RC=1），修它要库侧动刀 ⇒ 交 DCD。"
  exit $ast_rc
fi
if [ $ast_rc -ne 0 ]; then
  echo "结论：AST 门禁红（exit=$ast_rc）。修，或在 .gates-baseline.txt 里逐条写明放行理由。"
  exit $ast_rc
fi
if [ $plan_claims_rc -eq 2 ]; then
  echo "结论：计划表口径门读不出（exit=$plan_claims_rc）。四种形状：① \`docs/plan/开发计划_WebUI全功能接入.md\` 不在盘上；② 表里数不出任何一条完整写出的 \`VERB /api/…\`（列序或写法变了，本门没有口径来源）；③ 数不出任何一条 \`✅\` 领头的声明——『干净』成了空集给的干净；④ 上面那条 UI↔路由门自己的射程是 0，或它有解析不出的调用点（\`unparsed\`）：认领集合不完整时判『✅ 却没人调』会误伤真接了的面板，所以这一步宁可红着也不下结论。四种都是射程塌了。"
  exit $plan_claims_rc
fi
if [ $plan_claims_rc -ne 0 ]; then
  echo "结论：计划表口径门红（exit=$plan_claims_rc）。两条判据：判据 A＝文档某行状态栏以 \`✅\` 领头，UI↔路由门却在三棵第一方 UI 树里读不出它的调用点（文档说面板接了、代码里没人调）；判据 B＝文档完整写出的一条 \`VERB /api/…\` 在服务端路由表里落不到（改名、删路由、或把 \`/health\` 与 \`/api/health\` 写串）。修法都是**改文档**：面板真没接就写 \`后端 ✅ ／ UI ✗\` 的双段写法，路由改名就把那一行的路径改对——不要为了让本门绿去补一个假调用点，也不要往 UI↔ 门的名单里加豁免。"
  exit $plan_claims_rc
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
