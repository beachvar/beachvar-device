<script setup lang="ts">
import type { Button } from '@/types'

defineProps<{
  button: Button
}>()

const emit = defineEmits<{
  edit: [button: Button]
}>()
</script>

<template>
  <div class="px-5 py-4 flex items-center justify-between hover:bg-dark-800/50 transition-colors">
    <div class="flex items-center gap-3">
      <div
        class="w-10 h-10 rounded-lg flex items-center justify-center"
        :class="button.is_active ? 'bg-rose-500/10' : 'bg-gray-800'"
      >
        <span
          class="text-sm font-bold"
          :class="button.is_active ? 'text-rose-400' : 'text-gray-500'"
        >
          {{ button.button_number }}
        </span>
      </div>
      <div>
        <p class="font-medium">{{ button.label || `Botao ${button.button_number}` }}</p>
        <p class="text-sm text-gray-500">GPIO {{ button.gpio_pin }}</p>
      </div>
    </div>
    <div class="flex items-center gap-2">
      <span
        class="px-2 py-1 text-xs font-medium rounded-full"
        :class="
          button.is_active
            ? 'bg-green-500/10 text-green-400'
            : 'bg-gray-800 text-gray-500'
        "
      >
        {{ button.is_active ? 'Ativo' : 'Inativo' }}
      </span>
      <button
        @click="emit('edit', button)"
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
