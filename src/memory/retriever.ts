/**
 * MemoryRetriever — 混合检索 (关键词 + LLM 重排)
 *
 * Phase 4 将完整实现。当前为 stub (keyword-only)。
 *
 * 与 Python 版 memory/retriever.py 保持 1:1 语义映射。
 */

import type { KnowledgeEntry } from './store.js';

/**
 * MemoryRetriever — 混合检索器
 *
 * 5 阶段检索: 提取关键词 → 扫描索引 → 加载日分片 → 关键词匹配 → LLM 重排
 */
export class MemoryRetriever {
  /**
   * Phase 4 将存储 provider 引用用于 LLM 提取和重排。
   * 当前 stub 使用简单关键词分词。
   */
  constructor(_opts: {
    provider: unknown;
    fallbackProvider?: unknown;
    enableLlmRerank?: boolean;
  }) {
    // Phase 4: store opts for LLM-based extraction
    void _opts; // suppress unused warning
  }

  /**
   * 检索知识
   *
   * @param intent - 用户意图
   * @param store - MemoryStore 实例
   * @param topK - 返回条数
   */
  async retrieve(
    intent: string,
    store: { get_by_keywords: (kw: string[]) => KnowledgeEntry[] },
    topK = 5,
  ): Promise<KnowledgeEntry[]> {
    const keywords = await this._extractKeywords(intent);
    return this._keywordMatch(keywords, store.get_by_keywords(keywords)).slice(0, topK);
  }

  /** 从意图中提取关键词 */
  async _extractKeywords(intent: string): Promise<string[]> {
    // Phase 4: LLM 提取关键词
    // 当前简单中文分词
    const tokens: string[] = [];
    const chineseChars = intent.match(/[\u4e00-\u9fff]/g) || [];
    const seen = new Set<string>();
    for (let i = 0; i < chineseChars.length - 1; i++) {
      const bigram = chineseChars[i]! + chineseChars[i + 1]!;
      if (!seen.has(bigram)) {
        seen.add(bigram);
        tokens.push(bigram);
      }
    }
    // ASCII 单词
    const asciiTokens = intent.match(/[a-zA-Z_][a-zA-Z0-9_]{2,}/g) || [];
    tokens.push(...asciiTokens);

    return [...new Set(tokens)].slice(0, 10);
  }

  /** 关键词匹配 + 打分 */
  _keywordMatch(
    keywords: string[],
    entries: KnowledgeEntry[],
  ): KnowledgeEntry[] {
    const scored = entries.map((entry) => {
      const keywordHits = keywords.filter(
        (kw) =>
          entry.content.includes(kw) ||
          entry.keywords.some((k) => k.includes(kw)),
      ).length;
      const score =
        keywordHits * 2.0 +
        entry.importance * 0.5 +
        entry.confidence * 0.5;
      return { entry, score };
    });

    return scored
      .filter((s) => s.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((s) => s.entry);
  }

  /** 为上下文生成记忆摘要 */
  async summarizeForContext(
    _intent: string,
    retrieved: KnowledgeEntry[],
  ): Promise<string> {
    return this._simpleSummary(retrieved);
  }

  /** 简单拼接摘要（不经过 LLM） */
  _simpleSummary(entries: KnowledgeEntry[]): string {
    if (entries.length === 0) return '';
    const lines = ['## 相关记忆'];
    for (const entry of entries) {
      lines.push(`- [${entry.category_label}] ${entry.content}`);
    }
    return lines.join('\n');
  }
}
