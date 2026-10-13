#!/usr/bin/env python3
r"""「当下跟踪面⛔ 含凭据原文」的判据腿（裁定 20261013《凭据泄露处置路径不重写历史与甲落地路线》§二 动作②）。

被收口的形状：本仓两份 `docs/handoff/` 交接文档里躺着 **3 枚真值／6 处**明文 `svc_…` 令牌（链主 19:0x
按仓拆分现读，本席 20261014 现读复核同数：HEAD 面 6 处、工作树面 6 处、`git ls-files` 两面同形）。
裁定 §〇 把「⛔ 历史重写」钉死 ⇒ **这一族欠的是「清当下跟踪面」，处置的正解是轮换（归出资人）**：
本文件只保证「跟踪面不再供新载体」，⛔ 它等于泄露已消除。

四条腿各自单独可红：

1. **A 面（真仓）**：工作树面与**入库面（索引）**各扫一遍，`svc_` 值形状命中数必须为 **0**。
   ★ 同腿带**射程下限**：`git ls-files` 读出的文件数低于下限 ⇒ 直接红——⛔ 用"没扫到"冒充"没问题"
   （名单取法改名、整棵树被换走、`-z` 口径变了都会读到"零站点"）。
2. **B 面（反载体失明）**：把修前形状种进一枚临时 git 仓，**两条载体都必须抓到**——python 尺抓 A 面、
   `git grep --cached` 抓入库面。⛔ 只认真仓的尺永远绿：这一枚保证那条"0 命中"是量出来的而⛔ 是瞎出来的。
3. **C 面（⛔ 误伤占位符）**：`${MQTT_PASSWORD:-}`（裁定 §三 第 3 行**要求**的形状）、`svc_xxx`（短写）、
   `<在此粘贴>` 都必须⛔ 判红——否则这道闸会逼着人把注入位占位符也涂掉，那是另一种坏。
4. **D 面（自我射程）**：本文件自己是含"凭据形状"最多的地方（正控），所以**运行期拼**出来，源码里
   ⛔ 落一枚 ≥24 位的 `svc_` 字面。这条腿同时是"别把正控当泄露"的对撞。

★ **射程边界（下游逐字转抄前必读，转抄会过期）**：这把尺**只**认 `svc_` 那一族值形状。若将来漏的是别的
形状（非 `svc_` 前缀的高熵串、`Bearer` 串、PEM），这把尺会绿而⛔ 等于"没有泄露"。通用防泄露闸的真源在
homesdk（同一裁定 §二 动作③ 与 §43 的"单一真源、⛔ 四仓各存手工副本"），本席现读：AF `.git/hooks` 只有
`post-checkout`／`post-commit`（**零道**防泄露闸），而 `E:\NAS\homesdk\scripts\git-hooks` **不存在** ⇒
AF 半边挂在 homesdk 真源未建，⛔ 由本席自建第二份副本（那是裁定明令要消灭的形态）。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: 尺的唯一真源：AF 名下那三枚真值现读都是这个形状（`svc_` 前缀，长度 47）。
CREDENTIAL_VALUE_SHAPE = rb"svc_[A-Za-z0-9_+/-]{24,}"

#: 射程下限（⛔ 假期望：这不是"命中数期望"，只防整块扫空）。取法＝`git ls-files -z | tr -d '\0' | wc -l`
#: 那一条的现读数；落笔时现读 **819**，而 `docs/audit/` 那族在途删除会让磁盘面少 8 枚 ⇒ 下限留到 700。
#: 真数低于 700 只可能是名单本身换掉了（载体漂移），那时"零违规"就⛔ 可信。
MIN_FILE_FLOOR = 700


def _tracked_names() -> list[bytes]:
    """入库名单（索引面）：用 `-z` ⛔ 走 quoting——非 ASCII 路径默认会被 git 转成八进制转义串，
    把它当路径拼出来就是**静默漏扫**（本席第一遍真就这么漏掉过一枚中文文件名，靠复测才发现）。"""
    raw = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True
    ).stdout
    if not raw:
        raise AssertionError("射程塌：`git ls-files -z` 读出空名单——不在 git 树里？尺子无对象")
    return [name for name in raw.split(b"\0") if name]


def _scan_tree(names: list[bytes]) -> tuple[int, list[tuple[str, int]]]:
    """磁盘面逐枚扫（缺文件的登记进 `present` 计数而⛔ 计违规）。"""
    hits: list[tuple[str, int]] = []
    for raw_name in names:
        name = raw_name.decode("utf-8", "surrogateescape")
        path = REPO / name
        if not path.is_file():
            continue  # 已跟踪但磁盘无件（在途删除族）：由 B/入库面那条腿覆盖
        data = path.read_bytes()
        for lineno, line in enumerate(data.split(b"\n"), 1):
            if re.search(CREDENTIAL_VALUE_SHAPE, line):
                hits.append((name, lineno))
    return len(names), hits


def _grep_index() -> list[str]:
    """入库面载体（真仓）：`git grep --cached` 一条进程吃整棵索引（⛔ 依赖磁盘面）。"""
    return _grep_shape(REPO)


def _grep_shape(repo: Path) -> list[str]:
    """同一把尺的第二个载体，可对任意仓跑（合成树那条反空集腿要用它）。

    退出码两档⛔ 混：`0`＝有命中，`1`＝零命中（合法绿），其余＝载体自己塌了 ⇒ 抛。

    ★ **返回值只留 `路径:行号`，行文本就地丢掉**：这条腿一旦判红，pytest 会把返回值整份打进输出面，
      而那里躺着的正是凭据原文——量具的 stdout 也是出境面（本席第一版就漏过一次，红消息里
      整枚 `svc_…` 原文被原样贴出）。⇒ ⛔ 拿 `git grep` 的原始行当断言对象。
    """
    proc = subprocess.run(
        ["git", "grep", "--cached", "-I", "-n", "-E", CREDENTIAL_VALUE_SHAPE.decode("ascii")],
        cwd=repo,
        capture_output=True,
    )
    if proc.returncode not in (0, 1):
        raise AssertionError(
            f"射程塌：`git grep --cached` 退出码 {proc.returncode}（0/1 以外都⛔ 读成『干净』）"
            f"；stderr={proc.stderr.decode('utf-8', 'replace')[:200]!r}"
        )
    masked: list[str] = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        masked.append(":".join(line.split(":", 2)[:2]))  # path:lineno，第三段（行文本）丢弃
    return masked


def _plant_value() -> bytes:
    """正控在运行期拼（D 面：源码里⛔ 落一枚 ≥24 位 `svc_` 字面，否则本文件自己踩自己的闸）。"""
    return b"svc_" + b"aB3d" * 8  # 长度 4＋32＝36，命中 CREDENTIAL_VALUE_SHAPE


# ---------------------------------------------------------------- A 面：真仓两条腿
def test_worktree_tracked_files_have_zero_credential_shaped_values() -> None:
    names = _tracked_names()
    scanned, hits = _scan_tree(names)
    assert scanned >= MIN_FILE_FLOOR, (
        f"射程塌：磁盘面只数出 {scanned} 枚在册文件（下限 {MIN_FILE_FLOOR}）"
        f"——名单改名／整棵树被换走会读到这个形状，⛔ 报『干净』。"
    )
    assert hits == []


def test_index_face_has_zero_credential_shaped_values() -> None:
    """入库面（索引）单独一条：裁定 §二② 判的是「跟踪面」，而⛔ 暂存的修改也算交付过。"""
    assert _grep_index() == []


# ---------------------------------------------------------------- B 面：反载体失明
def test_python_carrier_catches_planted_shape(tmp_path: Path) -> None:
    target = tmp_path / "handoff_like.md"
    body = ("旧令牌 `" + _plant_value().decode("ascii") + "` 已生效\n第二行无值\n").encode("utf-8")
    target.write_bytes(body)
    lines = [i for i, line in enumerate(target.read_bytes().split(b"\n"), 1)
             if re.search(CREDENTIAL_VALUE_SHAPE, line)]
    assert lines == [1], lines


def test_git_grep_carrier_is_not_blind_and_reports_locations_only(tmp_path: Path) -> None:
    """把修前形状种进一枚真 git 仓 ⇒ `git grep --cached` 必须抓到，且**回的是位置而⛔ 行文本**。

    ⛔ 这条的反例形状是"上游名单口径变了／模式被写坏"：那种情况下 A 面两条腿会一起绿，而绿得⛔ 有意义。
    第二半（只回位置）是同一枚腿顺带的反泄漏断言：命中时返回值⛔ 含凭据形状，否则红消息会把原文
    打进 CI 日志／终端那类出境面。
    """
    work = tmp_path / "probe"
    work.mkdir()
    (work / "docs_note.md").write_bytes(
        ("| 明文令牌 | `" + _plant_value().decode("ascii") + "` |\n").encode("utf-8")
    )
    git = ["git", "-c", "user.name=gate", "-c", "user.email=gate@invalid"]
    assert subprocess.run(git + ["init", "-q"], cwd=work, capture_output=True).returncode == 0
    assert subprocess.run(git + ["add", "docs_note.md"], cwd=work, capture_output=True).returncode == 0
    masked = _grep_shape(work)
    assert masked == ["docs_note.md:1"], masked
    for item in masked:
        assert re.search(CREDENTIAL_VALUE_SHAPE, item.encode("utf-8")) is None, item


# ---------------------------------------------------------------- C 面：⛔ 误伤占位符
def test_placeholder_shapes_are_not_flagged(tmp_path: Path) -> None:
    """注入位占位符是裁定**要求**的形状，被这道闸判红就是逼人去涂掉它。"""
    fixtures = {
        "compose_env": b"      - MQTT_PASSWORD=${MQTT_PASSWORD:-}\n",
        "placeholder": b"export BUTLER_TOKEN=svc_xxx\n",
        "paste_slot": b"| token | <PASTE_FROM_NAS_INJECTION_SLOT> |\n",
        "shortish": b"svc_abcdef012345\n",  # 16 位：⛔ 够长就当值
    }
    for name, body in fixtures.items():
        target = tmp_path / f"{name}.txt"
        target.write_bytes(body)
        assert re.search(CREDENTIAL_VALUE_SHAPE, target.read_bytes()) is None, name


# ---------------------------------------------------------------- D 面：自我射程
def test_this_judgment_file_carries_no_live_shape() -> None:
    """本文件是正控聚集地：源码面必须过自己的闸（运行期拼才⛔ 自踩）。"""
    source = Path(__file__).read_bytes()
    assert re.search(CREDENTIAL_VALUE_SHAPE, source) is None


def test_both_handoff_docs_now_carry_the_marker() -> None:
    """打码⛔ 是删除：两份交接文档仍在其位、行数结构未塌，且每份都带打码标记。"""
    docs = [
        REPO / "docs" / "handoff" / "_af_exec_append.md",
        REPO / "docs" / "handoff" / "交接单_MA联动收尾_AF侧待办_20261006.md",
    ]
    for doc in docs:
        assert doc.is_file(), doc
        body = doc.read_bytes()
        assert "已打码".encode("utf-8") in body, doc
        assert re.search(CREDENTIAL_VALUE_SHAPE, body) is None, doc
        assert body.count(b"\n") >= 20, doc  # 结构性下限：整份被截断/替换会读到这个形状
