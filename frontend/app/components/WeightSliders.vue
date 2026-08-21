<script setup lang="ts">
import { DIM_LABELS } from "~/composables/useWeights";

const { weights, reset, isDefault } = useWeights();
</script>

<template>
  <div class="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
    <div class="mb-3 flex items-center justify-between">
      <h3 class="text-sm font-semibold">Score weights</h3>
      <UButton v-if="!isDefault" size="xs" variant="ghost" @click="reset">Reset</UButton>
    </div>
    <div class="space-y-3">
      <div v-for="(label, key) in DIM_LABELS" :key="key" class="flex items-center gap-3 text-sm">
        <span class="w-32 shrink-0 text-gray-600 dark:text-gray-300">{{ label }}</span>
        <input
          v-model.number="weights[key]"
          type="range"
          min="0"
          max="0.5"
          step="0.01"
          class="h-1.5 flex-1 cursor-pointer accent-primary-500"
        />
        <span class="w-10 shrink-0 text-right font-mono text-xs">{{ Math.round((weights[key] ?? 0) * 100) }}%</span>
      </div>
    </div>
    <p class="mt-3 text-xs text-gray-400">
      Totals and decisions update instantly — scores are transparent, not a black box.
    </p>
  </div>
</template>
