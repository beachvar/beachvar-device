<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import Hls from 'hls.js'

const props = defineProps<{
  src: string
  autoplay?: boolean
  muted?: boolean
  controls?: boolean
}>()

const videoRef = ref<HTMLVideoElement | null>(null)
const isLoading = ref(true)
const hasError = ref(false)
const errorMessage = ref('')

let hls: Hls | null = null

function initPlayer() {
  if (!videoRef.value || !props.src) return

  isLoading.value = true
  hasError.value = false
  errorMessage.value = ''

  // Cleanup previous instance
  if (hls) {
    hls.destroy()
    hls = null
  }

  if (Hls.isSupported()) {
    hls = new Hls({
      enableWorker: true,
      lowLatencyMode: true,
      backBufferLength: 30,
    })

    hls.loadSource(props.src)
    hls.attachMedia(videoRef.value)

    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      isLoading.value = false
      if (props.autoplay && videoRef.value) {
        videoRef.value.play().catch(() => {
          // Autoplay blocked, user needs to interact
        })
      }
    })

    hls.on(Hls.Events.ERROR, (_, data) => {
      if (data.fatal) {
        hasError.value = true
        switch (data.type) {
          case Hls.ErrorTypes.NETWORK_ERROR:
            errorMessage.value = 'Erro de rede. Verifique a conexão.'
            // Try to recover
            hls?.startLoad()
            break
          case Hls.ErrorTypes.MEDIA_ERROR:
            errorMessage.value = 'Erro de mídia.'
            hls?.recoverMediaError()
            break
          default:
            errorMessage.value = 'Erro ao carregar stream.'
            break
        }
      }
    })
  } else if (videoRef.value.canPlayType('application/vnd.apple.mpegurl')) {
    // Safari native HLS support
    videoRef.value.src = props.src
    videoRef.value.addEventListener('loadedmetadata', () => {
      isLoading.value = false
      if (props.autoplay && videoRef.value) {
        videoRef.value.play().catch(() => {})
      }
    })
  } else {
    hasError.value = true
    errorMessage.value = 'Seu navegador não suporta HLS.'
  }
}

function retry() {
  initPlayer()
}

watch(() => props.src, () => {
  initPlayer()
})

onMounted(() => {
  initPlayer()
})

onUnmounted(() => {
  if (hls) {
    hls.destroy()
    hls = null
  }
})
</script>

<template>
  <div class="relative w-full h-full bg-black">
    <!-- Video Element -->
    <video
      ref="videoRef"
      class="w-full h-full object-contain"
      :muted="muted"
      :controls="controls"
      playsinline
    />

    <!-- Loading Overlay -->
    <div
      v-if="isLoading && !hasError"
      class="absolute inset-0 flex items-center justify-center bg-black/50"
    >
      <div class="flex flex-col items-center gap-3">
        <div class="w-10 h-10 border-4 border-primary-500 border-t-transparent rounded-full animate-spin" />
        <span class="text-sm text-gray-400">Carregando stream...</span>
      </div>
    </div>

    <!-- Error Overlay -->
    <div
      v-if="hasError"
      class="absolute inset-0 flex items-center justify-center bg-black/80"
    >
      <div class="flex flex-col items-center gap-4 text-center px-4">
        <svg class="w-12 h-12 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
          />
        </svg>
        <p class="text-gray-300">{{ errorMessage }}</p>
        <button
          @click="retry"
          class="px-4 py-2 bg-primary-500 hover:bg-primary-600 text-white rounded-lg transition-colors"
        >
          Tentar novamente
        </button>
      </div>
    </div>
  </div>
</template>
