<script setup lang="ts">
import { marked } from "marked";

const { messages, loading, send } = useAsk();
const question = ref("");

const SUGGESTIONS = [
  "What are the top 5 opportunities right now?",
  "What should we launch for Christmas in wood?",
  "Compare metal tumblers vs ceramic mugs",
  "Is 'Custom Pet Memorial Suncatcher' in our catalog?",
  "Why does acrylic ornament score so high?",
  "Generate a research report for acrylic-ornament",
];

function submit(q?: string) {
  const value = (q ?? question.value).trim();
  if (!value) return;
  question.value = "";
  send(value);
}

function render(md: string): string {
  return marked.parse(md) as string;
}

const chatEl = ref<HTMLElement>();
watch(
  () => messages.value.map((m) => m.content.length).join(),
  () => {
    nextTick(() => chatEl.value?.scrollTo({ top: chatEl.value.scrollHeight }));
  },
);
</script>

<template>
  <div class="mx-auto flex h-[calc(100vh-8rem)] max-w-3xl flex-col">
    <h1 class="text-xl font-bold">Ask the copilot</h1>
    <p class="mb-4 text-sm text-gray-500">
      Answers come from deterministic tools over live data — every number is traceable, and every answer ends with a recommendation.
    </p>

    <div ref="chatEl" class="flex-1 space-y-4 overflow-y-auto pb-4">
      <div v-if="!messages.length" class="flex flex-wrap gap-2 pt-4">
        <button
          v-for="s in SUGGESTIONS"
          :key="s"
          class="rounded-full border border-gray-200 px-3 py-1.5 text-xs text-gray-500 hover:border-primary-400 hover:text-primary-600 dark:border-gray-700"
          type="button"
          @click="submit(s)"
        >
          {{ s }}
        </button>
      </div>

      <div v-for="(m, i) in messages" :key="i">
        <div v-if="m.role === 'user'" class="flex justify-end">
          <div class="max-w-[80%] rounded-2xl rounded-br-sm bg-primary-500 px-4 py-2 text-sm text-white">
            {{ m.content }}
          </div>
        </div>
        <div v-else class="space-y-2">
          <div v-if="m.tools.length" class="flex flex-wrap gap-1.5">
            <span
              v-for="(t, ti) in m.tools"
              :key="ti"
              class="rounded-full bg-violet-100 px-2 py-0.5 font-mono text-[11px] text-violet-700 dark:bg-violet-900/50 dark:text-violet-300"
              :title="JSON.stringify(t.args)"
            >
              🔧 {{ t.name }}
            </span>
          </div>
          <div v-if="m.error" class="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-300">
            {{ m.error }}
          </div>
          <article
            v-else-if="m.content"
            class="prose prose-sm dark:prose-invert max-w-none rounded-2xl rounded-bl-sm border border-gray-200 bg-white px-4 py-3 dark:border-gray-700 dark:bg-gray-900"
            v-html="render(m.content)"
          />
          <div v-else-if="loading && i === messages.length - 1" class="px-2 text-sm text-gray-400">
            Thinking…
          </div>
        </div>
      </div>
    </div>

    <form class="flex gap-2 border-t border-gray-200 pt-3 dark:border-gray-800" @submit.prevent="submit()">
      <input
        v-model="question"
        type="text"
        placeholder="Ask about products, niches, launch timing…"
        class="flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:border-primary-500 focus:outline-none dark:border-gray-700 dark:bg-gray-900"
        :disabled="loading"
      />
      <UButton type="submit" :loading="loading">Send</UButton>
    </form>
  </div>
</template>
