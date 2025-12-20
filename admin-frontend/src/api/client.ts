import axios from 'axios'
import type {
  SystemInfo,
  DeviceInfo,
  Camera,
  CameraCreateDTO,
  CameraUpdateDTO,
  LogEntry,
} from '@/types'

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

// System & Device
export async function getSystemInfo(): Promise<SystemInfo> {
  const { data } = await api.get<SystemInfo>('/system')
  return data
}

export async function getDeviceInfo(): Promise<DeviceInfo> {
  const { data } = await api.get<DeviceInfo>('/device-info')
  return data
}

export async function restartDevice(): Promise<void> {
  await api.post('/restart')
}

// Cameras
export async function getCameras(): Promise<{ cameras: Camera[]; total: number }> {
  const { data } = await api.get<{ cameras: Camera[]; total: number }>('/cameras/')
  return data
}

export async function getCamera(id: string): Promise<Camera> {
  const { data } = await api.get<Camera>(`/cameras/${id}`)
  return data
}

export async function createCamera(camera: CameraCreateDTO): Promise<Camera> {
  const { data } = await api.post<Camera>('/cameras/', camera)
  return data
}

export async function updateCamera(id: string, camera: CameraUpdateDTO): Promise<Camera> {
  const { data } = await api.patch<Camera>(`/cameras/${id}`, camera)
  return data
}

export async function deleteCamera(id: string): Promise<void> {
  await api.delete(`/cameras/${id}`)
}

// Streams
export async function startStream(cameraId: string): Promise<void> {
  await api.post(`/streams/${cameraId}/start`)
}

export async function stopStream(cameraId: string): Promise<void> {
  await api.post(`/streams/${cameraId}/stop`)
}

// Logs
export async function getCameraLogs(cameraId: string): Promise<{ logs: LogEntry[] }> {
  const { data } = await api.get<{ logs: LogEntry[] }>(`/cameras/${cameraId}/logs`)
  return data
}

export async function clearCameraLogs(cameraId: string): Promise<void> {
  await api.delete(`/cameras/${cameraId}/logs`)
}

// Courts
export async function getCourts(): Promise<{ courts: { id: string; name: string }[] }> {
  const { data } = await api.get<{ courts: { id: string; name: string }[] }>('/courts/')
  return data
}

export function subscribeToCameraLogs(
  cameraId: string,
  onMessage: (log: LogEntry) => void,
  onError?: (error: Event) => void
): EventSource {
  const eventSource = new EventSource(`/api/cameras/${cameraId}/logs/stream`)

  eventSource.onmessage = (event) => {
    try {
      const log = JSON.parse(event.data) as LogEntry
      onMessage(log)
    } catch (e) {
      console.error('Failed to parse log:', e)
    }
  }

  eventSource.onerror = (error) => {
    if (onError) {
      onError(error)
    }
  }

  return eventSource
}
