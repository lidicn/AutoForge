# AutoForge ↔ Memory-Agent 指标回灌契约（v0.5.0）

> 主题：AF 执行结果回流 MA，形成"假设 → 验证 → 校准"闭环。
> AutoForge 侧实现：`af_metrics.py`（MetricsAggregator + Ingester）。
> MA 侧实现：`POST /api/metrics/ingest`（复用 butler 白名单 + Bearer 鉴权）。

## 1. 推送端点

```
POST {MA_BASE}/api/metrics/ingest
Authorization: Bearer <BUTLER_TOKEN>
Content-Type: application/json
```

- 鉴权与现有 butler 白名单一致（`app.py` 的 `BUTLER_ENDPOINTS` 增加此路径）。
- 每条请求**只承载单个自动化在一个时间窗口的指标**（见 §3 `dedupe_key`）。
- 响应 `2xx` 视为成功；`4xx`/`5xx`/超时视为失败 → AF 侧落离线缓冲并重试。

## 2. 单条 metric payload

```json
{
  "dedupe_key": "study_day_light:2026-09-15T10",
  "automation_id": "study_day_light",
  "window": "2026-09-15T10",
  "generated_at": "2026-09-15T10:00:00",
  "runs": 12,
  "success": 11,
  "failed": 1,
  "success_rate": 0.9167,
  "audit_distribution": { "action_failed": 1, "event_emitted": 3 },
  "confidence": { "value": 0.92, "band": "auto" },
  "last_event_at": "2026-09-15T09:55:00"
}
```

字段说明：

| 字段 | 类型 | 含义 |
|---|---|---|
| `dedupe_key` | str | 幂等键 = `{automation_id}:{window}` |
| `automation_id` | str | 自动化 id |
| `window` | str | 小时窗口 `YYYY-MM-DDTHH`（生成时间截断到小时） |
| `generated_at` | str | 快照生成时间 ISO |
| `runs` / `success` / `failed` | int | 执行次数 / 成功 / 失败 |
| `success_rate` | float\|null | 成功率（`success/(success+failed)`，无样本为 null） |
| `audit_distribution` | obj | 各类审计事件计数（键见 `af_audit.ALL_EVENT_TYPES`） |
| `confidence` | obj | `{value, band}`，band ∈ `auto`/`shadow`/`ask`（null 表示无置信度） |
| `last_event_at` | str\|null | 该自动化最近一次审计事件时间 |

## 3. 幂等（去重）

AF 侧每条 metric 自带 `dedupe_key`（自动化 + 小时窗口）。MA 端按 `dedupe_key`
做幂等：同一 key 重复到达时**覆盖/合并最新快照**，不重复累计。窗口粒度=小时，
避免高频重复推送造成放大。

## 4. MA 侧查询

`GET /api/metrics`（限 butler 白名单）返回已接收的指标聚合，供 MA 的
"自动化健康度"类洞察消费。MA 可自由决定落盘（`{data}/af_metrics.json`）或内存聚合。

## 5. 失败处理（AF 侧）

- **退避**：单条推送失败按 2ⁿ 秒指数退避（封顶 30s，默认最多 5 次）。
- **离线缓冲**：仍失败则追加到 `{buffer_dir}/metrics_buffer.jsonl`（每行一条 JSON）。
- **恢复续传**：`Ingester.flush_buffer()` 重发缓冲，成功即删、失败保留 →
  断网不丢、恢复续传。

## 6. 配置

| 环境变量 | 用途 | 默认 |
|---|---|---|
| `MA_METRICS_URL` | MA 接收端点基址 | — |
| `BUTLER_TOKEN` | butler 鉴权令牌 | — |

CLI：`forge metrics push --ma-url <url> --token <tok> [--dry-run] [--buffer-dir <dir>]`。
