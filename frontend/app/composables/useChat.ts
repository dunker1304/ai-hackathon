export function useChat() {
  const config = useRuntimeConfig();
  const answer = ref("");
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function ask(question: string, topK = 5) {
    answer.value = "";
    error.value = null;
    loading.value = true;

    try {
      const res = await fetch(`${config.public.apiBase}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, top_k: topK }),
      });

      if (!res.ok || !res.body) {
        throw new Error(`Request failed: ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        answer.value += decoder.decode(value, { stream: true });
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : "Something went wrong";
    } finally {
      loading.value = false;
    }
  }

  return { answer, loading, error, ask };
}
