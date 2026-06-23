/**
 * 配置管理模块 — 从环境变量 / .env 文件加载所有配置
 *
 * 使用 zod + dotenv 实现类型安全的配置，
 * 支持 .env 文件自动加载和环境变量覆盖。
 */

import { z } from 'zod';
import dotenv from 'dotenv';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';

// ─── 加载 .env 文件 ────────────────────────────────────────
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const _projectRoot = path.resolve(__dirname, '..');
const _envFile = path.join(_projectRoot, '.env');
if (fs.existsSync(_envFile)) {
  dotenv.config({ path: _envFile });
}

// ─── Zod Schemas ──────────────────────────────────────────

/** MiMo API 配置 Schema */
const MiMoConfigSchema = z.object({
  api_key: z.string().default(''),
  base_url: z.string().default(''),
  chat_model: z.string().default('mimo-v2.5-pro'),
  vision_model: z.string().default('mimo-v2.5'),
  asr_model: z.string().default('mimo-v2.5-asr'),
  tts_model: z.string().default('mimo-v2.5-tts'),
  default_voice: z.string().default('冰糖'),
});

/** DeepSeek API 配置 Schema */
const DeepSeekConfigSchema = z.object({
  api_key: z.string().default(''),
  base_url: z.string().default('https://api.deepseek.com/v1'),
  chat_model: z.string().default('deepseek-chat'),
});

/** WeChat iLink Bot 渠道配置 Schema */
const WeChatConfigSchema = z.object({
  enabled: z.boolean().default(false),
  bot_token: z.string().default(''),
  bot_token_file: z.string().default(''),
  base_url: z.string().default('https://ilinkai.weixin.qq.com'),
  media_dir: z.string().default(''),
});

/** Agent 行为配置 Schema */
const AgentConfigSchema = z.object({
  scheduler_max_iterations: z.coerce.number().int().default(10),
  scheduler_task_timeout: z.coerce.number().int().default(60),
  scheduler_loop_timeout: z.coerce.number().int().default(120),
  scheduler_max_consecutive_tasks: z.coerce.number().int().default(3),
  workspace_dir: z.string().default(path.join(_projectRoot, 'workspace')),
  memory_history_turns: z.coerce.number().int().default(5),
  memory_max_working_entries: z.coerce.number().int().default(30),
  memory_extraction_timeout: z.coerce.number().default(8.0),
  enable_streaming: z.coerce.boolean().default(true),
  verbose: z.coerce.boolean().default(true),
});

// ─── 类型导出 ────────────────────────────────────────────

export type MiMoConfig = z.infer<typeof MiMoConfigSchema>;
export type DeepSeekConfig = z.infer<typeof DeepSeekConfigSchema>;
export type WeChatConfig = z.infer<typeof WeChatConfigSchema>;
export type AgentConfig = z.infer<typeof AgentConfigSchema>;

// ─── 环境变量加载函数 ─────────────────────────────────────

/** 从环境变量加载 MiMo 配置 */
export function loadMiMoConfig(): MiMoConfig {
  return MiMoConfigSchema.parse({
    api_key: process.env['MIMO_API_KEY'] ?? '',
    base_url: process.env['MIMO_BASE_URL'] ?? '',
    chat_model: process.env['MIMO_CHAT_MODEL'] ?? 'mimo-v2.5-pro',
    vision_model: process.env['MIMO_VISION_MODEL'] ?? 'mimo-v2.5',
    asr_model: process.env['MIMO_ASR_MODEL'] ?? 'mimo-v2.5-asr',
    tts_model: process.env['MIMO_TTS_MODEL'] ?? 'mimo-v2.5-tts',
    default_voice: process.env['MIMO_DEFAULT_VOICE'] ?? '冰糖',
  });
}

/** 从环境变量加载 DeepSeek 配置 */
export function loadDeepSeekConfig(): DeepSeekConfig {
  return DeepSeekConfigSchema.parse({
    api_key: process.env['DEEPSEEK_API_KEY'] ?? '',
    base_url: process.env['DEEPSEEK_BASE_URL'] ?? 'https://api.deepseek.com/v1',
    chat_model: process.env['DEEPSEEK_CHAT_MODEL'] ?? 'deepseek-chat',
  });
}

/** 从环境变量加载 WeChat 配置 */
export function loadWeChatConfig(): WeChatConfig {
  return WeChatConfigSchema.parse({
    enabled: process.env['MIA_WECHAT_ENABLED'] === 'true',
    bot_token: process.env['MIA_WECHAT_BOT_TOKEN'] ?? '',
    bot_token_file: process.env['MIA_WECHAT_BOT_TOKEN_FILE'] ?? '',
    base_url: process.env['MIA_WECHAT_BASE_URL'] ?? 'https://ilinkai.weixin.qq.com',
    media_dir: process.env['MIA_WECHAT_MEDIA_DIR'] ?? '',
  });
}

