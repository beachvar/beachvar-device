<script setup lang="ts">
defineProps<{
  title: string
  value: string | number
  unit?: string
  icon: 'cpu' | 'memory' | 'temp' | 'time'
  color: 'blue' | 'purple' | 'orange' | 'green'
}>()

const colorClasses = {
  blue: { bg: 'bg-blue-500/10', text: 'text-blue-400' },
  purple: { bg: 'bg-purple-500/10', text: 'text-purple-400' },
  orange: { bg: 'bg-orange-500/10', text: 'text-orange-400' },
  green: { bg: 'bg-green-500/10', text: 'text-green-400' },
}
</script>

<template>
  <div class="bg-dark-900 rounded-xl p-5 border border-gray-800">
    <div class="flex items-center justify-between mb-3">
      <span class="text-xs font-medium text-gray-500 uppercase tracking-wider">
        {{ title }}
      </span>
      <div
        class="w-8 h-8 rounded-lg flex items-center justify-center"
        :class="colorClasses[color].bg"
      >
        <!-- CPU Icon -->
        <svg
          v-if="icon === 'cpu'"
          class="w-4 h-4"
          :class="colorClasses[color].text"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"
          />
        </svg>
        <!-- Memory Icon -->
        <svg
          v-else-if="icon === 'memory'"
          class="w-4 h-4"
          :class="colorClasses[color].text"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"
          />
        </svg>
        <!-- Temperature Icon -->
        <svg
          v-else-if="icon === 'temp'"
          class="w-4 h-4"
          :class="colorClasses[color].text"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
          />
        </svg>
        <!-- Time Icon -->
        <svg
          v-else-if="icon === 'time'"
          class="w-4 h-4"
          :class="colorClasses[color].text"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
          />
        </svg>
      </div>
    </div>
    <p class="text-2xl font-bold">
      {{ value }}
      <span v-if="unit" class="text-sm font-normal text-gray-500">{{ unit }}</span>
    </p>
  </div>
</template>
