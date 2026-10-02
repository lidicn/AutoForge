"""模板自检跑法：把 gates.yml / gates.sh 里**真实的那段脚本**抽出来跑变异测试。

为什么要抽 YAML 而不是另写一份等价脚本：等价脚本会漂。这里跑的就是模板本身，
它红了才算红、绿了才算绿。

用法：python scripts/mutation_check_templates.py <template_ci_gates_yml>
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def extract_run_block(yml_text: str, step_name: str) -> str:
    """取某个 `- name: <step_name>` 之后、缩进更深的 `run: |` 块正文。"""
    lines = yml_text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == f"- name: {step_name}")
    except StopIteration:
        raise SystemExit(f"找不到步骤 {step_name!r}")
    body: list[str] = []
    run_indent: int | None = None
    for ln in lines[start + 1 :]:
        if run_indent is None:
            m = re.match(r"^(\s+)run: \|\s*$", ln)
            if not m:
                if ln.strip() and not ln.strip().startswith("#"):
                    break  # 这一步没有 run 块
                continue
            run_indent = len(m.group(1))
            continue
        if ln.strip() == "":
            body.append("")
            continue
        cur = len(ln) - len(ln.lstrip(" "))
        if cur <= run_indent:
            break
        body.append(ln[run_indent + 2 :])
    return "\n".join(body) + "\n"


def resolve_bash() -> str:
    """拿到**真能跑**的 bash。

    Windows 上 `subprocess.run(["bash", ...])` 会先命中 `C:\\Windows\\System32\\bash.exe`
    （WSL 的 App Execution Alias）。本机没装 WSL 发行版，它只打印
    "wsl.exe --install <Distro>" 并以 rc=1 退出——于是**每条变异都"红了"**，
    看起来像"门禁很有咬合力"，实际是跑错了解释器（本仓老教训：缺包九成是跑错解释器）。
    所以这里显式找 Git Bash，并且跑一次 `echo ok` 自检；自检不过就 exit 2，绝不报绿。
    """
    import shutil

    candidates = [
        os.environ.get("GATES_BASH", ""),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        shutil.which("bash") or "",
    ]
    for cand in candidates:
        if not cand or not os.path.isfile(cand):
            continue
        if "System32" in cand:  # WSL 别名桩，不是 Git Bash
            continue
        probe = subprocess.run([cand, "-c", "echo ok"], capture_output=True, text=True)
        if probe.returncode == 0 and "ok" in (probe.stdout or ""):
            return cand
    raise SystemExit("跑不了 bash：找不到可用的 Git Bash（设 GATES_BASH=<路径> 再跑）。本脚本拒绝在'没跑成'时报绿。")


BASH = ""


def run(script: str, cwd: Path) -> tuple[int, str]:
    # 显式 utf-8：脚本输出大量中文，Windows 缺省 cp936 会在读线程里直接抛 UnicodeDecodeError。
    p = subprocess.run(
        [BASH, "-c", script],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def ratchet_cases() -> list[tuple[str, tuple[str | None, str | None], int, str]]:
    """计数棘轮的变异表：正常绿、肥化红、**降了没改上限也红**、没登记文件红、读不到数红。

    跑法是造一个假的 `homesdk-gates`（只负责吐固定计数），因为要验的是这段 shell 的判定，
    不是 AST 扫描器本身——扫描器的咬合力由 `AgentOps/gates/templates/test_quality_gates.py`
    的已知违规样本负责。

    每条除了退出码还钉**结论文案**：只看 rc 会放过"因为别的原因红"。本轮实测就是这样——
    文案里的反引号被 bash 当命令替换执行，rc 恰好也对，自检差点把这条假通过当成果。
    """
    return [
        ("cap=total → 绿", ("104", "104 # 2026-10-02 登记"), 0, "全量违规 104 条 / 登记上限 104 条"),
        ("总数涨到 105 → 红", ("105", "104 # 2026-10-02 登记"), 1, "棘轮红：总数从 104 涨到 105"),
        ("降到 103 但没改上限 → 红（防基线肥化的另一半）", ("103", "104 # 2026-10-02 登记"), 1, "棘轮提示：总数降到 103"),
        ("没登记 .gates-tally.txt → rc=2", ("104", None), 2, "缺 .gates-tally.txt"),
        ("输出解析不到计数 → rc=2（拒绝在'没读到数'时报绿）", (None, "104 # 登记"), 2, "解析不到全量计数"),
    ]


def run_ratchet(script: str, root: Path, total: str | None, tally: str | None) -> tuple[int, str]:
    """在临时仓里跑棘轮脚本：`homesdk-gates` 用同目录桩替代（Git Bash 能跑无扩展名脚本）。"""
    tally_file = root / ".gates-tally.txt"
    if tally is None:
        tally_file.unlink(missing_ok=True)
    else:
        tally_file.write_text(tally + "\n", encoding="utf-8")
    stub = root / "homesdk-gates"
    if total is not None:
        body = (
            "#!/bin/sh\n"
            f'echo "扫描完成：stub  新增/未获批 {total} 条（error 1 / warn 2），基线内存量 0 条，过期基线条目 0 条"\n'
        )
    else:
        body = '#!/bin/sh\necho "格式变了，没有那句计数"\n'
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)
    p = subprocess.run(
        [BASH, "-c", f'export PATH="$PWD:$PATH";\n{script}'],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def main() -> int:
    global BASH
    BASH = resolve_bash()
    print(f"用 bash：{BASH}")
    yml = Path(sys.argv[1]).read_text(encoding="utf-8")
    script = extract_run_block(yml, "Gates job exists and stays hard")
    pristine = "  ast-gates:\n"
    if pristine not in yml:
        print("前置不成立：模板里没有 `  ast-gates:` job 名")
        return 9

    cases: list[tuple[str, str, int, str]] = [
        # (名称, 变异函数名, 期望 rc, 期望结论文案片段)
        ("正常态必须绿", "none", 0, "检查项绿"),
        ("塞进 continue-on-error 键 → 红", "coe", 1, "判红：门禁 workflow 里出现 continue-on-error"),
        ("ast-gates 改名 → 红", "rename", 1, "判红：没有任何 workflow 声明 ast-gates job"),
        ("删掉 smoke-gates job → 红", "drop_smoke", 1, "里没有 smoke-gates job"),
        ("整个 workflow 文件消失 → 红", "drop_file", 1, "判红：仓里没有任何 workflow 文件"),
    ]

    def mutate(kind: str, text: str) -> str:
        if kind == "coe":
            return text.replace(
                "      - name: AST gates (no smoke)\n",
                "      - name: AST gates (no smoke)\n        continue-on-error: true\n",
                1,
            )
        if kind == "rename":
            return text.replace("  ast-gates:", "  ast-gates-renamed:", 1)
        if kind == "drop_smoke":
            i, j = text.index("  smoke-gates:"), text.index("  workflow-hardening:")
            return text[:i] + text[j:]
        return text

    failures = 0
    for name, kind, expected, needle in cases:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            if kind != "drop_file":
                target = wf / "gates.yml"
                target.write_text(mutate(kind, yml) if kind != "none" else yml, encoding="utf-8")
            rc, out = run(script, root)
            ok = rc == expected and needle in out
            failures += 0 if ok else 1
            mark = "✓" if ok else "✗"
            note = "" if needle in out else f"  [文案不符，期望含「{needle}」]"
            print(f"{mark} {name}: rc={rc}（期望 {expected}）  {out.splitlines()[-1][:80] if out else ''}{note}")
    print("—— 棘轮（Tally ratchet）变异 ——")
    rscript = extract_run_block(yml, "Tally ratchet (hard)")
    for name, (total, tally), expected, needle in ratchet_cases():
        with tempfile.TemporaryDirectory() as td:
            rc, out = run_ratchet(rscript, Path(td), total, tally)
            ok = rc == expected and needle in out
            failures += 0 if ok else 1
            mark = "✓" if ok else "✗"
            note = "" if needle in out else f"  [文案不符，期望含「{needle}」]"
            print(f"{mark} {name}: rc={rc}（期望 {expected}）  {out.splitlines()[-1][:80] if out else ''}{note}")
    print("模板自检：" + ("全部符合预期" if failures == 0 else f"{failures} 条不符"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
