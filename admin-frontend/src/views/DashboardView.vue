<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { NButton, NEmpty, NSpin } from 'naive-ui'
import StatsCard from '@/components/StatsCard.vue'
import CameraCard from '@/components/CameraCard.vue'
import CameraModal from '@/components/CameraModal.vue'
import CameraLogsModal from '@/components/CameraLogsModal.vue'
import type { SystemInfo, DeviceInfo, Camera } from '@/types'
import { getSystemInfo, getDeviceInfo, getCameras, getCourts } from '@/api/client'

// Data
const systemInfo = ref<SystemInfo | null>(null)
const deviceInfo = ref<DeviceInfo | null>(null)
const cameras = ref<Camera[]>([])
const courts = ref<{ id: string; name: string }[]>([])
const loading = ref(true)
const online = ref(true)

// Modals
const showCameraModal = ref(false)
const showCameraLogsModal = ref(false)
const selectedCamera = ref<Camera | null>(null)

// Computed
const activeStreamsCount = computed(() => cameras.value.filter(c => c.is_streaming).length)
const youtubeCount = computed(() =>
  deviceInfo.value?.youtube_broadcasts.filter(b => b.is_running).length || 0
)
const uptimeFormatted = computed(() => {
  const seconds = systemInfo.value?.uptime_seconds || 0
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)

  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
})

const courtOptions = computed(() => {
  return courts.value.map(c => ({ label: c.name, value: c.id }))
})

// Methods
async function fetchData() {
  try {
    const [system, device, camsData, courtsData] = await Promise.all([
      getSystemInfo(),
      getDeviceInfo(),
      getCameras(),
      getCourts(),
    ])
    systemInfo.value = system
    deviceInfo.value = device
    cameras.value = camsData.cameras
    courts.value = courtsData.courts
    online.value = true
  } catch (error) {
    console.error('Failed to fetch data:', error)
    online.value = false
  } finally {
    loading.value = false
  }
}

function openAddCamera() {
  selectedCamera.value = null
  showCameraModal.value = true
}

function openEditCamera(camera: Camera) {
  selectedCamera.value = camera
  showCameraModal.value = true
}

function openCameraLogs(camera: Camera) {
  selectedCamera.value = camera
  showCameraLogsModal.value = true
}

function openTerminal() {
  window.open(`${window.location.origin}/admin/terminal/`, '_blank')
}

// Lifecycle
let refreshInterval: number | null = null

onMounted(() => {
  fetchData()
  refreshInterval = window.setInterval(fetchData, 5000)
})

onUnmounted(() => {
  if (refreshInterval) {
    clearInterval(refreshInterval)
  }
})
</script>

