/**
 * MIA (Modular Intelligent Agent) — TypeScript 版入口
 *
 * 基于 LLM 循环的多 Agent 系统。
 * TypeScript 重写版本，与 Python 版保持 1:1 语义映射。
 *
 * 用法:
 *   npm run dev                  — 交互式 CLI 模式
 *   npm run dev -- --query "..." — 单次查询模式
 *   npm run dev -- --server 8080 — HTTP API 服务器模式
 *   npm run dev -- --wechat      — 交互式 + 微信通道
 *
 * @module mia
 */

export { MessageBus } from './bus/bus.js';
export {
  MessageType,
  type Message,
  makeUserIntent,
  makeSendText,
  makeSendVoice,
  makeExecuteTask,
  makeTaskResult,
  makeTaskError,
  makeStreamStart,
  makeStreamChunk,
  makeStreamEnd,
  makeTuiThought,
  makeTuiTool,
  makeTuiStatus,
  makeConversationDone,
  makeRawInput,
  makeSystemReady,
  makeSystemShutdown,
} from './bus/message.js';

export { BaseAgent } from './agents/base.js';

export {
  Capability,
  MODEL_REGISTRY,
  type ModelInfo,
  getModelsByProvider,
  getModelsWithCapability,
  getModelsWithAllCapabilities,
  validateAssignment,
  getModelInfo,
  getAvailableModels,
  getAvailableModelsWithCapability,
  createProvider,
  getAllProviderNames,
} from './model-registry.js';

export {
  Config,
  getConfig,
  resetConfig,
  getMiMoBaseUrl,
  createDefaultRuntimeConfig,
  loadMiMoConfig,
  loadDeepSeekConfig,
  loadWeChatConfig,
  loadAgentConfig,
  type MiMoConfig,
  type DeepSeekConfig,
  type WeChatConfig,
  type AgentConfig,
  type RuntimeConfig,
} from './config.js';

/** 包版本号 */
export const VERSION = '0.2.0';
