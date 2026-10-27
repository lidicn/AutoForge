"""Dockerfile 指令语法 + af_guide 正文进镜像（升级失败换来的判据，2026-10-28）。

背景（出资人现场：「更新失败：镜像构建失败，未切流，旧镜像继续服务」）：
af_guide 那笔给 Dockerfile.api 补 `COPY skills` 时，我把说明写成了**行内注释**：

    COPY skills ./skills   # af_guide 的正文来源；缺这行 ⇒ ...

Dockerfile 的 `#` **只有出现在指令首个 token 时才算注释**（parser 先按空白切词再丢弃
整行注释），所以行内 `#` 连同后面的中文被 Docker 当成了 COPY 的额外源路径 ——
构建期报 "not found" 直接失败。宿主机护栏正确兜住（未切流、旧容器继续服务），
但代价是一次升级白跑：这种错必须是机械判据拦住的，不该等 `docker build` 才发现。

本文件两条腿各钉一族：
1. 语法腿：docker/Dockerfile* 里凡是指令行（非行首 `#`），出现行内 `#` 即红。
2. 内容腿：Dockerfile.api 必须有 `COPY skills`，且 skills/autoforge.md 在仓内真实存在
   —— 否则 af_guide 在生产读不到文件（它诚实回 ok:False，但"有工具没正文"就是坑）。

两条都能变红：注入一行 `COPY x ./y  # 注释` 或删掉 COPY skills，对应腿红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCKERFILES = sorted((REPO / "docker").glob("Dockerfile*"))

# 行续接（`\` 结尾）在语法上仍属同一条指令，判行内 `#` 时按逻辑行处理
_INSTR_RE = re.compile(
    r"^\s*(FROM|RUN|CMD|LABEL|EXPOSE|ENV|ADD|COPY|ENTRYPOINT|VOLUME|USER|WORKDIR|ARG|"
    r"ONBUILD|STOPSIGNAL|HEALTHCHECK|SHELL)\b",
    re.I,
)


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """把反斜杠续接合并成逻辑行，返回 (起始行号, 内容)。"""
    out: list[tuple[int, str]] = []
    raw = text.splitlines()
    i = 0
    while i < len(raw):
        start, buf = i + 1, raw[i]
        while buf.endswith("\\") and i + 1 < len(raw):
            i += 1
            buf = buf[:-1] + " " + raw[i]
        out.append((start, buf))
        i += 1
    return out


@pytest.mark.parametrize("df", DOCKERFILES, ids=lambda p: p.name)
def test_no_inline_comment_in_instruction(df: Path):
    """指令行内不得有 `#`——它会被当成参数（COPY 的额外源 / RUN 的实参），不是注释。"""
    assert df.is_file(), f"{df} 不在"
    bad: list[str] = []
    for lineno, line in _logical_lines(df.read_text(encoding="utf-8")):
        s = line.strip()
        if not s or s.startswith("#"):
            continue                      # 整行注释，合法
        if not _INSTR_RE.match(s):
            continue                      # 非指令行（空/续行残留），不参与判定
        # 引号内的 # 不算（ENV 值、RUN 里 python -c 'a#b' 等）：先剥掉引号段再判
        stripped = re.sub(r"'[^']*'|\"[^\"]*\"", "''", s)
        if re.search(r"\s#", stripped):
            bad.append(f"{df.name}:{lineno} {line.strip()[:90]}")
    assert not bad, "Dockerfile 指令带行内注释（# 会被当成参数）：\n  " + "\n  ".join(bad)


def test_api_image_carries_guide_source():
    """af_guide 的正文必须真进镜像：COPY skills 在，且源文件在仓内（COPY 的源不存在即构建失败）。"""
    api = REPO / "docker" / "Dockerfile.api"
    text = api.read_text(encoding="utf-8")
    copies_skills = any(
        re.match(r"^COPY\b", l.strip()) and re.search(r"\bskills\b", l)
        for l in text.splitlines()
    )
    assert copies_skills, "Dockerfile.api 没有 COPY skills ⇒ af_guide 在生产读不到指引（工具变空壳）"
    assert (REPO / "skills" / "autoforge.md").is_file(), "skills/autoforge.md 不在仓内 ⇒ COPY 的源缺失，构建必失败"


def test_guide_path_resolves_inside_image_layout(tmp_path, monkeypatch):
    """镜像布局下（/app/src/autoforge/af_mcp.py + /app/skills/）路径推导必须命中。

    钉的是 `_guide_path` 不依赖 cwd：容器启动目录不确定，靠 `__file__` 往上找仓库根。
    """
    from autoforge.af_mcp import _guide_path

    pkg = tmp_path / "app" / "src" / "autoforge"
    pkg.mkdir(parents=True)
    (tmp_path / "app" / "skills").mkdir()
    (tmp_path / "app" / "skills" / "autoforge.md").write_text("# guide\n", encoding="utf-8")
    monkeypatch.delenv("AF_GUIDE_PATH", raising=False)
    monkeypatch.setattr("autoforge.af_mcp.__file__", str(pkg / "af_mcp.py"))
    # __file__ 被 patch 后 resolve().parent.parent.parent == tmp_path/app
    got = _guide_path()
    assert got == tmp_path / "app" / "skills" / "autoforge.md", got
