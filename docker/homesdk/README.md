# `docker/homesdk/` — 仓内随附的私有 wheel（来源、装法、更换流程）

本目录那份 `homesdk-*-py3-none-any.whl` **是交付物，不是垃圾文件**。`.gitignore` 有一条全局 `*.whl`
会把它挡掉，所以专门为这个目录开了白名单 `!docker/homesdk/*.whl`（`.gitignore:64`）。

## 为什么在仓里

`homesdk` 没有上 PyPI（现读 `https://pypi.org/pypi/homesdk/json` 与 `…/homesdk-python/json` 都是 **HTTP 404**），
而 AF 的联动面硬依赖它：`pyproject.toml:56-61` 的注释写明"同意/否决分类（G4 canary auto-rollback 的伦理闸门）
依赖 homesdk"，`af_mqtt_bridge` / `af_executor` / `af_runtime` 都要 import 它。所以交付链按
**"仓内随附＋文件名钉死"** 走——这一档是裁定定的，不是顺手为之：
`decisions/20261007-MA五件与AF一件-裁定.md` §六 Q1 裁 **A（仓内先换 + pin，镜像重烤搭变更窗）**，
并明写"改那个权威值只有一条路：新的 DCD 裁定"。

## 现在是哪一枚、字节真值在哪

- **哪一枚**：以盘上这一目录里唯一的 `.whl` 为准（`ls docker/homesdk/`）。版本名同时出现在下面所有引用面，
  一致性由 `scripts/check_vendored_wheel.py` 每批判。
- **字节真值有两处，本文件不复制第三处**（钉一份摘要就多一份会过期的副本）：
  1. AF 侧：`tests/unit/test_mqtt_compose_env.py` 里的 `AUTHORITATIVE_WHEEL_SHA256` 常量——
     同一文件的判据会实算本目录那枚的 sha256 并与它对撞（"写着 0.3.x"与"跑的真是那一枚"是分开的两件事）；
  2. 库侧：`E:\NAS\homesdk\dist\VERSIONS.txt` 的对应版本段（PM 维护、**只增不删**），
     0.3.2 那一段自述由 DCD 构建，含 sha256、字节数与"该不该用"的用途说明。

## 来源与可再生性（这一枚是怎么来的）

- **源码可比对**：库仓在同一台机器的 `E:\NAS\homesdk`（`pyproject.toml:8` 现读 `version = "0.3.2"`，
  构建后端 `pyproject.toml:2-3` 是 `setuptools>=68` / `setuptools.build_meta`）。要审"闸门逻辑"就读
  `src/homesdk/consent.py`，不必靠二进制猜。
- **构建命令有登记**：库侧 `dist/VERSIONS.txt:42` 写明该段产物由
  `python -m pip wheel --no-build-isolation --no-deps -w dist .` 产出；0.3.2 那一段（`:28`）自述
  **DCD 构建，2026-10-07**。AF 侧不自行重烤，也不"顺手构建一枚同名文件放进去"。
- **字节不可逐字节复现**（这一条是实测，不是推测）：那枚 wheel 的 22 个 zip 条目带着 **17 个不同的
  `date_time`**（范围 2026-09-18 → 2026-10-06，就是各源文件自己的 mtime），所以"重新构建一次看看摘要
  对不对得上"这种做法**天然对不上**——摘要只能靠**登记值对撞**（见下一节两处真源），不能靠重烤。
  这也正是本门为什么把"改权威值只有一条路：新的 DCD 裁定"写在判据里。

## 谁会装它（引用面清单）

| 面 | 位置 | 装法 |
|---|---|---|
| CI 面（工作流） | `.github/workflows/ci.yml:25`、`:42`、`:63` | `pip install docker/homesdk/<那一枚>` |
| 交付面（服务镜像） | `docker/Dockerfile.api:26-27` | `COPY` 到镜像里再 `pip install` 那一条路径 |
| CI 面（测试镜像） | `docker/Dockerfile.test:26-27` | 同上 |
| 部署编排 | `docker/docker-compose.api.yml:56`（注释提及） | 说明镜像按文件名钉死装它 |

