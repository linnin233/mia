/**
 * MemoryBrowser — TUI 交互式知识浏览器
 *
 * Phase 6 将完整实现（需要 @inquirer/prompts）。
 *
 * 与 Python 版 memory/browser.py 保持 1:1 语义映射。
 */

import type { KnowledgeEntry } from './store.js';

/** TUI 知识浏览器 (stub) */
export class MemoryBrowser {
  /**
   * 浏览知识库
   *
   * @param working - Level 1 临时记忆
   * @param persistent - Level 2 持久知识
   */
  async browse(
    working: KnowledgeEntry[],
    persistent: KnowledgeEntry[],
  ): Promise<void> {
    const total = working.length + persistent.length;
    if (total === 0) {
      console.log('知识库为空。');
      return;
    }

    console.log(`\n知识库: 临时 ${working.length} 条 + 持久 ${persistent.length} 条`);
    console.log('-'.repeat(50));

    for (const entry of [...persistent, ...working]) {
      console.log(
        `[${entry.category_label}] ${entry.content}`,
      );
      console.log(
        `  置信度: ${entry.confidence.toFixed(2)} | 重要度: ${entry.importance.toFixed(2)}`,
      );
    }
    console.log('-'.repeat(50));
  }
}
