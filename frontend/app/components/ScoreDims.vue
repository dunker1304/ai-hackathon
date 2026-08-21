<script setup lang="ts">
import { DIM_LABELS, type Dims } from "~/composables/useWeights";

defineProps<{ dims: Dims }>();

function barColor(value: number): string {
  if (value >= 70) return "bg-green-500";
  if (value >= 45) return "bg-amber-500";
  return "bg-red-400";
}
</script>

<template>
  <div class="space-y-2">
    <div v-for="(dim, key) in dims" :key="key" class="flex items-center gap-3 text-sm">
      <span class="w-32 shrink-0 text-gray-600 dark:text-gray-300">
        <EvidencePopover :dim="dim" :label="DIM_LABELS[key] ?? key">{{ DIM_LABELS[key] ?? key }}</EvidencePopover>
      </span>
      <div class="h-2 flex-1 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
        <div class="h-full rounded-full" :class="barColor(dim.value)" :style="{ width: `${dim.value}%` }" />
      </div>
      <span class="w-12 shrink-0 text-right font-mono text-xs">{{ dim.value }}</span>
    </div>
  </div>
</template>
