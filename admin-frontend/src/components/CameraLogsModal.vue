<script setup lang="ts">
import { ref, watch, nextTick, onUnmounted } from 'vue'
import { NModal, NButton, NSpace, NSpin, NEmpty, NTag, useMessage } from 'naive-ui'
import type { Camera, LogEntry } from '@/types'
import { getCameraLogs, clearCameraLogs, subscribeToCameraLogs } from '@/api/client'

const props = defineProps<{
  show: boolean
  camera: Camera | null
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
}>()

const message = useMessage()
const logs = ref<LogEntry[]>([])
const loading = ref(false)
const streaming = ref(false)
const logsContainer = ref<HTMLElement | null>(null)
let eventSource: EventSource | null = null

function scrollToBottom() {
  nextTick(() => {
    if (logsContainer.value) {
      logsContainer.value.scrollTop = logsContainer.value.scrollHeight
    }
  })
}

async function loadLogs() {
  if (!props.camera) return

  loading.value = true
  try {
    const { logs: fetchedLogs } = await getCameraLogs(props.camera.id)
    logs.value = fetchedLogs
    scrollToBottom()
  } catch (error) {
    console.error('Failed to load logs:', error)
  } finally {
    loading.value = false
  }
}

function startStreaming() {
  if (!props.camera || streaming.value) return

  streaming.value = true
  eventSource = subscribeToCameraLogs(
    props.camera.id,
    (log) => {
      logs.value.push(log)
      // Keep only last 500 logs in UI
      if (logs.value.length > 500) {
        logs.value = logs.value.slice(-500)
      }
      scrollToBottom()
    },
    () => {
      streaming.value = false
    }
  )
}

function stopStreaming() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
  streaming.value = false
}

async function handleClear() {
  if (!props.camera) return

  try {
    await clearCameraLogs(props.camera.id)
    logs.value = []
    message.success('Logs limpos')
  } catch (error) {
    message.error('Erro ao limpar logs')
  }
}

function getLogColor(level: string): 'default' | 'warning' | 'error' {
  switch (level) {
    case 'error':
      return 'error'
    case 'warning':
      return 'warning'
    default:
      return 'default'
  }
}

function formatTime(timestamp: string): string {
  const date = new Date(timestamp)
  return date.toLocaleTimeString('pt-BR')
}

watch(
  () => props.show,
  (show) => {
    if (show && props.camera) {
      loadLogs()
      startStreaming()
    } else {
      stopStreaming()
      logs.value = []
    }
  }
)

onUnmounted(() => {
  stopStreaming()
})
</script>

<template>
  <NModal
    :show="show"
    @update:show="emit('update:show', $event)"
    preset="card"
    :title="`Logs - ${camera?.name || ''}`"
    :style="{ width: '800px', maxHeight: '80vh' }"
  >
    <div class="flex flex-col h-[500px]">
      <!-- Header -->
      <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2">
          <NTag :type="camera?.is_streaming ? 'success' : 'default'" size="small">
            {{ camera?.is_streaming ? 'Streaming' : 'Parado' }}
          </NTag>
          <NTag v-if="streaming" type="info" size="small">
            <span class="flex items-center gap-1">
              <span class="w-2 h-2 bg-blue-500 rounded-full pulse-dot" />
              Tempo real
            </span>
          </NTag>
        </div>
        <NSpace>
          <NButton size="small" @click="loadLogs" :loading="loading">
            Atualizar
          </NButton>
          <NButton size="small" type="warning" ghost @click="handleClear">
            Limpar
          </NButton>
        </NSpace>
      </div>

      <!-- Logs -->
      <div
        ref="logsContainer"
        class="flex-1 overflow-auto bg-dark-950 rounded-lg p-4 font-mono text-sm"
      >
        <NSpin v-if="loading" class="flex justify-center items-center h-full" />
        <NEmpty v-else-if="logs.length === 0" description="Nenhum log disponivel" />
        <div v-else class="space-y-1">
          <div
            v-for="(log, index) in logs"
            :key="index"
            class="flex gap-2 items-start"
          >
            <span class="text-gray-500 shrink-0">{{ formatTime(log.timestamp) }}</span>
            <NTag :type="getLogColor(log.level)" size="tiny" class="shrink-0">
              {{ log.level.toUpperCase() }}
            </NTag>
            <span
              :class="{
                'text-red-400': log.level === 'error',
                'text-yellow-400': log.level === 'warning',
                'text-gray-300': log.level === 'info',
              }"
            >
              {{ log.message }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="flex justify-end">
        <NButton @click="emit('update:show', false)">Fechar</NButton>
      </div>
    </template>
  </NModal>
</template>
