/**
 * useMemory hook — 记忆浏览器数据查询
 */

import { useState, useCallback } from 'react';
import type { MemoryEntry } from '../types.js';
import type { MemoryAgent } from '../../agents/memory.js';

const PAGE_SIZE = 10;

/** 记忆浏览器状态 */
interface MemoryBrowserState {
  entries: MemoryEntry[];
  total: number;
  page: number;
  totalPages: number;
}

/**
 * 从 MemoryAgent 读取记忆并分页
 */
export function useMemory(memoryAgent: MemoryAgent | null) {
  const [browser, setBrowser] = useState<MemoryBrowserState>({
    entries: [],
    total: 0,
    page: 0,
    totalPages: 0,
  });

  /** 加载记忆 */
  const loadMemory = useCallback(() => {
    if (!memoryAgent) return;

    // 访问 private store（临时方案，后续加公开 getter）
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const store = (memoryAgent as any).store;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const working = (memoryAgent as any)._workingMemory || [];

    const all = [...store.get_all(), ...working].map(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (e: any) => ({
        id: e.id,
        content: e.content,
        category: e.category,
        confidence: e.confidence,
        importance: e.importance,
        categoryLabel: e.category_label || e.category,
      }),
    ) as MemoryEntry[];

    setBrowser({
      entries: all,
      total: all.length,
      page: 0,
      totalPages: Math.max(1, Math.ceil(all.length / PAGE_SIZE)),
    });
  }, [memoryAgent]);

  /** 当前页记忆 */
  const currentPage = browser.entries.slice(
    browser.page * PAGE_SIZE,
    (browser.page + 1) * PAGE_SIZE,
  );

  /** 翻页 */
  const nextPage = useCallback(() => {
    setBrowser((prev) => ({
      ...prev,
      page: Math.min(prev.page + 1, prev.totalPages - 1),
    }));
  }, []);

  const prevPage = useCallback(() => {
    setBrowser((prev) => ({
      ...prev,
      page: Math.max(prev.page - 1, 0),
    }));
  }, []);

  return {
    currentPage,
    total: browser.total,
    page: browser.page,
    totalPages: browser.totalPages,
    loadMemory,
    nextPage,
    prevPage,
  };
}
