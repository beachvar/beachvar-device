export interface SystemInfo {
  hostname: string
  platform: string
  architecture: string
  python_version: string
  device_id: string
  temperature: number | null
  cpu_percent: number
  memory_percent: number
  uptime_seconds: number
}

export interface DeviceInfo {
  device_id: string
  device_name: string
  complex_name: string | null
  complex_id: string | null
  youtube_broadcasts: YouTubeBroadcast[]
}

export interface YouTubeBroadcast {
  id: string
  camera_id: string
  camera_name: string
  is_running: boolean
}

export interface Stream {
  live_input_id: string
  playback_hls: string
  playback_dash: string
}

export interface Camera {
  id: string
  name: string
  rtsp_url: string
  position: string
  court_id: string
  court_name: string
  complex_id: string
  complex_name: string
  has_stream: boolean
  stream_mode: string | null
  stream: Stream | null
  is_streaming: boolean
}

export interface CameraCreateDTO {
  name: string
  rtsp_url: string
  court_id: string
  position: string
}

export interface CameraUpdateDTO {
  name?: string
  rtsp_url?: string
  position?: string
}

export interface LogEntry {
  timestamp: string
  message: string
  level: 'info' | 'warning' | 'error'
}

export interface Court {
  id: string
  name: string
}
