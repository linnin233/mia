/**
 * MIA Ink TUI — 入口
 *
 * 启动 Ink 渲染器，挂载 App 组件。
 */

export { App } from './app.js';
export { tuiReducer, createInitialState } from './store.js';
export type { TuiState, ChatMessage, ThoughtEntry, ToolCallEntry, MemoryEntry } from './types.js';
