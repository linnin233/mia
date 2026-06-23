/**
 * 模型能力注册表 — 硬编码的模型能力真值表
 *
 * 职责:
 *   1. 定义每个模型的能力（文本推理、视觉、语音理解、TTS 等）
 *   2. 提供能力查询和校验函数
 *   3. 提供 Provider 工厂函数（根据平台名创建对应的 Provider 实例）
 *
 * 这是系统关于"哪个模型能做什么"的唯一真相来源。
 * 新增模型只需在此文件中添加一条记录即可。
 *
 * 与 Python 版 model_registry.py 保持 1:1 语义映射。
 */

import type { RuntimeConfig } from './config.js';

// ─── 模型能力枚举 ────────────────────────────────────────

/** 模型能力枚举 */
export enum Capability {
  /** 文本对话/推理 */
  TEXT_CHAT = 'text_chat',
  /** 图片/视频理解 */
  VISION = 'vision',
  /** 多模态音频理解（内容+情绪+意图） */
  AUDIO_UNDERSTANDING = 'audio_understanding',
  /** 纯语音识别（语音→文字） */
  ASR = 'asr',
  /** 语音合成（文字→语音） */
  TTS = 'tts',
  /** 流式文本输出 */
  STREAMING = 'streaming',
}

// ─── 模型信息 ────────────────────────────────────────────

/** 模型能力描述 */
export interface ModelInfo {
  /** 所属平台名称 ("mimo" / "deepseek") */
  provider: string;
  /** 该模型具备的能力集合 */
  capabilities: Set<Capability>;
  /** 人类可读的描述文本 */
  desc: string;
}

// ─── 模型注册表 ──────────────────────────────────────────
// 每个模型一条记录

/** 全局模型注册表 */
export const MODEL_REGISTRY: Record<string, ModelInfo> = {
  // ─── MiMo 平台 ─────────────────────────────────
  'mimo-v2.5-pro': {
    provider: 'mimo',
    capabilities: new Set([Capability.TEXT_CHAT, Capability.STREAMING]),
    desc: 'MiMo V2.5 Pro — 旗舰文本模型 (1M context, MoE 1.02T/42B)',
  },
  'mimo-v2.5': {
    provider: 'mimo',
    capabilities: new Set([
      Capability.TEXT_CHAT,
      Capability.VISION,
      Capability.AUDIO_UNDERSTANDING,
      Capability.STREAMING,
    ]),
    desc: 'MiMo V2.5 — 全模态模型 (文本+图片+音频理解, 310B/15B MoE)',
  },
  'mimo-v2.5-asr': {
    provider: 'mimo',
    capabilities: new Set([Capability.ASR]),
    desc: 'MiMo V2.5 ASR — 语音识别 (95%准确率, 支持方言+中英混)',
  },
  'mimo-v2.5-tts': {
    provider: 'mimo',
    capabilities: new Set([Capability.TTS, Capability.STREAMING]),
    desc: 'MiMo V2.5 TTS — 语音合成 (40+语言, 200+音色, 情感表达)',
  },

  // ─── DeepSeek 平台 ─────────────────────────────
  'deepseek-v4-pro': {
    provider: 'deepseek',
    capabilities: new Set([Capability.TEXT_CHAT, Capability.STREAMING]),
    desc: 'DeepSeek V4 Pro — 旗舰推理模型 (1.6T/49B MoE, 1M context)',
  },
  'deepseek-v4-flash': {
    provider: 'deepseek',
    capabilities: new Set([Capability.TEXT_CHAT, Capability.STREAMING]),
    desc: 'DeepSeek V4 Flash — 高吞吐变体 (284B/13B MoE, 1M context)',
  },
};

// ─── 查询函数 ────────────────────────────────────────────

/** 获取指定平台的所有模型 ID 列表 */
export function getModelsByProvider(providerName: string): string[] {
  return Object.entries(MODEL_REGISTRY)
    .filter(([, info]) => info.provider === providerName)
    .map(([modelId]) => modelId);
}

/** 获取所有具备指定能力的模型 ID 列表 */
export function getModelsWithCapability(cap: Capability): string[] {
  return Object.entries(MODEL_REGISTRY)
    .filter(([, info]) => info.capabilities.has(cap))
    .map(([modelId]) => modelId);
}

