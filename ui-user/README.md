# ui-user（已冻结 / 归档）

> ⚠️ **本目录已冻结，停止开发。** 本目录是 AutoForge 控制台的**原始独立前端原型**（原独立仓库 `autoforge-ui`）。
> 自 **v1.7.0（2026-09-17）** 起，前端已并入主仓库主目录 `ui/`，`ui/` 为**唯一活跃开发位置**。

## 为什么冻结

- `ui-user/` 是 v1.7.0 之前的独立前端仓库；合并进主仓后，`ui-user/` 与 `ui/` 功能重复。
- 部署实际使用的是 `ui/dist`（由 `ui/` 构建产出），**不是** `ui-user/dist`。
- 继续在 `ui-user/` 开发会与 `ui/` 分叉，造成重复维护与回归风险。

## 规则

- **新增前端功能 / Bug 修复一律在 `ui/` 进行**，不要动 `ui-user/`。
- `ui-user/` 仅保留作历史归档与回溯；除非出现必须回退到该原型的历史问题，否则不做修改。
- 若需从历史原型取代码，请 cherry-pick 到 `ui/`，不要反向合回 `ui-user/`。

## 目录结构（原型，仅供参考）

- `src/`：Vue 3 + TypeScript 源码（13 `.vue` / 12 `.ts`）
- `public/`：静态资源
- `dist/`：历史构建产物（**已被 `ui/dist` 取代，勿用于部署**）
- 构建见 `package.json` scripts（与 `ui/` 同源 Vite 配置）

## 活跃前端位置

- 源码：`ui/`
- 构建产物：`ui/dist` → NAS `/vol1/1000/docker/autoforge/ui/dist`
- 部署：见 `docker/docker-compose.api.yml`（`forge serve --ui-dir /ui` 同源托管 SPA）
