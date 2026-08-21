<script setup lang="ts">
import { DIM_LABELS, type Dims } from "~/composables/useWeights";

const config = useRuntimeConfig();
const { computeTotal, decide } = useWeights();

interface Opportunity {
  product_type_id: string;
  name: string;
  category: string;
  material: string;
  total: number;
  fit: number;
  decision: string;
  dims: Dims;
}

const { data } = await useFetch<{ items: Opportunity[] }>(
  `${config.public.apiBase}/opportunities`,
  { lazy: true, server: false },
);

const selected = ref<string[]>([]);

function toggle(id: string) {
  if (selected.value.includes(id)) {
    selected.value = selected.value.filter((s) => s !== id);
  } else if (selected.value.length < 3) {
    selected.value = [...selected.value, id];
  }
}

const chosen = computed(() =>
  (data.value?.items ?? [])
    .filter((i) => selected.value.includes(i.product_type_id))
    .map((i) => {
      const total = computeTotal(i.dims);
      return { ...i, liveTotal: total, liveDecision: decide(total, i.fit) };
    }),
);

function isBest(key: string, value: number): boolean {
  return chosen.value.length > 1 && value === Math.max(...chosen.value.map((c) => c.dims[key]?.value ?? 0));
}

const bestOverall = computed(() =>
  chosen.value.length > 1 ? chosen.value.reduce((a, b) => (a.liveTotal >= b.liveTotal ? a : b)) : null,
);
</script>

<template>
  <div>
    <h1 class="text-xl font-bold">Compare niches</h1>
    <p class="mb-4 text-sm text-gray-500">Pick 2–3 product types to compare dimension by dimension.</p>

    <div class="mb-6 flex flex-wrap gap-2">
      <button
        v-for="i in data?.items ?? []"
        :key="i.product_type_id"
        class="rounded-full border px-3 py-1 text-xs"
        :class="
          selected.includes(i.product_type_id)
            ? 'border-primary-500 bg-primary-50 font-semibold text-primary-700 dark:bg-primary-900/40 dark:text-primary-300'
            : 'border-gray-200 text-gray-500 hover:border-gray-400 dark:border-gray-700'
        "
        type="button"
        @click="toggle(i.product_type_id)"
      >
        {{ i.name }}
      </button>
    </div>

    <div v-if="chosen.length >= 2" class="overflow-x-auto rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-gray-200 text-left dark:border-gray-700">
            <th class="px-4 py-3 text-xs uppercase tracking-wide text-gray-400">Dimension</th>
            <th v-for="c in chosen" :key="c.product_type_id" class="px-4 py-3">
              <div class="font-semibold">{{ c.name }}</div>
              <div class="text-xs font-normal text-gray-400">{{ c.material }} · {{ c.category }}</div>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(label, key) in DIM_LABELS" :key="key" class="border-b border-gray-100 dark:border-gray-800">
            <td class="px-4 py-2.5 text-gray-500">{{ label }}</td>
            <td
              v-for="c in chosen"
              :key="c.product_type_id"
              class="px-4 py-2.5 font-mono"
              :class="isBest(key, c.dims[key]?.value ?? 0) ? 'font-bold text-green-600 dark:text-green-400' : ''"
            >
              {{ c.dims[key]?.value ?? "–" }}
            </td>
          </tr>
          <tr class="border-b border-gray-100 bg-gray-50/60 dark:border-gray-800 dark:bg-gray-800/40">
            <td class="px-4 py-2.5 font-semibold">Total score</td>
            <td v-for="c in chosen" :key="c.product_type_id" class="px-4 py-2.5 font-mono text-base font-bold">
              {{ c.liveTotal }}
            </td>
          </tr>
          <tr class="border-b border-gray-100 dark:border-gray-800">
            <td class="px-4 py-2.5 text-gray-500">Manufacturing fit</td>
            <td v-for="c in chosen" :key="c.product_type_id" class="px-4 py-2.5 font-mono">{{ c.fit }}</td>
          </tr>
          <tr>
            <td class="px-4 py-2.5 text-gray-500">Decision</td>
            <td v-for="c in chosen" :key="c.product_type_id" class="px-4 py-2.5">
              <DecisionBadge :decision="c.liveDecision" />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-if="bestOverall" class="mt-4 rounded-lg bg-green-50 p-3 text-sm text-green-800 dark:bg-green-900/30 dark:text-green-300">
      🏆 <strong>{{ bestOverall.name }}</strong> leads with {{ bestOverall.liveTotal }} at current weights
      ({{ bestOverall.liveDecision === "recommend" ? "recommended" : bestOverall.liveDecision }}).
    </p>

    <p v-else-if="chosen.length < 2" class="py-8 text-center text-sm text-gray-400">
      Select at least 2 product types above.
    </p>
  </div>
</template>