/** 从环境变量加载 Agent 配置 */
export function loadAgentConfig(): AgentConfig {
  return AgentConfigSchema.parse({
    scheduler_max_iterations: process.env['MIA_SCHEDULER_MAX_ITERATIONS'] ?? '10',
    scheduler_task_timeout: process.env['MIA_SCHEDULER_TASK_TIMEOUT'] ?? '60',
    scheduler_loop_timeout: process.env['MIA_SCHEDULER_LOOP_TIMEOUT'] ?? '120',
    scheduler_max_consecutive_tasks: process.env['MIA_SCHEDULER_MAX_CONSECUTIVE_TASKS'] ?? '3',
    workspace_dir: process.env['MIA_WORKSPACE_DIR'] ?? path.join(_projectRoot, 'workspace'),
    memory_history_turns: process.env['MIA_MEMORY_HISTORY_TURNS'] ?? '5',
    memory_max_working_entries: process.env['MIA_MEMORY_MAX_WORKING_ENTRIES'] ?? '30',
    memory_extraction_timeout: process.env['MIA_MEMORY_EXTRACTION_TIMEOUT'] ?? '8.0',
    enable_streaming: process.env['MIA_ENABLE_STREAMING'] !== 'false',
    verbose: process.env['MIA_VERBOSE'] !== 'false',
  });
}

/**
 * 获取 MiMo API Base URL，自动识别 key 类型:
 *   - tp- 开头 → Token Plan 网关 (token-plan-cn.xiaomimimo.com)
 *   - sk- 开头 → 按量付费网关 (api.xiaomimimo.com)
 */
export function getMiMoBaseUrl(config: MiMoConfig): string {
  if (config.base_url) return config.base_url;
  if (config.api_key.startsWith('tp-')) {
    return 'https://token-plan-cn.xiaomimimo.com/v1';
  }
  return 'https://api.xiaomimimo.com/v1';
}

// ─── RuntimeConfig — 运行时可变配置 ────────────────────────

/**
 * 运行时可变配置 — 由斜杠命令 (/model /agent /channel) 在运行时修改
 *
 * 与 env 静态配置不同，RuntimeConfig 支持在交互会话中动态修改，
 * 不会被环境变量覆盖。
 */
export interface RuntimeConfig {
  /** 平台级 API Key（一个平台一个 Key） */
  provider_api_keys: Record<string, string>;

  /** 模型开关（在 Key 已配置的前提下，精确控制可用模型） */
  model_enabled: Record<string, boolean>;

  // ─── Agent 模型分配 ──────────────────────────
  /** Scheduler (决策引擎) — 文本推理 */
  scheduler_model: string;
  scheduler_fallback: string;

  /** TaskAgent (任务执行) — 文本推理 + function calling */
  task_model: string;
  task_fallback: string;

  /** MemoryAgent (记忆管理) — 文本推理 */
  memory_model: string;
  memory_fallback: string;

  /** Receiver (输入理解) — 文本 + 视觉 + 语音 */
  receiver_text_model: string;
  receiver_vision_model: string;
  receiver_audio_model: string;
  receiver_vision_enabled: boolean;
  receiver_audio_enabled: boolean;

  /** Sender (输出) — TTS 语音合成 */
  sender_tts_model: string;
  sender_tts_enabled: boolean;

  /** WeChat Sender (微信输出) — 同 Sender */
  wechat_sender_tts_enabled: boolean;

  /** 渠道开关 */
  wechat_enabled: boolean;
}

/** 创建默认 RuntimeConfig */
export function createDefaultRuntimeConfig(
  mimoKey: string,
  deepseekKey: string,
): RuntimeConfig {
  return {
    provider_api_keys: {
      mimo: mimoKey,
      deepseek: deepseekKey,
    },
    model_enabled: {
      'mimo-v2.5-pro': true,
      'mimo-v2.5': true,
      'mimo-v2.5-asr': true,
      'mimo-v2.5-tts': true,
      'deepseek-v4-pro': true,
      'deepseek-v4-flash': true,
    },
    scheduler_model: 'mimo-v2.5-pro',
    scheduler_fallback: 'deepseek-v4-flash',
    task_model: 'mimo-v2.5-pro',
    task_fallback: 'deepseek-v4-flash',
    memory_model: 'mimo-v2.5-pro',
    memory_fallback: 'deepseek-v4-flash',
    receiver_text_model: 'mimo-v2.5-pro',
    receiver_vision_model: 'mimo-v2.5',
    receiver_audio_model: 'mimo-v2.5',
    receiver_vision_enabled: true,
    receiver_audio_enabled: true,
    sender_tts_model: 'mimo-v2.5-tts',
    sender_tts_enabled: true,
    wechat_sender_tts_enabled: true,
    wechat_enabled: false,
  };
}

// ─── 全局配置聚合 ────────────────────────────────────────

/** 全局配置聚合类 */
export class Config {
  readonly mimo: MiMoConfig;
  readonly deepseek: DeepSeekConfig;
  readonly agent: AgentConfig;
  readonly wechat: WeChatConfig;
  readonly runtime: RuntimeConfig;
  readonly projectRoot: string;

  constructor() {
    this.projectRoot = _projectRoot;
    this.mimo = loadMiMoConfig();
    this.deepseek = loadDeepSeekConfig();
    this.agent = loadAgentConfig();
    this.wechat = loadWeChatConfig();
    this.runtime = createDefaultRuntimeConfig(
      this.mimo.api_key,
      this.deepseek.api_key,
    );

    // 确保工作目录存在
    fs.mkdirSync(this.agent.workspace_dir, { recursive: true });
  }
}

/** 全局单例 */
let _config: Config | null = null;

/** 获取全局配置单例 */
export function getConfig(): Config {
  if (_config === null) {
    _config = new Config();
  }
  return _config;
}

/** 重置配置单例（测试用） */
export function resetConfig(): void {
  _config = null;
}