/**
 * 获取同时具备多个能力的模型 ID 列表（AND 逻辑）
 *
 * @param caps - 需要同时具备的能力集合
 * @returns 满足所有能力的模型 ID 列表
 */
export function getModelsWithAllCapabilities(caps: Set<Capability>): string[] {
  return Object.entries(MODEL_REGISTRY)
    .filter(([, info]) => isSubset(caps, info.capabilities))
    .map(([modelId]) => modelId);
}

/**
 * 校验模型是否具备所需能力，不具备则抛出 Error
 *
 * @param modelId - 模型 ID
 * @param requiredCaps - 该任务需要的能力集合
 * @throws Error 模型不存在或缺少所需能力
 */
export function validateAssignment(
  modelId: string,
  requiredCaps: Set<Capability>,
): void {
  const info = MODEL_REGISTRY[modelId];
  if (!info) {
    throw new Error(`未知模型: ${modelId}`);
  }

  const missing: Capability[] = [];
  for (const cap of requiredCaps) {
    if (!info.capabilities.has(cap)) {
      missing.push(cap);
    }
  }

  if (missing.length > 0) {
    const capsStr = missing.join(', ');
    const hasStr = [...info.capabilities].join(', ');
    throw new Error(
      `模型 ${modelId} 缺少以下能力: ${capsStr}\n` +
      `  该模型具备: ${hasStr}`,
    );
  }
}

/** 获取模型的完整信息，不存在返回 undefined */
export function getModelInfo(modelId: string): ModelInfo | undefined {
  return MODEL_REGISTRY[modelId];
}

// ─── 可用模型筛选（结合 RuntimeConfig） ────────────────────

/**
 * 获取当前可用的模型列表（Key 已配 + 开关已启用）
 *
 * 同时满足两个条件才算"可用":
 *   1. 该模型所属平台的 API Key 已配置
 *   2. 该模型的开关已启用
 *
 * @param runtime - RuntimeConfig 实例
 * @returns 可用模型 ID 列表
 */
export function getAvailableModels(runtime: RuntimeConfig): string[] {
  const available: string[] = [];
  for (const [modelId, info] of Object.entries(MODEL_REGISTRY)) {
    const hasKey = Boolean(
      runtime.provider_api_keys[info.provider],
    );
    const enabled = runtime.model_enabled[modelId] ?? false;
    if (hasKey && enabled) {
      available.push(modelId);
    }
  }
  return available;
}

/**
 * 获取当前可用 + 具备指定能力的模型列表
 *
 * 在 getAvailableModels 的基础上增加能力过滤。
 */
export function getAvailableModelsWithCapability(
  runtime: RuntimeConfig,
  cap: Capability,
): string[] {
  const available = getAvailableModels(runtime);
  return available.filter(
    (mid) => MODEL_REGISTRY[mid]?.capabilities.has(cap),
  );
}

// ─── Provider 工厂 ────────────────────────────────────────

/**
 * 根据平台名创建对应的 Provider 实例（懒加载以避免循环依赖）
 *
 * 同一个平台的多个模型共享一个 Provider 实例。
 * 具体用哪个模型由调用时传入的 model= 参数决定。
 *
 * @param providerName - 平台名 ("mimo" / "deepseek")
 * @param apiKey - 该平台的 API Key
 * @returns MiMoProvider 或 DeepSeekProvider 实例
 * @throws Error 未知平台名
 */
export async function createProvider(
  providerName: string,
  apiKey: string,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
): Promise<any> {
  if (providerName === 'mimo') {
    const { MiMoProvider } = await import('./providers/mimo.js');
    return new MiMoProvider(apiKey);
  }

  if (providerName === 'deepseek') {
    const { DeepSeekProvider } = await import('./providers/deepseek.js');
    return new DeepSeekProvider(apiKey);
  }

  throw new Error(`未知平台: ${providerName}`);
}

/** 获取所有已注册的平台名称（去重） */
export function getAllProviderNames(): string[] {
  const seen = new Set<string>();
  for (const info of Object.values(MODEL_REGISTRY)) {
    seen.add(info.provider);
  }
  return [...seen];
}

// ─── 工具函数 ────────────────────────────────────────────

/** 判断 subset 是否为 superset 的子集 */
function isSubset<T>(subset: Set<T>, superset: Set<T>): boolean {
  for (const elem of subset) {
    if (!superset.has(elem)) return false;
  }
  return true;
}
