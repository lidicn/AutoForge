"""`scripts/check_sim_dep_floor.py` 的判据（裁定 20261011《十三问》§3 Q4.3，执行记录 §二之一百一十四）。

要钉住的形状：**同一枚包在两枚 extra 里各抄一遍时，没有任何东西保证两处同值**，而"没约束"那一族
在重建时会自己往下走薄——CI 面（钉 3.11）与镜像面（base 3.14）解析出的仿真依赖本来就不是同一版
（PyPI 现读：`>=3.11` 最新 `0.13.109`、`>=3.14` 是 `0.13.371`）。

红腿按三条判据各自单独打（A 裸名／B 在册下界逐面／C 依据锚点），绿腿把真仓读数钉死
（18 条依赖、六枚 extra 的条数、在册那枚在 `sim` 与 `dev` 两面同为 `>=0.13.109`）。
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_sim_dep_floor.py"

VHASS = "pytest-homeassistant-custom-component"
FLOOR = ">=0.13.109"

PYPROJECT = f'''[project]
name = "autoforge"
requires-python = ">=3.11"
dependencies = ["jsonschema>=4.20", "typer>=0.12"]

[project.optional-dependencies]
sim = ["{VHASS}{FLOOR}"]
dev = ["pytest>=8.0", "{VHASS}{FLOOR}"]
api = ["fastapi>=0.110", "uvicorn>=0.29"]
mqtt = ["paho-mqtt>=1.6,<2.1"]
'''

# 合法依据样本：含「裁定」＋一个盘上真在的锚点（tmp 树里只保证 pyproject.toml 在）。
BASIS = "裁定 20261011《十三问》§3 Q4.3 裁「仿真依赖钉版本：做」。读数存证 `pyproject.toml:1`。"
DRIFT_FLOORS = {VHASS: (FLOOR, ("sim", "dev"), BASIS)}


def _mod(floors: dict[str, tuple[str, tuple[str, ...], str]] | None = None):
    spec = importlib.util.spec_from_file_location("check_sim_dep_floor", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if floors is not None:
        mod.SIM_DEP_FLOORS = floors
    return mod


def _tree(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    (tmp_path / "pyproject.toml").write_text(text, encoding="utf-8")
    return tmp_path


def _check(tmp_path: pathlib.Path, text: str = PYPROJECT, floors: dict | None = DRIFT_FLOORS):
    return _mod(floors).check(_tree(tmp_path, text))


def _anchor(tmp_path: pathlib.Path, text: str):
    return _mod().anchor_ok(_tree(tmp_path, text))


def _sub(old: str, new: str, text: str = PYPROJECT) -> str:
    assert old in text, f"样本里没有 {old!r}"
    return text.replace(old, new, 1)


# ── 真仓实测 ─────────────────────────────────────────────────────────

def test_real_repo_is_green_and_pinned():
    mod = _mod()
    assert mod.anchor_ok(ROOT) is None
    findings, info = mod.check(ROOT)
    assert findings == [], f"真仓读数不该为空却给出：{findings}"
    assert info["total"] == 18, f"包声明依赖总数现读 {info['total']}"
    assert info["core"] == ["jsonschema>=4.20", "typer>=0.12"], info["core"]
    assert info["extras"] == {"api": 2, "dev": 10, "ha": 1, "homesdk": 1, "mqtt": 1, "sim": 1}, info["extras"]
    want, per = info["floors"][VHASS]
    assert want == FLOOR and per == {"sim": FLOOR, "dev": FLOOR}, f"在册下界现读 {want} / {per}"


def test_real_repo_register_is_not_empty():
    """反空集：登记为空时 B／C 两条判据一起变成纸门，所以先证本门今天有射程。"""
    floors = _mod().SIM_DEP_FLOORS
    assert floors, "登记空了——那 B、C 两条判据此刻谁也咬不到"
    for name, (_want, required, basis) in floors.items():
        assert required, f"`{name}` 那条没写它必须出现在哪几枚 extra"
        assert "裁定" in basis and "`" in basis, f"`{name}` 的依据没有可核锚点"


def test_real_repo_sim_dependency_carries_the_ruled_floor():
    """裁定 Q4.3 裁「做」的字面对象：`sim` 与 `dev` 两枚 extra 里那一枚都带上界，且两处同值。"""
    import tomllib
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    for extra in ("sim", "dev"):
        hits = [x for x in extras[extra] if str(x).startswith(VHASS)]
        assert hits == [f"{VHASS}{FLOOR}"], f"`{extra}` 里那一枚现读 {hits}"


# ── 判据 A：每一枚依赖都带约束 ───────────────────────────────────────

def test_bare_core_dependency_goes_red(tmp_path):
    findings, _info = _check(tmp_path, _sub('"typer>=0.12"', '"typer"'))
    assert len(findings) == 1 and "裸名依赖" in findings[0] and "typer" in findings[0], findings


def test_bare_dependency_in_an_unregistered_extra_goes_red(tmp_path):
    """A 是全量的，不只看在册那枚：`api` extra 里放裸名同样红。"""
    findings, _info = _check(tmp_path, _sub('"fastapi>=0.110"', '"fastapi"'))
    assert len(findings) == 1 and "`api`" in findings[0], findings


def test_upper_bound_only_still_counts_as_constrained(tmp_path):
    """本门只判"带不带约束"，不判约束的形状：`<2.1` 也算带约束，不该被误判成裸名。"""
    findings, _info = _check(tmp_path, _sub('"typer>=0.12"', '"typer<2.1"'))
    assert findings == [], findings


# ── 判据 B：在册下界逐枚 extra 同值 ─────────────────────────────────

def test_one_extra_lowered_alone_goes_red_for_that_face_only(tmp_path):
    """同包两枚手抄改一枚忘一枚：只咬被改的那一面，另一面不得跟着喊。"""
    findings, _info = _check(tmp_path, _sub(f'sim = ["{VHASS}{FLOOR}"]', f'sim = ["{VHASS}>=0.13.100"]'))
    assert len(findings) == 1, findings
    assert "`sim`" in findings[0] and ">=0.13.100" in findings[0] and FLOOR in findings[0], findings[0]


def test_registered_package_dropping_its_floor_goes_red(tmp_path):
    """保留约束、去掉下界（写成 `<0.14`）：A 放过、B 必须报——"有约束"不等于"钉住了下界"。"""
    findings, _info = _check(tmp_path, _sub(f'"{VHASS}{FLOOR}"]\ndev', f'"{VHASS}<0.14"]\ndev'))
    assert len(findings) == 1, findings
    assert "没有 `>=` 下界" in findings[0] and "裸名" not in findings[0], findings[0]


def test_registered_package_disappearing_from_one_extra_goes_red(tmp_path):
    """在册项不许静默蒸发：从 `dev` 里整条删掉，登记那一面就没人对撞了。"""
    findings, _info = _check(tmp_path, _sub(f'dev = ["pytest>=8.0", "{VHASS}{FLOOR}"]', 'dev = ["pytest>=8.0"]'))
    assert len(findings) == 1 and "从这些 extra 里消失了" in findings[0] and "dev" in findings[0], findings


def test_marker_only_specifier_is_not_read_as_a_floor(tmp_path):
    """写成 `==` 的精确钉不是 `>=` 下界，得单独报，不能被当成"已钉死"放过。"""
    findings, _info = _check(tmp_path, _sub(f'sim = ["{VHASS}{FLOOR}"]', f'sim = ["{VHASS}==0.13.109"]'))
    assert len(findings) == 1 and "没有 `>=` 下界" in findings[0], findings


# ── 判据 C：依据必须指得到裁定与盘上锚点 ────────────────────────────

@pytest.mark.parametrize("basis,phrase", [
    ("   ", "依据为空"),
    ("PyPI 现读 `>=3.11` 面最新 0.13.109，存证 `pyproject.toml:1`。", "没有「裁定」"),
    ("裁定 20261011 §3 Q4.3。存证 `docker/never-exists.md`。", "没有一个盘上真实存在"),
])
def test_bad_basis_goes_red(tmp_path, basis, phrase):
    floors = {VHASS: (FLOOR, ("sim", "dev"), basis)}
    findings, _info = _check(tmp_path, floors=floors)
    assert len(findings) == 1 and phrase in findings[0], findings


# ── 射程塌了不许报干净 ───────────────────────────────────────────────

def test_no_optional_dependencies_is_exit_two(tmp_path):
    assert _anchor(tmp_path, '[project]\nname = "autoforge"\nrequires-python = ">=3.11"\n') is not None


def test_missing_registered_extra_is_exit_two(tmp_path):
    """登记要对比的那枚 extra 整枚不在——比"少一格"更基本，射程没了就该 exit 2 而不是判 0 条问题。"""
    assert _anchor(tmp_path, _sub(f'sim = ["{VHASS}{FLOOR}"]\n', "")) is not None


def test_unparseable_pyproject_is_exit_two(tmp_path):
    assert _anchor(tmp_path, '[project\nname = "autoforge"\n') is not None


def test_no_project_table_is_exit_two(tmp_path):
    assert _anchor(tmp_path, '[tool.ruff]\nline-length = 100\n') is not None
