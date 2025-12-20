<script setup lang="ts">
import { computed } from 'vue'
import { NModal } from 'naive-ui'
import HlsPlayer from './HlsPlayer.vue'
import type { Camera } from '@/types'

const props = defineProps<{
  show: boolean
  camera: Camera | null
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
}>()

const hlsUrl = computed(() => {
  if (!props.camera) return null
  // Local HLS URL pattern: /hls/{camera_id}/stream.m3u8
  return `/hls/${props.camera.id}/stream.m3u8`
})

const modalTitle = computed(() => {
  if (!props.camera) return 'Player'
  return `${props.camera.name} - ${props.camera.court_name}`
})

function copyHlsUrl() {
  if (hlsUrl.value) {
    navigator.clipboard.writeText(window.location.origin + hlsUrl.value)
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="modalTitle"
    :style="{ width: '900px', maxWidth: '95vw' }"
    :mask-closable="true"
    :close-on-esc="true"
    @update:show="emit('update:show', $event)"
  >
    <div v-if="camera && hlsUrl" class="aspect-video bg-black rounded-lg overflow-hidden">
      <HlsPlayer
        :src="hlsUrl"
        :autoplay="true"
        :muted="false"
        :controls="true"
      />
    </div>
    <div v-else class="aspect-video bg-black rounded-lg flex items-center justify-center">
      <p class="text-gray-500">Stream não disponível</p>
    </div>

    <template #footer>
      <div class="flex items-center justify-between">
        <div class="text-sm text-gray-500">
          <span v-if="camera">{{ camera.position }}</span>
        </div>
        <button
          v-if="hlsUrl"
          @click="copyHlsUrl"
          class="px-3 py-1.5 text-sm bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors"
        >
          Copiar Link HLS
        </button>
      </div>
    </template>
  </NModal>
</template>
