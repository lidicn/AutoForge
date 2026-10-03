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
