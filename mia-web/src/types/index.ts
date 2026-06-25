export interface SessionInfo {
  session_id: string
  name: string
  source: 'cli' | 'wechat' | 'telegram' | 'api'
  created_at: string
  updated_at: string
  turn_count: number
  is_active: boolean
}

export interface ChannelStatus {
  enabled: boolean
  has_token: boolean
}

export interface ChannelsResponse {
  wechat: ChannelStatus
  telegram: ChannelStatus
}

export interface MemoryEntry {
  id: string
  content: string
  category: string
  confidence: number
  keywords: string[]
  importance: number
  source_sessions: string[]
  created_at: string
}

export interface MemoryResponse {
  total: number
  page: number
  page_size: number
  entries: MemoryEntry[]
}

export interface ConfigSummary {
  scheduler: { model: string; fallback: string }
  task: { model: string; fallback: string }
  memory: { model: string; fallback: string }
  receiver: { text_model: string; vision_enabled: boolean; audio_enabled: boolean }
  sender: { tts_enabled: boolean; tts_model: string }
  streaming: boolean
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

export interface ChatResponse {
  response: string
}

export interface SSEChunk {
  text?: string
  done?: boolean
  error?: string
}
