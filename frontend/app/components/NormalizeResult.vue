<script setup lang="ts">
interface Candidate {
  id: string;
  name: string;
  similarity: number;
}

interface NormalizeData {
  status: string;
  product_type: {
    id: string;
    name: string;
    category: string;
    material: string;
    difficulty: number;
    fit: number;
  } | null;
  confidence: number;
  reasoning: string;
  evidence_span: string;
  candidates: Candidate[];
  signature: Record<string, unknown>;
}

const props = defineProps<{ result: NormalizeData; title: string }>();

const confidenceStyle = computed(() => {
  if (props.result.confidence >= 0.8) return "bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300";
  if (props.result.confidence >= 0.55) return "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300";
  return "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300";
});

/** Highlight evidence words inside the original title. */
const highlightedTitle = computed(() => {
  const words = props.result.evidence_span
    .toLowerCase()
    .split(/[^a-z0-9-]+/)
    .filter((w) => w.length > 2);
  return props.title
    .split(/(\s+)/)
    .map((part) => {
      const clean = part.toLowerCase().replace(/[^a-z0-9-]/g, "");
      return words.includes(clean)
        ? `<mark class="rounded bg-yellow-200 px-0.5 dark:bg-yellow-700/60">${part}</mark>`
        : part;
    })
    .join("");
});
</script>

<template>
  <div class="rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-900">
    <p class="mb-3 text-sm text-gray-500" v-html="highlightedTitle" />

    <div v-if="result.status === 'matched' && result.product_type" class="space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <span class="text-lg font-semibold">{{ result.product_type.name }}</span>
        <span class="rounded-full px-2.5 py-0.5 text-xs font-semibold" :class="confidenceStyle">
          {{ Math.round(result.confidence * 100) }}% confidence
        </span>
      </div>
      <div class="flex flex-wrap gap-4 text-sm text-gray-600 dark:text-gray-300">
        <span><span class="text-gray-400">Category:</span> {{ result.product_type.category }}</span>
        <span><span class="text-gray-400">Material:</span> {{ result.product_type.material }}</span>
        <span><span class="text-gray-400">Difficulty:</span> {{ result.product_type.difficulty }}/5</span>
        <span><span class="text-gray-400">Fit:</span> {{ result.product_type.fit }}/100</span>
      </div>
      <p class="text-sm text-gray-500 italic">{{ result.reasoning }}</p>
    </div>

    <div v-else class="space-y-2">
      <div class="flex items-center gap-2">
        <span class="text-lg font-semibold text-red-600 dark:text-red-400">Out of catalog</span>
        <span class="rounded-full px-2.5 py-0.5 text-xs font-semibold" :class="confidenceStyle">
          {{ Math.round(result.confidence * 100) }}% confidence
        </span>
      </div>
      <p class="text-sm text-gray-500">
        {{ result.reasoning }} We report this honestly instead of forcing a wrong match.
      </p>
    </div>

    <details v-if="result.candidates.length" class="mt-4">
      <summary class="cursor-pointer text-xs font-medium text-gray-400 hover:text-gray-600">
        Why? Top candidates considered ({{ result.candidates.length }})
      </summary>
      <div class="mt-2 space-y-1">
        <div
          v-for="c in result.candidates"
          :key="c.id"
          class="flex items-center justify-between rounded bg-gray-50 px-2 py-1 text-xs dark:bg-gray-800"
        >
          <span>{{ c.name }} <span class="text-gray-400">({{ c.id }})</span></span>
          <span class="font-mono">{{ (c.similarity * 100).toFixed(0) }}%</span>
        </div>
      </div>
    </details>
  </div>
</template>
