/**
 * WeatherTool — wttr.in 天气查询
 *
 * Phase 5 将完整实现。
 */

import { Tool, type ToolResult } from './base.js';

/** 天气查询工具 (stub) */
export class WeatherTool extends Tool {
  readonly name = 'weather';
  readonly description = '查询指定城市的天气信息';
  readonly parameters = {
    type: 'object',
    properties: {
      city: { type: 'string', description: '城市名称' },
    },
    required: ['city'],
  };

  async execute(kwargs: Record<string, unknown>): Promise<ToolResult> {
    return {
      success: true,
      data: `[天气工具待实现] 城市: ${kwargs['city']}`,
      error: '',
    };
  }
}
