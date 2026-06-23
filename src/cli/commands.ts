/**
 * CLI 斜杠命令处理 — /model /agent /channel 的 TUI 交互逻辑
 *
 * Phase 6: 简化版 stub 实现。完整的 @inquirer/prompts 交互菜单将在后续完善。
 *
 * 与 Python 版 cli/commands.py 保持 1:1 语义映射。
 */

import type { RuntimeConfig } from '../config.js';

/** 命令执行后的操作指示 */
export enum CommandAction {
  NONE = 'none',
  RECONFIGURE_AGENTS = 'reconfigure_agents',
  RECONFIGURE_WECHAT = 'reconfigure_wechat',
}

// ─── 辅助函数 ────────────────────────────────────────────

/** 掩码显示 API Key */
function maskKey(key: string): string {
  if (!key) return '(未配置)';
  if (key.length <= 8) return '*'.repeat(key.length);
  return `${key.slice(0, 4)}****${key.slice(-4)}`;
}

// ─── /model — 模型平台配置 ───────────────────────────────

/** /model 命令 — Phase 6 stub: 显示状态，引导用户编辑 .env */
export async function handleModelCommand(
  runtime: RuntimeConfig,
): Promise<CommandAction> {
  console.log();
  console.log('  ──────────────────────────────────────────');
  console.log('  模型平台配置');
  console.log('  ──────────────────────────────────────────');

  for (const [provider, key] of Object.entries(runtime.provider_api_keys)) {
    console.log(`  ${provider.toUpperCase()}: Key=${maskKey(key)}`);
  }

  console.log();
  console.log('  (完整交互菜单开发中，暂请在 .env 文件编辑 API Key)');
  console.log('  可用命令: /model /agent /channel');
  console.log();

  return CommandAction.NONE;
}

// ─── /agent — Agent 模型分配 ─────────────────────────────

/** /agent 命令 — Phase 6 stub: 显示当前分配 */
export async function handleAgentCommand(
  runtime: RuntimeConfig,
): Promise<CommandAction> {
  console.log();
  console.log('  ──────────────────────────────────────────');
  console.log('  Agent 模型分配');
  console.log('  ──────────────────────────────────────────');
  console.log(`  Scheduler:    主=${runtime.scheduler_model}  备=${runtime.scheduler_fallback || '(无)'}`);
  console.log(`  TaskAgent:    主=${runtime.task_model}  备=${runtime.task_fallback || '(无)'}`);
  console.log(`  MemoryAgent:  主=${runtime.memory_model}  备=${runtime.memory_fallback || '(无)'}`);
  console.log(`  Receiver:     文本=${runtime.receiver_text_model}`);
  console.log(`                视觉=${runtime.receiver_vision_enabled ? 'on' : 'off'} 语音=${runtime.receiver_audio_enabled ? 'on' : 'off'}`);
  console.log(`  Sender:       TTS=${runtime.sender_tts_enabled ? 'on' : 'off'}`);
  console.log();
  console.log('  (完整交互菜单开发中，暂请在代码中修改 RuntimeConfig)');
  console.log();

  return CommandAction.NONE;
}

// ─── /channel — 通信渠道配置 ─────────────────────────────

/** /channel 命令 — Phase 6 stub: 显示配置状态 */
export async function handleChannelCommand(
  runtime: RuntimeConfig,
): Promise<CommandAction> {
  console.log();
  console.log('  ──────────────────────────────────────────');
  console.log('  通信渠道配置');
  console.log('  ──────────────────────────────────────────');
  console.log(`  微信 (iLink Bot): ${runtime.wechat_enabled ? '✓ 已启用' : '✗ 未启用'}`);
  console.log();
  console.log('  启动时加 --wechat 参数可启用微信渠道');
  console.log('  (完整交互菜单开发中)');
  console.log();

  return CommandAction.NONE;
}
