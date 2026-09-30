# 交接卡 —— G7 AF-Spec

> 模板见 `docs/交接卡_模板.md`。红线依据：`IR_AND_RUNTIME` §1（Spec 是投影，Graph 才是模型）。

## 0. 元信息

| 项 | 值 |
|---|---|
| 里程碑 | G7 AF-Spec（面向 Agent 的文本语法 → 同一份 JSON IR） |
| 完成日期 | 2026-09-14 |
| 状态 | ✅ 已交付 |
| 双环境自测 | 本机 190 passed / 5 skipped；NAS 真 vhass **195 passed** |

## 1. 文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/autoforge/af_spec.py` | [NEW] | `compile_spec`（AF-Spec→IR→Graph，经 Schema 校验）、`render_spec`（Graph→AF-Spec）、`graph_to_raw`、`SpecError` |
| `src/autoforge/af_cli.py` | [MODIFY] | 新增 `forge spec compile` / `forge spec render` 子应用 |
| `tests/unit/test_af_spec.py` | [NEW] | 11 条：编译结构 / 全样例往返一致 / NL 对齐 / 错误 / CLI |

## 2. 行为增量

- **新增能力**：
  - `compile_spec(text)`：行式文本 → JSON IR（`load_graph` 走同一 Schema）→ Graph。
  - `render_spec(graph)`：Graph → 文本（与 compile 互逆）。
  - 覆盖全部 7 种节点（`on/if/do/ask/wait/set/pass`）+ 6 种边；支持 `confidence/mode/version/snapshot/persist/meta/vars`、`canary`/`requires_confirm`/`atomic`/`result_var`。
  - `forge spec compile <spec> [-o out.json] [--nl]`、`forge spec render <ir>`。
- **零有损往返**：渲染器只写出原始 IR 中**出现过**的字段，解析器只在对应 token 出现时才填该字段 → `compile_spec(render_spec(g))` 与 `g` 的 raw 结构一致（全样例验证）。
- **对外契约**：新增 `spec` CLI 子命令；无 IR Schema 改动（AF-Spec 只是投影）。

## 3. 验证方式

```powershell
.\.venv314\Scripts\python.exe -m pytest tests/unit/test_af_spec.py -q
.\.venv314\Scripts\python.exe -m pytest tests/ -q          # 190 passed / 5 skipped

# CLI 冒烟
forge spec render examples/ir/case01_day_light.json
forge spec compile examples/ir/case01_day_light.forge -o out.json --nl
```

```bash
# NAS 真 vhass
docker run --rm -v /vol1/1000/docker/autoforge:/app autoforge-test   # 195 passed
```

- 关键验收：
  - `test_render_compile_roundtrip_all_examples`：8 份样例全部 raw 级往返一致 + 渲染幂等。
  - `test_nl_alignment_with_compiled_graph`：往返后 NL 渲染**逐字一致**（"批准的=跑的"）。

## 4. 已知风险 / 残留项

- [ ] 结构化子对象（`trigger`/`expr`/`params`/`canary`）用**内联 JSON** 表达，不是完全的自然语言式 DSL。理由：保证零有损往返；未来若要做"更糖"的表达式中缀语法，可在此之上增量加糖，不影响本层契约。
- [ ] `reserved`（`emit`/`fn` 保留位）节点**拒绝渲染**（属未实现能力，不该出现在可跑图里）；读到即报错，不静默丢弃。
- [ ] 表达式求值语义仍由 `af_ir.expr` 唯一决定；AF-Spec 不引入第二套求值（防语义漂移）。
- [ ] 无多 automation 的 spec 语法糖（每块以 `automation` 开头，天然支持多块）。

## 5. 合并影响

- 既有 IR 样例：全部仍通过 `forge build`；新增 `spec` 子命令不影响既有命令。
- 同步更新：`docs/ROADMAP.md`（G7 → ✅）。
- 部署：无。
- 下游：MA/DB 不受影响；AF-Spec 为 Agent 撰写面，MA 可直接产出 spec 后 compile。

## 6. 回归基线（本里程碑起算）

- 本机：190 passed / 5 skipped / 0 failed
- NAS 真 vhass：195 passed