四条面**必须指向同一枚**。上面那张表由 `scripts/check_vendored_wheel.py` 对撞：本目录只许一枚 wheel、
全部引用面的文件名等于盘上真身、真身的实算 sha256 等于 AF 侧那处权威登记、本文件在册且指得到两处真源。

## 本地开发怎么装（以及"装不上"会长什么样）

```bash
# 正确：直接装仓内那枚（路径写目录，版本换了也不用改命令）
pip install docker/homesdk/homesdk-0.3.2-py3-none-any.whl

# 不要用这条：.[homesdk] 这枚 extra 声明的是 homesdk>=0.3.2，而 PyPI 上没有这个包
pip install -e ".[homesdk]"
# 它的失败形状是 pip 报 "No matching distribution found for homesdk>=0.3.2"——
# 那枚 extra 只是"这块能力归属哪个包"的声明面，不是可安装的入口。
```

CI 与两份镜像装的都是 `.[dev]` / `.[api,ha,mqtt]`，这些 extra **都不含 homesdk**（所以不会去 PyPI 抓它、
也不会因为抓不到而红），homesdk 一律由上面那行裸 wheel 安装提供——这一点另有同族门
`scripts/check_mqtt_runtime_dep.py` 在判（它判的是 paho 的声明面/交付面/CI 面三面一致）。

**装不上长什么样**（现读实测，不是设想）：把 `site-packages` 关掉再导入包本体——

```bash
"C:/Users/lidicn/AppData/Local/Programs/Python/Python313/python.exe" -I -S -c \
  "import sys; sys.path.insert(0, 'E:/NAS/AutoForge/src'); import autoforge.af_executor"
# ModuleNotFoundError: No module named 'homesdk'
```

因为 `src/autoforge/af_executor.py:95` 是**模块级** import（`from homesdk.consent import YES, classify_answer`），
缺包就是在导入链上抛 `ModuleNotFoundError`，serve 起不来——**不是**静默降级、也不是"同意闸门读成默认放行"。
所以这里不需要再造一个"统一入口"脚本来报错：报错的地点就是那条依赖本身。真正会静默的是**另一件事**——
paho 缺了桥不起（`scripts/check_mqtt_runtime_dep.py`）、以及配置没配好时 `AUTOFORGE_MQTT=1` 抛
`MqttUnavailable` 拒绝启动，那一族已有自己的门与预检（`paho_available()` / `broker_settings()`）。

## 换 wheel 的步骤（0.3.2 → 0.3.3 时）

1. 先有裁定（新增 DCD 裁定，或按既有裁定的窗口指示走）；本仓已排的那一格是任务 #80（0.3.3 发版窗）。
2. 换本目录：放进新那一枚、**删掉旧的那枚**（门不许目录里同时有两枚——那是"CI 挑旧镜像挑新"的物理前提）。
3. 改五处引用：`.github/workflows/ci.yml` ×3、`docker/Dockerfile.api:26-27`、`docker/Dockerfile.test:26-27`；
   顺手核对 `docker/docker-compose.api.yml` 的注释与 `pyproject.toml:61` 的下限（`homesdk>=0.3.x`）。
4. 改 AF 侧权威摘要：`tests/unit/test_mqtt_compose_env.py` 的 `AUTHORITATIVE_WHEEL_SHA256` 换成新裁定的值
   （值本身从库侧 `dist/VERSIONS.txt` 取，别自己算完就当权威）。
5. `bash gates.sh` 全绿后再提交；镜像重烤走 NAS 变更窗，不在仓内射程。

—— 记账出处：执行记录 §二之九十八（第六轮审计 ARCH-04 的复测与分派）。
