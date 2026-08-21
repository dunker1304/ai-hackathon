<script setup lang="ts">
import type { Dims } from "~/composables/useWeights";

const config = useRuntimeConfig();

const title = ref("");
const submitted = ref("");
const result = ref<any>(null);
const loading = ref(false);
const error = ref("");

const EXAMPLES = [
  "Personalized Grandpa Gift For Father's Day From Granddaughter Acrylic Ornament",
  "Custom Pet Photo Fleece Blanket Dog Mom Christmas",
  "Stainless Steel Tumbler 20oz Engraved For Dad",
  "Handmade resin dice set for D&D",
];

interface Opportunity {
  product_type_id: string;
  dims: Dims;
  total: number;
  fit: number;
  decision: string;
}

const { data: opps } = await useFetch<{ items: Opportunity[] }>(
  `${config.public.apiBase}/opportunities`,
  { lazy: true, server: false },
);

const matchedScore = computed(() => {
  const id = result.value?.product_type?.id;
  if (!id) return null;
  return opps.value?.items.find((i) => i.product_type_id === id) ?? null;
});

async function analyze(t?: string) {
  const value = (t ?? title.value).trim();
  if (!value || loading.value) return;
  title.value = value;
  submitted.value = value;
  loading.value = true;
  error.value = "";
  result.value = null;
  try {
    result.value = await $fetch(`${config.public.apiBase}/normalize`, {
      method: "POST",
      body: { title: value },
    });
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Normalization failed";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="mx-auto max-w-3xl">
    <h1 class="text-xl font-bold">Analyze a listing</h1>
    <p class="mb-4 text-sm text-gray-500">
      Paste any Etsy/Amazon listing title — we normalize it to the Printway catalog and score the opportunity.
    </p>

    <form class="mb-3 flex gap-2" @submit.prevent="analyze()">
      <input
        v-model="title"
        type="text"
        placeholder="e.g. Personalized Grandpa Gift For Father's Day From Granddaughter"
        class="flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:border-primary-500 focus:outline-none dark:border-gray-700 dark:bg-gray-900"
      />
      <UButton type="submit" :loading="loading">Analyze</UButton>
    </form>

    <div class="mb-6 flex flex-wrap gap-2">
      <button
        v-for="ex in EXAMPLES"
        :key="ex"
        class="rounded-full border border-gray-200 px-3 py-1 text-xs text-gray-500 hover:border-primary-400 hover:text-primary-600 dark:border-gray-700"
        type="button"
        @click="analyze(ex)"
      >
        {{ ex.slice(0, 48) }}{{ ex.length > 48 ? "…" : "" }}
      </button>
    </div>

    <div v-if="loading" class="py-12 text-center text-gray-400">Normalizing…</div>
    <div v-else-if="error" class="rounded-lg bg-red-50 p-4 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-300">{{ error }}</div>

    <template v-else-if="result">
      <NormalizeResult :result="result" :title="submitted" />

      <div v-if="matchedScore" class="mt-4 rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-900">
        <div class="mb-3 flex items-center justify-between">
          <h3 class="text-sm font-semibold">Opportunity Score</h3>
          <div class="flex items-center gap-3">
            <span class="font-mono text-2xl font-bold">{{ matchedScore.total }}</span>
            <DecisionBadge :decision="matchedScore.decision" />
          </div>
        </div>
        <ScoreDims :dims="matchedScore.dims" />
        <p class="mt-3 text-xs text-gray-400">
          Fit gate: {{ matchedScore.fit }}/100 — fit below 50 blocks any recommendation regardless of market score.
        </p>
      </div>
    </template>
  </div>
</template>
