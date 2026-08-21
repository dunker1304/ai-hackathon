<script setup lang="ts">
const links = [
  { to: "/", label: "Discover" },
  { to: "/analyze", label: "Analyze" },
  { to: "/compare", label: "Compare" },
  { to: "/ask", label: "Ask" },
];
</script>

<template>
  <div class="min-h-screen bg-gray-50 dark:bg-gray-950">
    <header class="sticky top-0 z-30 border-b border-gray-200 bg-white/80 backdrop-blur dark:border-gray-800 dark:bg-gray-900/80">
      <div class="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
        <div class="flex items-center gap-6">
          <NuxtLink to="/" class="text-sm font-bold tracking-tight">
            🎯 Product Opportunity Hub
          </NuxtLink>
          <nav class="flex gap-1">
            <NuxtLink
              v-for="link in links"
              :key="link.to"
              :to="link.to"
              class="rounded-md px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
              active-class="bg-gray-100 font-semibold text-gray-900 dark:bg-gray-800 dark:text-white"
            >
              {{ link.label }}
            </NuxtLink>
          </nav>
        </div>
        <FreshnessBadge />
      </div>
    </header>
    <main class="mx-auto max-w-6xl px-4 py-6">
      <slot />
    </main>
  </div>
</template>
