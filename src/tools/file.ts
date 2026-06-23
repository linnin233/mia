/**
 * FileTool — 文件读写工具
 *
 * Phase 5 将完整实现。
 */

import { Tool, type ToolResult } from './base.js';

/** 文件读写工具 (stub) */
export class FileTool extends Tool {
  readonly name = 'file';
  readonly description = '在沙箱中读写文件';
  readonly parameters = {
    type: 'object',
    properties: {
      operation: { type: 'string', enum: ['read', 'write', 'list'] },
      path: { type: 'string', description: '文件路径' },
      content: { type: 'string', description: '写入内容' },
    },
    required: ['operation', 'path'],
  };

  async execute(kwargs: Record<string, unknown>): Promise<ToolResult> {
    return {
      success: true,
      data: `[File工具待实现] 操作: ${kwargs['operation']}, 路径: ${kwargs['path']}`,
      error: '',
    };
  }
}
