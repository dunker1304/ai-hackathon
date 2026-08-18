<script setup lang="ts">
const question = ref("");
const { answer, loading, error, ask } = useChat();

function submit() {
  if (!question.value.trim() || loading.value) return;
  ask(question.value);
}
</script>

<template>
  <main class="wrap">
    <h1>RAG Chat</h1>
    <p class="hint">Ask a question about whatever you ingested via <code>POST /documents</code>.</p>

    <form class="row" @submit.prevent="submit">
      <input
        v-model="question"
        type="text"
        placeholder="Ask something..."
        :disabled="loading"
      />
      <UButton type="submit" :disabled="loading">
        {{ loading ? "Thinking..." : "Ask" }}
      </UButton>
    </form>
    <p v-if="error" class="error">{{ error }}</p>

    <pre v-if="answer" class="answer">{{ answer }}</pre>
  </main>
</template>

<style scoped>
.wrap {
  max-width: 640px;
  margin: 4rem auto;
  padding: 0 1.5rem;
  font-family: ui-sans-serif, system-ui, sans-serif;
  color: #1a1a1a;
}
h1 {
  font-size: 1.5rem;
  margin-bottom: 0.25rem;
}
.hint {
  color: #666;
  font-size: 0.9rem;
  margin-bottom: 1.5rem;
}
.hint code {
  background: #f2f2f2;
  padding: 0.1rem 0.35rem;
  border-radius: 4px;
}
.row {
  display: flex;
  gap: 0.5rem;
}
input {
  flex: 1;
  padding: 0.6rem 0.8rem;
  border: 1px solid #ccc;
  border-radius: 6px;
  font-size: 1rem;
}
button {
  padding: 0.6rem 1.1rem;
  border: none;
  border-radius: 6px;
  background: #1a1a1a;
  color: #fff;
  font-size: 1rem;
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
.error {
  color: #c0392b;
  margin-top: 1rem;
}
.answer {
  margin-top: 1.5rem;
  padding: 1rem;
  background: #f7f7f7;
  border-radius: 8px;
  white-space: pre-wrap;
  line-height: 1.5;
}
</style>
