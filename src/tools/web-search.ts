/**
 * WebSearchTool — DuckDuckGo 网页搜索
 *
 * Phase 5 将完整实现。
 */

import { Tool, type ToolResult } from './base.js';

/** DuckDuckGo 网页搜索工具 (stub) */
export class WebSearchTool extends Tool {
  readonly name = 'web_search';
  readonly description = '搜索网页，获取实时信息';
  readonly parameters = {
    type: 'object',
    properties: {
      query: { type: 'string', description: '搜索关键词' },
    },
    required: ['query'],
  };

  async execute(kwargs: Record<string, unknown>): Promise<ToolResult> {
    // Phase 5: 实现 DuckDuckGo 搜索
    return {
      success: true,
      data: `[搜索工具待实现] 查询: ${kwargs['query']}`,
      error: '',
    };
  }
}
