---

## 六、service_token 切换 + AF 侧执行结果（2026-10-06 晚）

### 6.1 MA 重签令牌（第三版）

AF 旧令牌 `svc_-xd8ZTmbld1bRur9Ya4KipJFu_7I5Bb0Dnf9L2kWUhM` 因 MA 侧 config 对象与运行态不同步、某次 `save()` 覆盖 `service_tokens` 而丢失 → AF 调用返回 401 `{"ok":false,"error":"未登录"}`。MA 已修复并重签第三版：

| 消费方 | 令牌名 | scope | 明文（第三版） |
|---|---|---|---|
| AF (AutoForge) | `autoforge` | 13 条（butler，含 `POST:/api/metrics/ingest`） | `svc_bBOOEsC7qcXcGFctr3wxz35pBM-FuNTVYAVrtMjToes` |

旧令牌已失效。

### 6.2 AF 侧已执行

1. `.env`（`/vol1/1000/docker/autoforge/.env`）`BUTLER_TOKEN` 更新为第三版新令牌，`MA_METRICS_URL=http://192.168.2.200:8086`。
2. `docker restart autoforge-api`（满足 MA 要求；实际回灌由宿主 cron `docker exec` 调 CLI，不依赖容器内 env）。
3. 新增每日 03:17 cron（`scripts/metrics_push_cron.sh`，IR 源 examples/ir，容器内 `forge metrics push`），用于双轨观察期自动回灌。

### 6.3 验证结果（✅ 已闭环）

MA v4 重签令牌 + **重启容器**后，AF 复测通过（第四版令牌 `svc_-LY8PW7OCk01gLMoyDO-0gsaD1OiNm0r01Ang1I-T3E`）：

- 宿主 `curl -X POST http://192.168.2.200:8086/api/metrics/ingest -H "Authorization: Bearer <v4>" -d '{}'` → **400 `missing automation_id`**（**鉴权通过**，仅业务字段缺失，非 401）
- 容器内 `docker exec autoforge-api python -m autoforge.af_cli metrics push case01_day_light.json --ma-url http://192.168.2.200:8086 --token <v4>` → **`sent=1 / buffered=0`**，MA 接受入库（ok=true）
- `scripts/metrics_push_cron.sh` 真实回灌 18 个 example IR + flush 旧缓冲 → `sent>0`（第四版 `.env` 已 scp 到 NAS，cron 用 `docker exec -e` 注入，不依赖容器重启）

**根因复盘**：前三版失败均为「MA 独立进程签发令牌写入 config.json 后，应用运行态 Config 对象（启动时 `service_tokens` 为空）后续 `config.save()`（节流写盘）覆盖丢失」。第四版在签发后**立即重启 MA 容器**重新加载 config.json，运行态已含 `service_tokens`，故持久生效。这是 MA 侧部署纪律问题，非 AF 契约缺陷。

### 6.4 闭环结论

AF→MA 指标回灌链路已通（MA 端点接受 `svc_` 令牌 → `dedupe_key` 去重 + 离线缓冲兜底）。双轨观察期自动回灌（`scripts/metrics_push_cron.sh`，每日 03:17）已生效，离线缓冲在令牌恢复后已续传清空。

### 6.5 7 天观察期

第四版令牌已生效，进入旧令牌（butler-env / 第三版 `svc_bBOOEsC7qcXcGFctr3wxz35pBM-FuNTVYAVrtMjToes`）零使用观察期；7 天后由 MA 吊销旧令牌。cron 现已 `ok=true`，无积压。

—— AF 开发组 · 2026-10-06 晚（v4 令牌复测 ok=true 更新）
