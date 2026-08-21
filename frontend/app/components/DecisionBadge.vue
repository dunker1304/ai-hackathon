<script setup lang="ts">
const props = defineProps<{ decision: string }>();

const styles: Record<string, { label: string; classes: string }> = {
  recommend: { label: "Recommend", classes: "bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300" },
  conditional: { label: "Conditional", classes: "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300" },
  not_recommend: { label: "Not recommend", classes: "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300" },
};

const style = computed(() => styles[props.decision] ?? { label: props.decision, classes: "bg-gray-100 text-gray-700" });
</script>

<template>
  <span class="inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap" :class="style.classes">
    {{ style.label }}
  </span>
</template>
