<script setup lang="ts">
const config = useRuntimeConfig();
const open = ref(false);

interface Freshness {
  sources: { source: string; fetched_at: string; count: number }[];
  scores_computed_at: string | null;
}

const { data } = await useFetch<Freshness>(`${config.public.apiBase}/meta/freshness`, {
  lazy: true,
  server: false,
});

function hoursAgo(ts: string): string {
  const diff = Date.now() - new Date(ts.replace(" ", "T")).getTime();
  const hours = Math.max(Math.floor(diff / 3_600_000), 0);
  return hours === 0 ? "<1h ago" : `${hours}h ago`;
}

const label = computed(() => {
  if (!data.value?.sources.length) return "No data";
  const newest = data.value.sources
    .map((s) => s.fetched_at)
    .sort()
    .at(-1)!;
  return `Updated ${hoursAgo(newest)} · ${data.value.sources.length} sources`;
});
</script>

<template>
  <div class="relative">
    <button
      class="flex items-center gap-1.5 rounded-full border border-gray-200 px-3 py-1 text-xs text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
      type="button"
      @click="open = !open"
    >
      <span class="h-2 w-2 rounded-full bg-green-500" />
      {{ label }}
    </button>
    <div
      v-if="open && data"
      class="absolute right-0 top-full z-50 mt-1 w-72 rounded-lg border border-gray-200 bg-white p-3 text-xs shadow-xl dark:border-gray-700 dark:bg-gray-900"
    >
      <p class="mb-2 font-semibold">Data freshness</p>
      <div v-for="s in data.sources" :key="s.source" class="flex justify-between border-t border-gray-100 py-1 dark:border-gray-800">
        <span>{{ s.source }}</span>
        <span class="text-gray-400">{{ s.count.toLocaleString() }} rows · {{ hoursAgo(s.fetched_at) }}</span>
      </div>
    </div>
  </div>
</template>
