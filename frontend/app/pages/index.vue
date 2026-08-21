<script setup lang="ts">
import { marked } from "marked";
import type { Dims } from "~/composables/useWeights";

interface Opportunity {
  product_type_id: string;
  name: string;
  category: string;
  material: string;
  total: number;
  fit: number;
  decision: string;
  dims: Dims;
  computed_at: string;
}

const config = useRuntimeConfig();
const { computeTotal, decide, isDefault } = useWeights();

const { data, status } = await useFetch<{ items: Opportunity[] }>(
  `${config.public.apiBase}/opportunities`,
  { lazy: true, server: false },
);

const category = ref("");
const material = ref("");
const showWeights = ref(false);
const expandedId = ref<string | null>(null);

const categories = computed(() =>
  [...new Set((data.value?.items ?? []).map((i) => i.category))].sort(),
);
const materials = computed(() =>
  [...new Set((data.value?.items ?? []).map((i) => i.material))].sort(),
);

/** Live-recomputed rows: sliders change totals + decisions instantly. */
const rows = computed(() =>
  (data.value?.items ?? [])
    .filter((i) => (!category.value || i.category === category.value))
    .filter((i) => (!material.value || i.material === material.value))
    .map((i) => {
      const total = computeTotal(i.dims);
      return { ...i, liveTotal: total, liveDecision: decide(total, i.fit) };
    })
    .sort((a, b) => b.liveTotal - a.liveTotal),
);

// --- Report modal ---
const reportMd = ref("");
const reportFor = ref("");
const reportLoading = ref(false);

async function openReport(id: string, name: string) {
  reportFor.value = name;
  reportLoading.value = true;
  reportMd.value = "";
  try {
    const res = await $fetch<{ markdown: string }>(`${config.public.apiBase}/report/${id}`);
    reportMd.value = res.markdown;
  } catch {
    reportMd.value = "_Failed to load report._";
  } finally {
    reportLoading.value = false;
  }
}

function downloadReport() {
  const blob = new Blob([reportMd.value], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `report-${reportFor.value.toLowerCase().replace(/\s+/g, "-")}.md`;
  a.click();
  URL.revokeObjectURL(a.href);
}

const reportHtml = computed(() => (reportMd.value ? marked.parse(reportMd.value) : ""));
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-xl font-bold">Discover opportunities</h1>
        <p class="text-sm text-gray-500">
          Ranked by Opportunity Score · fit-gated for manufacturing capability
          <span v-if="!isDefault" class="ml-1 rounded bg-primary-100 px-1.5 py-0.5 text-xs font-medium text-primary-700 dark:bg-primary-900/50 dark:text-primary-300">custom weights</span>
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <select v-model="category" class="rounded-md border border-gray-200 bg-white px-2 py-1.5 text-sm dark:border-gray-700 dark:bg-gray-900">
          <option value="">All categories</option>
          <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
        </select>
        <select v-model="material" class="rounded-md border border-gray-200 bg-white px-2 py-1.5 text-sm dark:border-gray-700 dark:bg-gray-900">
          <option value="">All materials</option>
          <option v-for="m in materials" :key="m" :value="m">{{ m }}</option>
        </select>
        <UButton size="sm" :variant="showWeights ? 'solid' : 'outline'" @click="showWeights = !showWeights">
          ⚖️ Weights
        </UButton>
      </div>
    </div>

    <WeightSliders v-if="showWeights" class="mb-4" />

    <div v-if="status === 'pending'" class="py-16 text-center text-gray-400">Loading opportunities…</div>

    <div v-else class="overflow-x-auto rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-gray-200 text-left text-xs uppercase tracking-wide text-gray-400 dark:border-gray-700">
            <th class="px-4 py-3">#</th>
            <th class="px-4 py-3">Product type</th>
            <th class="px-4 py-3">Category</th>
            <th class="px-4 py-3">Material</th>
            <th class="px-4 py-3 text-right">Score</th>
            <th class="px-4 py-3 text-right">Fit</th>
            <th class="px-4 py-3">Decision</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="(row, idx) in rows" :key="row.product_type_id">
            <tr
              class="cursor-pointer border-b border-gray-100 hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-800/50"
              @click="expandedId = expandedId === row.product_type_id ? null : row.product_type_id"
            >
              <td class="px-4 py-2.5 text-gray-400">{{ idx + 1 }}</td>
              <td class="px-4 py-2.5 font-medium">{{ row.name }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ row.category }}</td>
              <td class="px-4 py-2.5 text-gray-500">{{ row.material }}</td>
              <td class="px-4 py-2.5 text-right font-mono font-semibold">{{ row.liveTotal }}</td>
              <td class="px-4 py-2.5 text-right font-mono" :class="row.fit < 50 ? 'text-red-500' : row.fit < 70 ? 'text-amber-500' : 'text-green-600'">
                {{ row.fit }}
              </td>
              <td class="px-4 py-2.5"><DecisionBadge :decision="row.liveDecision" /></td>
            </tr>
            <tr v-if="expandedId === row.product_type_id" class="border-b border-gray-100 bg-gray-50/50 dark:border-gray-800 dark:bg-gray-800/30">
              <td colspan="7" class="px-6 py-4">
                <div class="mb-3 flex items-center justify-between">
                  <p class="text-xs text-gray-400">
                    Score breakdown — click any dimension for raw values, source, and fetch time
                  </p>
                  <UButton size="xs" variant="outline" @click.stop="openReport(row.product_type_id, row.name)">
                    📄 Research report
                  </UButton>
                </div>
                <ScoreDims :dims="row.dims" />
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <!-- Report modal -->
    <Teleport to="body">
      <div v-if="reportFor" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" @click="reportFor = ''">
        <div class="max-h-[85vh] w-full max-w-3xl overflow-y-auto rounded-xl bg-white p-6 dark:bg-gray-900" @click.stop>
          <div class="mb-4 flex items-center justify-between">
            <h2 class="font-bold">Product Research Report</h2>
            <div class="flex gap-2">
              <UButton size="xs" variant="outline" :disabled="!reportMd" @click="downloadReport">Download .md</UButton>
              <UButton size="xs" variant="ghost" @click="reportFor = ''">✕</UButton>
            </div>
          </div>
          <div v-if="reportLoading" class="py-12 text-center text-gray-400">Generating report…</div>
          <article v-else class="prose prose-sm dark:prose-invert max-w-none [&_table]:text-xs" v-html="reportHtml" />
        </div>
      </div>
    </Teleport>
  </div>
</template>
