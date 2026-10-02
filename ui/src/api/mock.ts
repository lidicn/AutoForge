import type {
  HealthResponse, GraphListResponse, GraphResponse, BuildResponse,
  SimResponse, ConfResponse, DiffResponse, SpecCompileResponse, FaultsResponse,
} from '../types/api'
import study_day_light from '../../examples/ir/study_day_light.json'
import study_leave_light from '../../examples/ir/study_leave_light.json'
import night_desk_lamp from '../../examples/ir/night_desk_lamp.json'
import study_ask_ac from '../../examples/ir/study_ask_ac.json'
import study_ask_light from '../../examples/ir/study_ask_light.json'

const IR_BY_NAME: Record<string, GraphResponse> = {
  study_day_light: study_day_light as GraphResponse,
  study_leave_light: study_leave_light as GraphResponse,
  night_desk_lamp: night_desk_lamp as GraphResponse,
  study_ask_ac: study_ask_ac as GraphResponse,
  study_ask_light: study_ask_light as GraphResponse,
}

function delay(ms = 50) { return new Promise(r => setTimeout(r, ms)) }

export const mockData = {
  health: {
    ok: true,
    version: '0.2.1',
    milestones: ['G1', 'G2', 'G3', 'G4', 'G5', '真机接线', 'G6', 'G7'],
  } as HealthResponse,

  graphs: {
    items: [
      { name: 'study_day_light', latest_version: 3, saved_at: '2026-09-14T08:00:00Z', note: '白天人在补光', mode: 'restart', automation_ids: ['study_day_light'] },
      { name: 'study_leave_light', latest_version: 2, saved_at: '2026-09-14T07:30:00Z', note: '人离10分钟关灯', mode: 'restart', automation_ids: ['study_leave_light'] },
      { name: 'night_desk_lamp', latest_version: 1, saved_at: '2026-09-13T20:00:00Z', note: '夜晚开电脑则开挂灯', mode: 'single', automation_ids: ['night_desk_lamp'] },
      { name: 'study_ask_ac', latest_version: 2, saved_at: '2026-09-13T18:00:00Z', note: '闷了询问是否开空调', mode: 'single', automation_ids: ['study_ask_ac'] },
      { name: 'study_ask_light', latest_version: 1, saved_at: '2026-09-12T10:00:00Z', note: '询问中打断不回滚', mode: 'single', automation_ids: ['study_ask_light'] },
    ],
  } as GraphListResponse,

  confidences: {
    items: [
      { automation_id: 'study_day_light', confidence: 0.92, band: 'auto' },
      { automation_id: 'study_leave_light', confidence: 0.78, band: 'shadow' },
      { automation_id: 'night_desk_lamp', confidence: 0.45, band: 'ask' },
      { automation_id: 'study_ask_ac', confidence: 0.88, band: 'auto' },
      { automation_id: 'study_ask_light', confidence: 0.62, band: 'shadow' },
    ],
    thresholds: { auto: 0.85, shadow_low: 0.60 },
  } as ConfResponse,

  faults: {
    kinds: [
      { value: 'unavailable', label: '实体掉线（unavailable）', inject_layer: 'state', expected_handler: 'on_error 软失效 + 漂移告警' },
      { value: 'timeout', label: '网络/传输层超时', inject_layer: 'adapter', expected_handler: 'on_error（单次失败，不重试）' },
      { value: 'reorder', label: '事件乱序', inject_layer: 'event', expected_handler: '去重/熔断兜底，最终状态收敛' },
      { value: 'drop', label: '消息丢包', inject_layer: 'adapter', expected_handler: 'on_error（指令未达设备）' },
      { value: 'drift', label: '状态漂移', inject_layer: 'state', expected_handler: 'canary 检测漂移 + 自动回滚 + 审计' },
    ],
    failures: [
      { key: 'device', label: '设备故障', edge: 'on_error', recoverable: false },
      { key: 'timeout', label: '超时', edge: 'on_timeout', recoverable: true },
      { key: 'cancel', label: '中断取消', edge: 'on_cancel', recoverable: true },
      { key: 'reject', label: '业务拒绝', edge: 'no|default', recoverable: true },
    ],
  } as FaultsResponse,
}

function loadIR(name: string): GraphResponse {
  return IR_BY_NAME[name] ?? {
    name: '', version: 0, ir: { ir_version: '', id: '', name: '', version: 0, mode: 'single', snapshot: false, meta: {}, nodes: [], edges: [] },
    nl: '', diagnostics: [],
  }
}

