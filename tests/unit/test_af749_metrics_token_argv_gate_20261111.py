# -*- coding: utf-8 -*-
"""判据 · AF-749：`forge metrics push` 令牌不进 argv（卡号 AF-V129-G2.7-20261111）。

**洞**（台账 §1.190）：定时任务用 `docker exec … metrics push … --token "$TOKEN"` 调 CLI
⇒ 容器内 `ps` 能读到明文 service token；宿主侧 `docker exec -e "BUTLER_TOKEN=$TOKEN"`
同样是一张 argv。修法＝令牌只走 env（`--env-file` 注入 0600 临时文件），argv 里只剩文件名。

钉的四句话（⛔ 值：本文件不出现任何真实令牌，只用变量名/假值）：
- **G1 argv 面**：`scripts/metrics_push_cron.sh` 的**可执行行**里没有 `--token`
  （注释里留的历史说明不算——静态判据必须跳过注释，否则"解释自己"会被判成违规）。
- **G2 宿主 argv 面**：没有 `-e "VAR=$值"` 形式的凭据注入；改用 `--env-file`。
- **G3 临时文件卫生**：`mktemp` ＋ `chmod 600` ＋ `trap … rm -f` 三样齐（否则就是"把令牌换成另一种落盘"）。
- **G4 CLI 口径**：`--token` **拒绝**（exit 2，AF-755 升拒绝：告警只是提醒，老脚本照样
  把令牌塞进 argv），缺省读 `BUTLER_TOKEN`；拒绝文案⛔ 不回显令牌值。
- **G5 射程声明（⑧）**：`scripts/`＋`docker/` 全量扫可执行行，确认没有第二处凭据字面进 argv；
  这是**负控**，射程＝脚本与部署面（不含 docs／报告，那里的示例是文档不是调用面）。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from autoforge import af_cli

REPO = pathlib.Path(__file__).resolve().parents[2]
CRON = REPO / "scripts" / "metrics_push_cron.sh"


def _executable_lines(text: str) -> list[tuple[int, str]]:
    """去掉整行注释与行尾注释（`#` 之后），返回 (行号, 代码行)。shell 里没有 #的字符串转义面。"""
    out: list[tuple[int, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if line.strip():
            out.append((lineno, line))
    return out


# ── G1 / G2：argv 面 ───────────────────────────────────────────────────
def test_g1_no_token_flag_in_executable_lines():
    lines = _executable_lines(CRON.read_text(encoding="utf-8"))
    offenders = [(n, ln) for n, ln in lines if "--token" in ln]
    assert not offenders, f"可执行行里仍有 --token（argv 泄露面）：{offenders}"


def test_g2_no_inline_env_secret_assignment():
    """`-e "BUTLER_TOKEN=$TOKEN"` 那类：宿主 ps 能读到整条 docker exec 命令。"""
    lines = _executable_lines(CRON.read_text(encoding="utf-8"))
    pattern = re.compile(r"-e\s+[\"']?[A-Z_]*(TOKEN|SECRET|PASSWORD|CODE)[A-Z_]*=")
    offenders = [(n, ln) for n, ln in lines if pattern.search(ln)]
    assert not offenders, f"仍有凭据经 -e 注入（宿主 argv 面）：{offenders}"


# ── G3：临时文件卫生 ───────────────────────────────────────────────────
def test_g3_envfile_is_0600_and_cleaned_up():
    text = CRON.read_text(encoding="utf-8")
    assert "mktemp" in text, "env 文件没用 mktemp ⇒ 固定名可被抢占/残留"
    assert re.search(r"chmod\s+600\s+\"?\$ENVFILE", text), "env 文件没有 chmod 600"
    assert re.search(r"trap\s+'?rm -f\s+\"?\$ENVFILE", text), "env 文件没有 trap 清理（异常路径会留下令牌）"
    lines = _executable_lines(text)
    assert any("--env-file" in ln for _n, ln in lines), "没用 --env-file 注入（令牌又回到 argv 了）"


# ── G4：CLI 口径 ───────────────────────────────────────────────────────
def test_g4_cli_reads_env_and_warns_on_explicit_token(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    seen: dict[str, str] = {}

    class _FakeIngester:
        def __init__(self, buffer_dir=None):
            seen["buffer_dir"] = str(buffer_dir)

        def push(self, snapshot, *, ma_url="", token="", dry_run=False):
            seen["token"] = token
            seen["ma_url"] = ma_url
            return {"ok": True, "dry_run": dry_run}

        def flush_buffer(self, *, ma_url="", token=""):
            return {"flushed": 0}

    monkeypatch.setattr(af_cli, "Ingester", _FakeIngester)
    monkeypatch.setenv("BUTLER_TOKEN", "ENV_TOKEN_SENTINEL")

    runner = CliRunner()
    ir = REPO / "examples" / "ir" / "case01_day_light.json"
    assert ir.is_file(), "缺示例 IR，本腿无从判定"

    # ① 不给 --token ⇒ 从环境变量取，且**没有**告警
    r1 = runner.invoke(af_cli.app, ["metrics", "push", str(ir), "--dry-run",
                                    "--buffer-dir", str(tmp_path)])
    assert r1.exit_code == 0, r1.output
    assert seen["token"] == "ENV_TOKEN_SENTINEL", "缺省没有读 BUTLER_TOKEN"
    assert "argv" not in r1.output, f"没给 --token 却告警了：{r1.output}"

    # ② 给了 --token ⇒ **拒绝**（AF-755 升拒绝；exit 2 才叫"拒绝"，warn-only 等于没修）
    r2 = runner.invoke(af_cli.app, ["metrics", "push", str(ir), "--dry-run",
                                    "--token", "ARGV_SENTINEL",
                                    "--buffer-dir", str(tmp_path)])
    assert r2.exit_code == 2, f"--token 必须拒绝（exit 2），实际 {r2.exit_code}：{r2.output}"
    assert "BUTLER_TOKEN" in r2.output, "拒绝文案要给替代口径（否则调用方不知道怎么改）"
    assert "ARGV_SENTINEL" not in r2.output.replace("--token", ""), "⛔ 拒绝文案不该回显令牌值"


# ── G5：顺藤扫（负控＋覆盖声明，⑧）────────────────────────────────────
def test_g5_no_other_argv_credential_face():
    """射程：`scripts/` 与 `docker/` 下的 `.sh`／`.yml`／`.yaml` 可执行行。

    ⛔ 不扫 `docs/`、报告与 `*.md`：那里的 `--token <butler>` 是**文档示例**，不是调用面
    （把它们算进来只会逼人删文档，而不是修泄露）。
    """
    roots = [REPO / "scripts", REPO / "docker"]
    exts = {".sh", ".yml", ".yaml"}
    pattern = re.compile(r"--(token|password|secret|auth-code)\s+\S")
    hits: list[str] = []
    scanned = 0
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in exts:
                continue
            scanned += 1
            for lineno, line in _executable_lines(path.read_text(encoding="utf-8", errors="replace")):
                if pattern.search(line):
                    hits.append(f"{path.relative_to(REPO).as_posix()}:{lineno}: {line.strip()[:90]}")
    assert scanned > 0, "一个文件都没扫到 ⇒ 射程塌，⛔ 报干净"
    assert not hits, "还有凭据经 argv 传递的调用面：" + "；".join(hits)


def test_g5b_cron_script_is_in_the_scan_range():
    """自证射程没塌：本批修的那个脚本必须落在 G5 的扫描集合里。"""
    assert CRON.is_file()
    assert CRON.parent == REPO / "scripts"
    assert CRON.suffix == ".sh"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