<template>
  <div class="min-h-screen bg-dark-950 text-gray-100">
    <!-- Sidebar -->
    <aside class="fixed left-0 top-0 h-full w-64 bg-dark-900 border-r border-gray-800 hidden lg:block">
      <div class="p-6 border-b border-gray-800">
        <h1 class="text-xl font-bold">
          <span class="text-primary-400">Beach</span><span class="text-cyan-400">Var</span>
        </h1>
        <p class="text-xs text-gray-500 mt-1">Device Admin Panel</p>
      </div>
      <nav class="p-4">
        <ul class="space-y-2">
          <li>
            <a
              href="#"
              class="flex items-center gap-3 px-4 py-2.5 rounded-lg bg-primary-500/10 text-primary-400 font-medium"
            >
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"
                />
              </svg>
              Dashboard
            </a>
          </li>
          <li>
            <a
              href="#"
              @click.prevent="openTerminal"
              class="flex items-center gap-3 px-4 py-2.5 rounded-lg text-gray-400 hover:bg-gray-800 hover:text-gray-200 transition-colors"
            >
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
                />
              </svg>
              Terminal SSH
            </a>
          </li>
        </ul>
      </nav>
      <div class="absolute bottom-0 left-0 right-0 p-4 border-t border-gray-800">
        <div class="text-xs text-gray-500">
          <p>Device ID</p>
          <p class="text-gray-400 font-mono truncate">
            {{ deviceInfo?.device_id || '--' }}
          </p>
        </div>
      </div>
    </aside>

    <!-- Main Content -->
    <main class="lg:ml-64 min-h-screen">
      <!-- Top Bar -->
      <header class="sticky top-0 z-10 bg-dark-900/80 backdrop-blur-sm border-b border-gray-800">
        <div class="px-6 py-4 flex items-center justify-between">
          <!-- Mobile Logo -->
          <div class="lg:hidden">
            <h1 class="text-lg font-bold">
              <span class="text-primary-400">Beach</span><span class="text-cyan-400">Var</span>
            </h1>
          </div>

          <!-- Device Name -->
          <div class="hidden lg:block">
            <h2 class="text-lg font-semibold text-gray-200">
              {{ deviceInfo?.device_name || '--' }}
            </h2>
            <p class="text-sm text-gray-500">{{ deviceInfo?.complex_name || '' }}</p>
          </div>

          <!-- YouTube Live Badge -->
          <div
            v-if="youtubeCount > 0"
            class="flex items-center gap-2 px-3 py-1.5 rounded-full bg-red-500/10 text-sm"
          >
            <svg class="w-4 h-4 text-red-500" viewBox="0 0 24 24" fill="currentColor">
              <path
                d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"
              />
            </svg>
            <span class="text-red-400 font-medium">{{ youtubeCount }}</span>
            <span class="text-red-400">LIVE</span>
          </div>

          <!-- Status Badge -->
          <div
            class="flex items-center gap-2 px-3 py-1.5 rounded-full text-sm"
            :class="online ? 'bg-green-500/10' : 'bg-red-500/10'"
          >
            <span
              class="w-2 h-2 rounded-full pulse-dot"
              :class="online ? 'bg-green-500' : 'bg-red-500'"
            />
            <span :class="online ? 'text-green-400' : 'text-red-400'">
              {{ online ? 'Online' : 'Offline' }}
            </span>
          </div>

          <!-- Actions -->
          <div class="flex items-center gap-3">
            <NButton quaternary circle @click="fetchData" title="Atualizar">
              <template #icon>
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    stroke-width="2"
                    d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                  />
                </svg>
              </template>
            </NButton>
          </div>
        </div>
      </header>

      <!-- Dashboard Content -->
      <div class="p-6">
        <NSpin :show="loading" class="min-h-[200px]">
          <!-- Stats Grid -->
          <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <StatsCard
              title="CPU"
              :value="systemInfo?.cpu_percent?.toFixed(1) || '--'"
              unit="%"
              icon="cpu"
              color="blue"
            />
            <StatsCard
              title="Memoria"
              :value="systemInfo?.memory_percent?.toFixed(1) || '--'"
              unit="%"
              icon="memory"
              color="purple"
            />
            <StatsCard
              title="Temperatura"
              :value="systemInfo?.temperature?.toFixed(1) || '--'"
              unit="C"
              icon="temp"
              color="orange"
            />
            <StatsCard
              title="Uptime"
              :value="uptimeFormatted"
              icon="time"
              color="green"
            />
          </div>

          <!-- Cameras Section -->
          <div class="mb-6">
            <div class="bg-dark-900 rounded-xl border border-gray-800 overflow-hidden">
              <div class="px-5 py-4 border-b border-gray-800 flex items-center justify-between">
                <div class="flex items-center gap-3">
                  <div class="w-10 h-10 rounded-lg bg-cyan-500/10 flex items-center justify-center">
                    <svg
                      class="w-5 h-5 text-cyan-400"
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
                    <h3 class="font-semibold">Cameras</h3>
                    <p class="text-sm text-gray-500">
                      {{ cameras.length }} registradas, {{ activeStreamsCount }} transmitindo
                    </p>
                  </div>
                </div>
                <div class="flex items-center gap-2">
                  <NButton size="small" @click="openAddCamera">
                    <template #icon>
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path
                          stroke-linecap="round"
                          stroke-linejoin="round"
                          stroke-width="2"
                          d="M12 6v6m0 0v6m0-6h6m-6 0H6"
                        />
                      </svg>
                    </template>
                  </NButton>
                </div>
              </div>
              <div class="divide-y divide-gray-800">
                <NEmpty v-if="cameras.length === 0" description="Nenhuma camera registrada" class="py-8" />
                <CameraCard
                  v-else
                  v-for="camera in cameras"
                  :key="camera.id"
                  :camera="camera"
                  @edit="openEditCamera"
                  @logs="openCameraLogs"
                />
              </div>
            </div>
          </div>

          <!-- Footer -->
          <footer class="mt-8 text-center text-sm text-gray-600">
            <p>
              BeachVar Device v1.0 |
              <span class="font-mono">{{ deviceInfo?.device_id || '--' }}</span>
            </p>
          </footer>
        </NSpin>
      </div>
    </main>

    <!-- Modals -->
    <CameraModal
      v-model:show="showCameraModal"
      :camera="selectedCamera"
      :courts="courtOptions"
      @saved="fetchData"
      @deleted="fetchData"
    />

    <CameraLogsModal
      v-model:show="showCameraLogsModal"
      :camera="selectedCamera"
    />
  </div>
</template>
