/**
 * MIA Ink TUI — 全局状态管理 (useReducer)
 */

import type {
  TuiState,
  ChatMessage,
  ThoughtEntry,
  ToolCallEntry,
  MemoryEntry,
} from './types.js';

// ─── Actions ────────────────────────────────────────────

export type TuiAction =
  | { type: 'ADD_USER_MESSAGE'; content: string; id: string }
  | { type: 'SET_STREAMING'; text: string }
  | { type: 'APPEND_STREAMING'; delta: string }
  | { type: 'FINISH_STREAMING'; content: string }
  | { type: 'SET_PROCESSING'; value: boolean }
  | { type: 'ADD_THOUGHT'; entry: ThoughtEntry }
  | { type: 'ADD_TOOL_CALL'; entry: ToolCallEntry }
  | { type: 'UPDATE_TOOL_CALL'; id: string; result: string; status: 'success' | 'error' }
  | { type: 'SET_MEMORIES'; entries: MemoryEntry[] }
  | { type: 'TOGGLE_PANEL'; panel: 'thinking' | 'tools' | 'memory' }
  | { type: 'SET_STATUS'; model?: string; memoryCount?: number; sessionId?: string }
  | { type: 'SET_FULLSCREEN'; mode: 'chat' | 'memory' }
  | { type: 'SET_MEMORY_PAGE'; page: number }
  | { type: 'CLEAR_CHAT' };

// ─── Initial State ──────────────────────────────────────

export function createInitialState(): TuiState {
  return {
    messages: [],
    streamingText: '',
    isProcessing: false,
    thoughts: [],
    toolCalls: [],
    memories: [],
    panels: {
      thinking: true,
      tools: true,
      memory: true,
    },
    status: {
      model: 'mimo-v2.5-pro',
      memoryCount: 0,
      sessionId: '',
    },
    fullscreenMode: 'chat',
    memoryPage: 0,
  };
}

// ─── Reducer ────────────────────────────────────────────

export function tuiReducer(state: TuiState, action: TuiAction): TuiState {
  switch (action.type) {
    case 'ADD_USER_MESSAGE': {
      const msg: ChatMessage = {
        id: action.id,
        role: 'user',
        content: action.content,
        timestamp: Date.now(),
      };
      return { ...state, messages: [...state.messages, msg] };
    }

    case 'SET_STREAMING':
      return { ...state, streamingText: action.text, isProcessing: true };

    case 'APPEND_STREAMING':
      return { ...state, streamingText: state.streamingText + action.delta };

    case 'FINISH_STREAMING': {
      const msg: ChatMessage = {
        id: Date.now().toString(16),
        role: 'assistant',
        content: action.content,
        timestamp: Date.now(),
      };
      return {
        ...state,
        messages: [...state.messages, msg],
        streamingText: '',
        isProcessing: false,
      };
    }

    case 'SET_PROCESSING':
      return { ...state, isProcessing: action.value };

    case 'ADD_THOUGHT':
      return {
        ...state,
        thoughts: [...state.thoughts.slice(-19), action.entry], // 保留最近20条
      };

    case 'ADD_TOOL_CALL':
      return {
        ...state,
        toolCalls: [...state.toolCalls.slice(-19), action.entry],
      };

    case 'UPDATE_TOOL_CALL':
      return {
        ...state,
        toolCalls: state.toolCalls.map((tc) =>
          tc.id === action.id
            ? { ...tc, result: action.result, status: action.status }
            : tc,
        ),
      };

    case 'SET_MEMORIES':
      return { ...state, memories: action.entries };

    case 'TOGGLE_PANEL':
      return {
        ...state,
        panels: {
          ...state.panels,
          [action.panel]: !state.panels[action.panel],
        },
      };

    case 'SET_STATUS':
      return {
        ...state,
        status: {
          ...state.status,
          ...(action.model !== undefined && { model: action.model }),
          ...(action.memoryCount !== undefined && { memoryCount: action.memoryCount }),
          ...(action.sessionId !== undefined && { sessionId: action.sessionId }),
        },
      };

    case 'SET_FULLSCREEN':
      return { ...state, fullscreenMode: action.mode };

    case 'SET_MEMORY_PAGE':
      return { ...state, memoryPage: action.page };

    case 'CLEAR_CHAT':
      return {
        ...state,
        messages: [],
        streamingText: '',
        isProcessing: false,
      };

    default:
      return state;
  }
}