export const mockApi = {
  health: () => delay(80).then(() => ({ data: mockData.health })),
  graphs: () => delay(100).then(() => ({ data: mockData.graphs })),
  graph: (name: string) => delay(120).then(() => ({ data: loadIR(name) })),
  build: (ir: unknown) => delay(150).then(() => {
    const automation = (ir as { id?: string })?.id ?? 'test'
    return {
      data: {
        ok: true,
        errors: automation === 'dangerous_delete_all'
          ? [{ code: 'L3_ACTION', level: 'error', message: '高危动作（HTTP POST to external）', automation_id: automation, node_id: 'd1' }]
          : [],
        warnings: automation === 'study_ask_ac'
          ? [{ code: 'L2_NEEDS_CONFIRM', level: 'warning', message: 'L2动作建议使用canary+人工确认', automation_id: automation, node_id: 'd1' }]
          : [],
        nl: `当条件满足时，执行对应动作。`,
      } as BuildResponse,
    }
  }),
  sim: (_ir: unknown, _seed?: Record<string, string>, _events?: unknown[]) =>
    delay(200).then(() => ({
      data: {
        instances: [
          { instance_id: 'uuid-001', state: 'done', current_node: 'p1', trace: [
            { node: 'a1', at: '2026-09-14T08:00:00Z' },
            { node: 'i1', at: '2026-09-14T08:00:01Z' },
            { node: 'd1', at: '2026-09-14T08:00:02Z' },
            { node: 'p1', at: '2026-09-14T08:00:03Z' },
          ]},
        ],
        audit: [
          { type: 'action_failed', entity_id: 'light.study_main', message: '设备响应正常', at: '2026-09-14T08:00:02Z' },
        ],
        bus: {},
        final_states: { 'light.study_main': 'on' },
        nl: '仿真完成：检测到有人，光照不足，主灯已开启。',
      } as SimResponse,
    })),
  conf: (name: string) => delay(80).then(() => {
    const target = mockData.confidences.items.find(i => i.automation_id === name)
    return { data: { items: target ? [target] : mockData.confidences.items, thresholds: mockData.confidences.thresholds } } as { data: ConfResponse }
  }),
  intervene: (_name: string, automation_id: string) => delay(100).then(() => ({
    data: { ...mockData.confidences, items: mockData.confidences.items.map(i => i.automation_id === automation_id ? { ...i, confidence: Math.max(0, i.confidence - 0.25) } : i) } as ConfResponse,
  })),
  diff: (_name: string, old: string, new_v: string) => delay(100).then(() => ({
    data: {
      render: `--- study_day_light v1\n+++ study_day_light v2\n@@ -nodes @@\n+  { id: "d1", kind: "do", name: "新增动作节点" }\n`,
      // 真后端原样回显版本号并带两版备注，夹具必须同形
      old: Number(old),
      new: Number(new_v),
      notes: { old: '首次归档', new: '新增动作节点' },
      structured: {
        added_automations: [], removed_automations: [],
        added_nodes: [{ node_id: 'study_day_light:d1', address: 'study_day_light:d1', automation_id: 'study_day_light', node: 'd1' }],
        removed_nodes: [],
        changed_nodes: [],
        added_edges: [],
        removed_edges: [],
        meta_changes: [],
      },
    } as DiffResponse,
  })),
  spec: (name: string) => delay(80).then(() => ({
    data: { spec: `automation ${name}\nname "${name}"\nir_version "0.2.1"\nversion 1\nmode single\n\non a1 {"type": "state", "entity_id": "binary_sensor.study_motion", "to": "on"}\nif i1 {"op": "lt", "left": {"var": "entity.sensor.study_illum", "type": "numeric"}, "right": {"const": 200}}\ndo d1 ha.light.turn_on {"entity_id": "light.study_main"}\npass p1\n\nedge a1 -> i1 then\nedge i1 -> d1 then\nedge i1 -> p1 no\nedge d1 -> p1 then\nedge d1 -> p1 on_error\n` },
  })),
  specCompile: (text: string) => delay(150).then(() => ({
    data: {
      ok: true,
      ir: {
        ir_version: '0.2.1', id: 'compiled_auto', name: '编译后的自动化',
        version: 1, mode: 'single', snapshot: true, meta: {},
        nodes: [
          { id: 'a1', kind: 'on', name: '手动触发', trigger: { type: 'state', entity_id: 'binary_sensor.study_motion', to: 'on' } },
          { id: 'd1', kind: 'do', name: '开灯', adapter: 'ha', action: 'light.turn_on', params: { entity_id: 'light.study_main' } },
          { id: 'p1', kind: 'pass', name: '结束' },
        ],
        edges: [
          { from: 'a1', to: 'd1', kind: 'then' }, { from: 'd1', to: 'p1', kind: 'then' },
          { from: 'd1', to: 'p1', kind: 'on_error' },
        ],
      },
      nl: text || '当书房运动传感器检测到有人时，打开书房主灯。',
      diagnostics: [
        { code: 'L2_NEEDS_CONFIRM', level: 'warning', message: 'L2动作建议使用canary+人工确认', automation_id: 'compiled_auto', node_id: 'd1' },
      ],
    } as SpecCompileResponse,
  })),
  faults: () => delay(80).then(() => ({ data: mockData.faults })),
}
