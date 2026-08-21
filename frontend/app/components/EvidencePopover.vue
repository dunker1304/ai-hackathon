<script setup lang="ts">
import type { DimEntry } from "~/composables/useWeights";

defineProps<{ dim: DimEntry; label: string }>();
const open = ref(false);
</script>

<template>
  <span class="relative inline-block">
    <button
      class="cursor-pointer border-b border-dotted border-gray-400 text-left hover:text-primary-600 dark:hover:text-primary-400"
      type="button"
      :title="`Show evidence for ${label}`"
      @click.stop="open = !open"
    >
      <slot />
    </button>
    <Teleport to="body">
      <div v-if="open" class="fixed inset-0 z-40" @click="open = false" />
    </Teleport>
    <div
      v-if="open"
      class="absolute left-0 top-full z-50 mt-1 w-80 rounded-lg border border-gray-200 bg-white p-3 text-xs shadow-xl dark:border-gray-700 dark:bg-gray-900"
      @click.stop
    >
      <p class="mb-2 font-semibold">{{ label }}</p>
      <p class="mb-2 text-gray-600 dark:text-gray-300">{{ dim.explanation }}</p>
      <table class="w-full">
        <thead>
          <tr class="text-left text-gray-400">
            <th class="pb-1 pr-2 font-medium">Metric</th>
            <th class="pb-1 pr-2 font-medium">Value</th>
            <th class="pb-1 pr-2 font-medium">Source</th>
            <th class="pb-1 font-medium">Fetched</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ev in dim.evidence" :key="ev.metric" class="border-t border-gray-100 dark:border-gray-800">
            <td class="py-1 pr-2">{{ ev.metric }}</td>
            <td class="py-1 pr-2 font-mono">{{ ev.value.toLocaleString() }}</td>
            <td class="py-1 pr-2">{{ ev.source }}</td>
            <td class="py-1 whitespace-nowrap">{{ ev.fetched_at.slice(0, 16) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </span>
</template>
