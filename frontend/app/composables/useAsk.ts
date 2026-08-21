export interface AskToolCall {
  name: string;
  args: Record<string, unknown>;
}

export interface AskChatMessage {
  role: "user" | "assistant";
  content: string;
  tools: AskToolCall[];
  error?: string;
}

/** NDJSON stream reader for POST /ask (events: tool | token | error | done). */
export function useAsk() {
  const config = useRuntimeConfig();
  const messages = ref<AskChatMessage[]>([]);
  const loading = ref(false);

  async function send(question: string) {
    if (!question.trim() || loading.value) return;
    const history = messages.value
      .filter((m) => !m.error)
      .map((m) => ({ role: m.role, content: m.content }));

    messages.value = [
      ...messages.value,
      { role: "user", content: question, tools: [] },
      { role: "assistant", content: "", tools: [] },
    ];
    loading.value = true;

    const current = () => messages.value[messages.value.length - 1]!;

    try {
      const res = await fetch(`${config.public.apiBase}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, history }),
      });
      if (!res.ok || !res.body) throw new Error(`Request failed: ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const rawLine of lines) {
          if (!rawLine.trim()) continue;
          const event = JSON.parse(rawLine);
          if (event.type === "tool") {
            current().tools = [...current().tools, { name: event.name, args: event.args }];
          } else if (event.type === "token") {
            current().content += event.content;
          } else if (event.type === "error") {
            current().error = event.message;
          }
        }
      }
    } catch (e) {
      current().error = e instanceof Error ? e.message : "Something went wrong";
    } finally {
      loading.value = false;
    }
  }

  return { messages, loading, send };
}
