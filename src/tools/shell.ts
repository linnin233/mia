/**
 * ShellTool — 沙箱子进程执行
 *
 * Phase 5 将完整实现。
 */

import { Tool, type ToolResult } from './base.js';

/** Shell 命令执行工具 (stub) */
export class ShellTool extends Tool {
  readonly name = 'shell';
  readonly description = '在沙箱中执行 shell 命令';
  readonly parameters = {
    type: 'object',
    properties: {
      command: { type: 'string', description: '要执行的命令' },
    },
    required: ['command'],
  };

  async execute(kwargs: Record<string, unknown>): Promise<ToolResult> {
    return {
      success: true,
      data: `[Shell工具待实现] 命令: ${kwargs['command']}`,
      error: '',
    };
  }
}
