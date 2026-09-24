<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number; stroke?: number }>(), { size: 20, stroke: 1.7 })

const PATHS: Record<string, string[]> = {
  flame:   ['M12 3.2c2.9 3.3 4.7 5.6 4.7 8.2a4.7 4.7 0 1 1-9.4 0c0-2.6 1.8-4.9 4.7-8.2Z', 'M12 17.6a2.5 2.5 0 0 1-1.3-4.7c.5-.3 1-.8 1.3-1.4.3.6.8 1.1 1.3 1.4a2.5 2.5 0 0 1-1.3 4.7Z'],
  agent:   ['M5 9a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v5a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4V9Z', 'M9.5 11h.01', 'M14.5 11h.01', 'M12 5V3'],
  bolt:    ['M13.2 3 5.8 13.2h5L10.2 21 18 10.6h-5.2L13.2 3Z'],
  key:     ['M9.5 6.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z', 'M12.5 10h7', 'M17 10v3', 'M19.5 10v2'],
  copy:    ['M8 8h10.5A1.5 1.5 0 0 1 20 9.5V20a1.5 1.5 0 0 1-1.5 1.5H8A1.5 1.5 0 0 1 6.5 20V9.5A1.5 1.5 0 0 1 8 8Z', 'M4.5 16A1.5 1.5 0 0 1 3 14.5V4A1.5 1.5 0 0 1 4.5 2.5H15'],
  pencil:  ['M5 19.5l.9-3.6L15.3 6.5l2.7 2.7-9.4 9.4L5 19.5Z', 'M14 7.8l2.7 2.7'],
  trash:   ['M5 7.5h14', 'M9.5 7.5V5.2h5v2.3', 'M7.2 7.5l.9 12.3h7.8l.9-12.3'],
  check:   ['M5 12.5l4.5 4.5L19 7.5'],
  close:   ['M6.5 6.5l11 11', 'M17.5 6.5l-11 11'],
  chevron: ['M7 10l5 5 5-5'],
  logout:  ['M14.5 8.5V5.5A1.5 1.5 0 0 0 13 4H6a1.5 1.5 0 0 0-1.5 1.5v13A1.5 1.5 0 0 0 6 20h7a1.5 1.5 0 0 0 1.5-1.5v-3', 'M10 12h9.5', 'M17 9.5l2.5 2.5-2.5 2.5'],
  plus:    ['M12 5.5v13', 'M5.5 12h13'],
  sun:     ['M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z', 'M12 1.5v2', 'M12 20.5v2', 'M4.2 4.2l1.4 1.4', 'M18.4 18.4l1.4 1.4', 'M1.5 12h2', 'M20.5 12h2', 'M4.2 19.8l1.4-1.4', 'M18.4 5.6l1.4-1.4'],
  moon:    ['M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z'],
}
</script>

<template>
  <svg
    class="ui-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="stroke"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path v-for="(d, i) in PATHS[name] || []" :key="i" :d="d" />
  </svg>
</template>