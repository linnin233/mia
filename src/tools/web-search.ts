/**
 * WebSearchTool — DuckDuckGo 网页搜索
 *
 * 返回前 5 条搜索结果 (标题 + 摘要 + URL)。
 *
 * 与 Python 版 tools/web_search.py 保持 1:1 语义映射。
 */

import { Tool, type ToolResult } from './base.js';

/** DuckDuckGo 搜索结果项 */
interface SearchResult {
  title: string;
  href: string;
  body: string;
}

/**
 * WebSearchTool — DuckDuckGo 搜索工具
 *
 * 适用于: 查找最新信息、事实查询、资料收集。
 */
export class WebSearchTool extends Tool {
  readonly name = 'web_search';
  readonly description =
    '搜索互联网信息，返回相关结果的标题、摘要和URL。' +
    '适用于: 查找最新信息、事实查询、资料收集。' +
    '注意: 返回摘要而非完整网页内容。';
  readonly parameters = {
    type: 'object',
    properties: {
      query: { type: 'string', description: '搜索关键词' },
      max_results: {
        type: 'integer',
        description: '最大返回结果数 (默认5, 最多10)',
      },
    },
    required: ['query'],
  };

  /**
   * 执行网页搜索
   *
   * @param kwargs.query - 搜索关键词
   * @param kwargs.max_results - 最大返回结果数
   */
  async execute(kwargs: Record<string, unknown>): Promise<ToolResult> {
    const query = String(kwargs['query'] || '');
    const maxResults = Math.min(Number(kwargs['max_results']) || 5, 10);

    if (!query) {
      return { success: false, data: '', error: '搜索关键词不能为空' };
    }

    try {
      // DuckDuckGo Instant Answer API (免费，无需 API Key)
      const url = 'https://api.duckduckgo.com/';
      const params = new URLSearchParams({
        q: query,
        format: 'json',
        no_html: '1',
        skip_disambig: '1',
      });

      const response = await fetch(`${url}?${params}`, {
        signal: AbortSignal.timeout(10_000),
      });

      if (!response.ok) {
        return { success: false, data: '', error: `搜索请求失败 (HTTP ${response.status})` };
      }

      const data = (await response.json()) as {
        AbstractText?: string;
        AbstractURL?: string;
        AbstractSource?: string;
        RelatedTopics?: Array<{
          Text?: string;
          FirstURL?: string;
        }>;
        Results?: Array<{
          Text?: string;
          FirstURL?: string;
        }>;
      };

      const results: SearchResult[] = [];

      // Abstract (主结果)
      if (data.AbstractText) {
        results.push({
          title: data.AbstractSource || query,
          href: data.AbstractURL || '',
          body: data.AbstractText,
        });
      }

      // RelatedTopics / Results
      const topics = data.RelatedTopics || [];
      for (const topic of topics.slice(0, maxResults - results.length)) {
        if (topic.Text && topic.FirstURL) {
          results.push({
            title: topic.Text.split(' - ')[0]?.slice(0, 100) || topic.Text.slice(0, 100),
            href: topic.FirstURL,
            body: topic.Text.slice(0, 200),
          });
        }
      }

      if (results.length === 0) {
        return { success: true, data: '未找到相关搜索结果。', error: '' };
      }

      // 格式化
      const formatted = results.map((r, i) => {
        const body = r.body.length > 200 ? r.body.slice(0, 200) + '...' : r.body;
        return `${i + 1}. ${r.title}\n   URL: ${r.href}\n   摘要: ${body}`;
      });

      return { success: true, data: formatted.join('\n\n'), error: '' };
    } catch (err) {
      return {
        success: false,
        data: '',
        error: `搜索失败: ${err instanceof Error ? err.message : String(err)}`,
      };
    }
  }
}
