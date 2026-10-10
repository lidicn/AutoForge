"""`scripts/check_trust_boundary.py` 的判据（第六轮审计 ARCH-05，执行记录 §二之九十九）。

要钉住的形状：**档位是从运行期路由表的依赖树上读出来的，不是从变量名猜的**。
最要紧的一格是 `_readonly_guard`——它挂在四条匿名 POST 上，文本上看"有守卫"，实际判的是单写者租约、
不鉴权。既有那条 UI↔路由门（`check_ui_api_paths.py`）只判"路径在不在、方法对不对"，读不出鉴权档，
所以本批新门补的是 ARCH-05 说的那一缝：边界没有一份可 diff 的资产，新端点匿名上线没人拦。

红腿按五条判据各自单独打（A 清单漂移／B 默认拒绝／C 登记不可核对／D 过期登记／E 安装器接线），
绿腿把现网读数钉死（90 条路由、七档计数、14 条非鉴权档全部在册、2 个未挂载安装器）。
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_trust_boundary.py"


def _mod():
    spec = importlib.util.spec_from_file_location("check_trust_boundary", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _mod()


def _row(method, path, tier, deps="（无）", call="h_one", first_party=True):
    return {"methods": method, "path": path, "tier": tier, "deps": deps,
            "call": call, "first_party": first_party}


def _rows():
    """一小组合成路由：七档各一条，够把五条判据打到。"""
    return [
        _row("GET", "/api/health", "anon", call="api_health"),
        _row("POST", "/api/sim", "anon", "_readonly_guard", call="api_sim"),
        _row("GET", "/api/auth/me", "optional", "dep", call="api_auth_me"),
        _row("POST", "/api/auth/logout", "bearer-in-handler", "security:HTTPBearer", call="api_auth_logout"),
        _row("GET", "/api/graphs", "scope:read", "requires(read)", call="api_graphs"),
        _row("POST", "/api/build-x", "scope:write", "requires(write)", call="api_build_x"),
        _row("POST", "/api/live/run", "scope:live", "requires(live)", call="api_live_run"),
        _row("GET", "/docs", "builtin", call="-", first_party=False),
    ]


REGISTER_OK = "\n".join([
    "- `GET /api/health` · 处理器：api_health · 依据：docs/x.md · 理由：探针面给外部监控无凭据回读，不含用户数据",
    "- `POST /api/sim` · 处理器：api_sim · 依据：docs/x.md · 理由：仿真面会随 store 落遥测，匿名口径归裁定不归本门",
    "- `GET /api/auth/me` · 处理器：api_auth_me · 依据：docs/x.md · 理由：登录态恢复，不带令牌一律 401，不回他人数据",
    "- `POST /api/auth/logout` · 处理器：api_auth_logout · 依据：docs/x.md · 理由：只撤销调用方自己带来的那枚令牌",
])

INSTALLERS_OK = "\n".join([
    "- 入口：src/autoforge/af_plugins.py::install_api · 断言：未挂载（src/ 内无调用者） · 备注：那条挂载不带鉴权依赖",
])


def _tree(tmp_path: pathlib.Path, rows=None, register=REGISTER_OK,
          installers=INSTALLERS_OK, *, doc=True, auto_markers=True) -> pathlib.Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "src" / "autoforge").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "x.md").write_text("# 记录\n", encoding="utf-8")
    (root / "src" / "autoforge" / "af_plugins.py").write_text(
        "def install_api(app, ext):\n    app.add_api_route('/api/x', f, methods=['GET'])\n", encoding="utf-8"
    )
    (root / "src" / "autoforge" / "af_api.py").write_text(
        "@app.get('/api/health')\ndef api_health():\n    return {}\n", encoding="utf-8"
    )
    if not doc:
        return root
    body = "\n".join(mod.render_table(rows if rows is not None else _rows()))
    markers = (mod.AUTO_BEGIN, mod.AUTO_END) if auto_markers else ("<!-- X -->", "<!-- Y -->")
    text = (
        "# 清单\n\n"
        "## 一、口径\n\n"
        "## 二、端点表（自动段）\n\n"
        f"{markers[0]}\n{body}\n{markers[1]}\n\n"
        f"{mod.REGISTER_HEADING}\n\n{register}\n\n"
        f"{mod.INSTALLER_HEADING}\n\n{installers}\n\n"
        "## 五、复测对撞\n\n略。\n"
    )
    (root / mod.DOC_REL).write_text(text, encoding="utf-8")
    return root


def _findings(root, rows=None):
    real = mod.derive_rows
    mod.derive_rows = lambda _r: (rows if rows is not None else _rows(), [])
    try:
        return mod.check(root)
    finally:
        mod.derive_rows = real


# ───────────────────────── 档位判定（本批的承重形状）─────────────────────────

def test_readonly_guard_is_not_authentication():
    """`_readonly_guard` 单独挂着 ⇒ anon。文本 grep 会把这四条 POST 读成"有守卫"。"""
    assert mod._tier(("_readonly_guard",)) == "anon"
    assert mod._tier(("requires(write)", "_readonly_guard")) == "scope:write"


def test_tier_reads_scope_from_closure_not_from_variable_name():
    def factory(scope):
        def dep():
            return scope

        dep.__qualname__ = "build_app.<locals>.requires.<locals>.dep"
        return dep
    assert mod._dep_label(factory("write")) == "requires(write)"
    assert mod._dep_label(factory("live")) == "requires(live)"
    assert mod._tier(("requires(write)",)) == "scope:write"


def test_multiple_scopes_on_one_route_are_all_listed():
    assert mod._tier(("requires(read)", "requires(write)")) == "scope:read+write"


def test_optional_and_security_schemes_get_their_own_tiers():
    assert mod._tier(("dep",)) == "optional"
    assert mod._tier(("security:HTTPBearer",)) == "bearer-in-handler"
    assert mod._tier(()) == "anon"


def test_dep_label_keeps_innermost_qualname_and_names_security_types():
    def _readonly_guard():
        return None

    _readonly_guard.__qualname__ = "build_app.<locals>._readonly_guard"
    assert mod._dep_label(_readonly_guard) == "_readonly_guard"

    class HTTPBearer:  # 没有 __qualname__ 的实例面：安全方案
        pass

    assert mod._dep_label(HTTPBearer()) == "security:HTTPBearer"


# ───────────────────────────── A 清单对撞 ─────────────────────────────

def test_clean_tree_is_green(tmp_path):
    root = _tree(tmp_path)
    findings, info = _findings(root)
    assert findings == []
    assert info["rows"] == 8


def test_new_route_absent_from_inventory_is_red(tmp_path):
    # 清单是**改路由之前**生成的（`_tree` 用干净读数），漂移后的读数才交给门
    rows = _rows() + [_row("POST", "/api/brand-new", "anon", call="api_brand_new")]
    root = _tree(tmp_path)
    findings, _ = _findings(root, rows)
    assert any("A：" in f for f in findings)
    assert any("/api/brand-new" in f for f in findings)


def test_tier_drift_between_doc_and_runtime_is_red(tmp_path):
    """清单写着 read、现读是 anon（守卫被摘掉）——这一格就是边界被无声放大。"""
    rows = [dict(x, tier="anon", deps="（无）") if x["path"] == "/api/graphs" else x for x in _rows()]
    root = _tree(tmp_path)
    findings, _ = _findings(root, rows)
    assert any("档位变了" in f for f in findings)


def test_handler_swap_under_same_path_is_red(tmp_path):
    rows = [dict(x, call="api_graphs_v2") if x["path"] == "/api/graphs" else x for x in _rows()]
    root = _tree(tmp_path)
    findings, _ = _findings(root, rows)
    assert any("处理器变了" in f for f in findings)


def test_register_wording_reword_does_not_move_the_gate(tmp_path):
    """门不判散文：把四条理由整段换措辞（读数不动），结果集必须一致。"""
    moved = "\n".join(
        line.replace("理由：", "理由：按 owner 复述的意思——")
        for line in REGISTER_OK.splitlines()
    )
    root_a = _tree(tmp_path / "a")
    root_b = _tree(tmp_path / "b", register=moved)
    fa, _ = _findings(root_a)
    fb, _ = _findings(root_b)
    assert fa == fb == []


# ───────────────────────── B 默认拒绝／C 可核对／D 只减不增 ─────────────────────────

def test_unregistered_anonymous_endpoint_is_red(tmp_path):
    root = _tree(tmp_path, register="- `GET /api/health` · 处理器：api_health · 依据：docs/x.md · 理由：探针面给外部监控无凭据回读，不含用户数据")
    findings, _ = _findings(root)
    hits = [f for f in findings if "B：" in f]
    assert len(hits) == 3  # /api/sim(anon) · /api/auth/me(optional) · /api/auth/logout(bearer)
    assert any("默认拒绝" in f for f in hits)


def test_register_handler_must_match_runtime_handler(tmp_path):
    bad = REGISTER_OK.replace("处理器：api_sim", "处理器：api_sim_old")
    findings, _ = _findings(_tree(tmp_path, register=bad))
    assert any("C：" in f and "换人" in f for f in findings)


def test_register_anchor_must_be_a_real_file_inside_repo(tmp_path):
    bad = REGISTER_OK.replace("依据：docs/x.md", "依据：docs/nope.md", 1)
    findings, _ = _findings(_tree(tmp_path, register=bad))
    assert any("不是仓内真实存在的文件" in f for f in findings)
    escape = REGISTER_OK.replace("依据：docs/x.md", "依据：../../escape.md", 1)
    (tmp_path / "escape.md").write_text("x\n", encoding="utf-8")
    findings2, _ = _findings(_tree(tmp_path / "esc", register=escape))
    assert any("不是仓内真实存在的文件" in f for f in findings2)


def test_placeholder_or_short_reason_is_red(tmp_path):
    for reason in ("待补", "略", "too short"):
        bad = REGISTER_OK.replace(
            "理由：探针面给外部监控无凭据回读，不含用户数据", f"理由：{reason}", 1
        )
        findings, _ = _findings(_tree(tmp_path / reason, register=bad))
        assert any("占位词" in f or "过短" in f for f in findings), reason


def test_stale_register_entry_is_red_only_if_no_longer_unguarded(tmp_path):
    """只减不增：把 /api/graphs 那条登记留着（它现在是 read 档）必须红，逼人删掉过期行。"""
    stale = REGISTER_OK + "\n- `GET /api/graphs` · 处理器：api_graphs · 依据：docs/x.md · 理由：这条早就挂上 read 了"
    findings, _ = _findings(_tree(tmp_path, register=stale))
    assert any("D：" in f and "过期登记" in f for f in findings)


@pytest.mark.parametrize("method", ["DELETE", "PUT"])
def test_inventory_row_method_is_part_of_the_key(tmp_path, method):
    """同一路径不同方法是两条端点；登记里的方法抄错＝既进不了 B 也躲不过 D。"""
    rows = [dict(x, methods=method) if x["path"] == "/api/sim" else x for x in _rows()]
    findings, _ = _findings(_tree(tmp_path), rows)
    assert any("A：" in f for f in findings)


# ─────────────────────────────── E 安装器面 ───────────────────────────────

def test_installer_file_without_register_is_red(tmp_path):
    root = _tree(tmp_path, installers="（今天没有）")
    findings, _ = _findings(root)
    assert any("E：" in f and "af_plugins.py" in f and "没登记" in f for f in findings)


def test_wiring_inside_the_installer_file_itself_is_still_wiring(tmp_path):
    """就地接线也算接线：调用点写在定义 `install_api` 那份文件里，不能因为"那是它自己的文件"就放过。"""
    root = _tree(tmp_path)
    (root / "src" / "autoforge" / "af_api.py").write_text(
        "from autoforge.af_plugins import install_api\n\n\n"
        "def build_app():\n"
        "    app = object()\n"
        "    ext = object()\n"
        "    install_api(app, ext)\n",
        encoding="utf-8",
    )
    findings, _ = _findings(root)
    assert any("接线已经发生" in f and "af_api.py:7" in f for f in findings)


def test_prose_mention_of_the_installer_is_not_a_call(tmp_path):
    """本批真修过的缺陷：caller 判据原先按文本 grep，注释里一句 `install_api(app, service)` 就会报"已接线"。

    真仓现读就是这样：`af_runtime_plugins.py` 的注释里有这个名字，而它**不是**调用。判据读 AST 的
    `Call` 节点，所以这一腿同时是自证与反例——把判据退回文本面，这腿立刻红。
    """
    root = _tree(tmp_path)
    prose = (
        "# 端点通过 install_api(app, service) 挂载，\n"
        "# 由 install_api() 统一处理\n"
        "X = 1\n"
    )
    wired = "def serve():\n    install_api(app, ext)\n"
    other = root / "src" / "autoforge" / "af_other.py"
    other.write_text(prose, encoding="utf-8")
    assert mod.has_caller(root, "install_api") == []
    other.write_text(wired, encoding="utf-8")
    hits = mod.has_caller(root, "install_api")
    assert hits == ["src/autoforge/af_other.py:2"]
    findings, _ = _findings(root)
    assert any("接线已经发生" in f for f in findings)


def test_unrecognized_installer_claim_shape_is_red(tmp_path):
    weird = "- 入口：src/autoforge/af_plugins.py::install_api · 断言：大概没挂 · 备注：说不清"
    findings, _ = _findings(_tree(tmp_path, installers=weird))
    assert any("本门只认这两种形状" in f for f in findings)


def test_stale_installer_register_is_red(tmp_path):
    root = _tree(tmp_path)
    (root / "src" / "autoforge" / "af_plugins.py").write_text("X = 1\n", encoding="utf-8")
    findings, _ = _findings(root)
    assert any("E：" in f and "过期登记" in f for f in findings)


# ───────────────────────────── exit 2 射程档 ─────────────────────────────

@pytest.mark.parametrize("case", ["no_doc", "no_markers", "no_register_section", "empty_route_table"])
def test_range_breaks_exit_2(tmp_path, case, capsys):
    if case == "no_doc":
        root = _tree(tmp_path, doc=False)
    elif case == "no_markers":
        root = _tree(tmp_path, auto_markers=False)
    elif case == "no_register_section":
        root = _tree(tmp_path)
        text = (root / mod.DOC_REL).read_text(encoding="utf-8").replace(mod.REGISTER_HEADING, "## 三、没有了")
        (root / mod.DOC_REL).write_text(text, encoding="utf-8")
    else:
        root = _tree(tmp_path)
    real = mod.derive_rows
    if case == "empty_route_table":
        mod.derive_rows = lambda _r: ([], ["`fastapi` 导入失败"])
    rc = mod.main(["check_trust_boundary.py", str(root)])
    mod.derive_rows = real
    assert rc == 2
    assert "读不出射程" in capsys.readouterr().out


def test_unparseable_register_block_is_red_and_exit_2_when_whole_block_unreadable(tmp_path, capsys):
    """登记行形状变了：单条解析不出＝红（不许静默跳过）；整块读不出＝exit 2（射程断）。"""
    broken_line = "- `GET /api/health` 处理器（分隔符被改成全角）api_health"
    findings, _ = _findings(_tree(tmp_path, register=broken_line))
    assert any("C：登记行解析不出" in f for f in findings)

    root = _tree(tmp_path)
    text = (root / mod.DOC_REL).read_text(encoding="utf-8").replace(REGISTER_OK, broken_line)
    (root / mod.DOC_REL).write_text(text, encoding="utf-8")
    real = mod.derive_rows
    mod.derive_rows = lambda _r: (_rows(), [])
    rc = mod.main(["x", str(root)])
    mod.derive_rows = real
    assert rc == 2
    assert "读不出射程" in capsys.readouterr().out


def test_exit_two_text_names_the_fix_direction(tmp_path, capsys):
    root = tmp_path / "nothing"
    root.mkdir()
    assert mod.main(["x", str(root)]) == 2
    out = capsys.readouterr().out
    assert "射程断不是" in out


# ──────────────────────────── 真仓绿腿（把现读钉死）───────────────────────────

def test_real_repo_is_green_and_pinned():
    assert mod.anchor_ok(ROOT) is None
    findings, info = mod.check(ROOT)
    assert findings == [], findings[:3]
    assert info["rows"] == 90
    assert info["ungated"] == 14
    assert info["register"] == 14
    assert info["found_installers"] == [
        "src/autoforge/af_conflict_runtime.py",
        "src/autoforge/af_runtime_plugins.py",
    ]


def test_real_repo_tier_counts_are_the_batch_reading():
    rows, unknown = mod.derive_rows(ROOT)
    assert unknown == []
    counts: dict[str, int] = {}
    for x in rows:
        counts[x["tier"]] = counts.get(x["tier"], 0) + 1
    assert counts == {
        "scope:write": 39, "scope:read": 30, "scope:live": 3,
        "anon": 10, "optional": 2, "bearer-in-handler": 2, "builtin": 4,
    }


def test_the_four_anonymous_posts_are_the_substance_of_the_batch():
    """报告 F-10 那一族：声明了 `_readonly_guard` 的四条 POST 其实一条令牌都不看。"""
    rows, _ = mod.derive_rows(ROOT)
    by_path = {x["path"]: x for x in rows}
    for p in ("/api/build", "/api/bind", "/api/sim", "/api/spec/compile"):
        assert by_path[p]["tier"] == "anon", p
        assert by_path[p]["deps"] == "_readonly_guard", p


def test_two_reports_counts_reconciled_in_inventory_doc():
    """执行记录要引这张表：read 30／write 39／live 3 与第三轮报告逐条对上，"无鉴权"那一档本门读成 14。"""
    text = (ROOT / mod.DOC_REL).read_text(encoding="utf-8")
    assert "scope:read=30" in text and "scope:write=39" in text and "scope:live=3" in text
    assert "anon=10" in text and "optional=2" in text and "bearer-in-handler=2" in text
    assert "非鉴权档第一方端点 **14 条**" in text
