# AutoForge v2 四件套 · 设计稿（MiMo 生成 · 待评审）

本目录收 AutoForge v2 四件套的 MiMo 生成设计稿，由 Lever-Hub 管线投喂 + 无头 harvest 回收得到（技能：`~/.codebuddy/skills/lever-hub`）。

> 设计种子，非成品代码。**不自动合入 AutoForge**（等 `v1.10.0` 投产收口后，经评审再落地）。当前不在 09-30 变更窗口内。

## 四篇

| 篇 | 文件 | MiMo hex | 状态 |
| --- | --- | --- | --- |
| 1.1 首演码仪式试演期 | `1.1-首演码仪式试演期.md` | `9ac06bfe4acb40baad502f1a299a254d` | success |
| 1.2 结构化 Ask 协议 | `1.2-结构化Ask协议.md` | `75366c43a5c20514cd098822f33750f0` | success |
| 1.3 AF-Spec 封闭词表 forbid | `1.3-AFSpec封闭词表forbid.md` | `8a5968e2bb9803b015327419cd8d60df` | success |
| 1.5 诚实报告分层 | `1.5-诚实报告分层.md` | `0287b18f1dbe187f0ddee833b45cfd1e` | success |

四篇均 `dialogStatus: success`、无拒答、`diskMatch: true`（落盘自证通过）。

## 阅读顺序建议
1. **1.1 首演码** —— 定义「首演码」仪式与试演期，是 v2 引入的新治理机制，先读以建立全局语境。
2. **1.3 AF-Spec forbid** —— 封闭词表硬性约束（forbid 集合），是首演码/评审的硬门。
3. **1.2 结构化 Ask** —— 混沌态下的结构化提问协议，决定下游如何向模型要「可验收」的产物。
4. **1.5 诚实报告** —— 分层诚实报告（能力/边界/不确定），收口「模型对自知」的承诺。

## 来源
- 需求稿：`E:\NAS\Lever-Hub\需求稿\AutoForge-*.md`
- 生成件原始落点：`E:\NAS\Lever-Hub\out-alt/<hex>.md`
- 投喂/回收纪律：技能 `lever-hub` 的 `references/gotchas.md` §7（自动投喂可靠流程）。

## 路线图（评审入口）
- **[实施路线图_v2.0.md](实施路线图_v2.0.md)** —— 四篇设计种子合成为 4 个里程碑（M1 首演码 → M2 AF-Spec forbid → M3 结构化 Ask → M4 诚实报告分层），含顺序、依赖、接口契约、读真仓补全前置、验收门、风险与部署闸门。**落地前先读此图。**
