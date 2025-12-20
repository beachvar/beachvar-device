<script setup lang="ts">
import type { Camera } from '@/types'

defineProps<{
  camera: Camera
}>()

const emit = defineEmits<{
  edit: [camera: Camera]
  logs: [camera: Camera]
  toggleStream: [camera: Camera]
}>()
</script>

<template>
  <div class="px-5 py-4 flex items-center justify-between hover:bg-dark-800/50 transition-colors">
    <div class="flex items-center gap-3">
      <div
        class="w-10 h-10 rounded-lg flex items-center justify-center"
        :class="camera.is_streaming ? 'bg-green-500/10' : 'bg-gray-800'"
      >
        <span
          v-if="camera.is_streaming"
          class="w-3 h-3 rounded-full bg-red-500 pulse-dot"
        />
        <svg
          v-else
          class="w-5 h-5 text-gray-500"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"
          />
        </svg>
      </div>
      <div>
        <p class="font-medium">{{ camera.name }}</p>
        <p class="text-sm text-gray-500">{{ camera.court_name }} - {{ camera.position }}</p>
      </div>
    </div>
    <div class="flex items-center gap-2">
      <span
        class="px-2 py-1 text-xs font-medium rounded-full"
        :class="
          camera.is_streaming
            ? 'bg-green-500/10 text-green-400'
            : 'bg-gray-800 text-gray-500'
        "
      >
        {{ camera.is_streaming ? 'LIVE' : 'Offline' }}
      </span>
      <button
        @click="emit('logs', camera)"
        class="p-2 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors"
        title="Ver logs"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
          />
        </svg>
      </button>
      <button
        @click="emit('edit', camera)"
        class="p-2 rounded-lg hover:bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors"
        title="Editar"
      >
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z"
          />
        </svg>
      </button>
    </div>
  </div>
</template>
