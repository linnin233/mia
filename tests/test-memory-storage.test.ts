/**
 * MemoryStore 单元测试
 *
 * 对应 Python 版 test_memory_storage.py
 */

import { describe, it, expect } from 'vitest';
import { MemoryStore, KnowledgeEntry, CATEGORY_FACT, CATEGORY_PREFERENCE } from '../src/memory/store.js';

describe('MemoryStore', () => {
  it('初始状态 count 应为 0', () => {
    const store = new MemoryStore();
    expect(store.count).toBe(0);
  });

  it('添加知识条目后 count 应增加', () => {
    const store = new MemoryStore();
    const entry = new KnowledgeEntry({
      content: '用户偏好 TypeScript',
      category: CATEGORY_PREFERENCE,
      confidence: 0.8,
      keywords: ['TypeScript', '偏好'],
      importance: 0.7,
    });

    store.add(entry);
    expect(store.count).toBe(1);
  });

  it('get_all 应返回所有条目', () => {
    const store = new MemoryStore();
    store.add(new KnowledgeEntry({ content: 'entry 1' }));
    store.add(new KnowledgeEntry({ content: 'entry 2' }));

    const all = store.get_all();
    expect(all.length).toBe(2);
  });

  it('get_recent 应返回最近 N 条', () => {
    const store = new MemoryStore();
    store.add(new KnowledgeEntry({ content: 'a' }));
    store.add(new KnowledgeEntry({ content: 'b' }));
    store.add(new KnowledgeEntry({ content: 'c' }));

    const recent = store.get_recent(2);
    expect(recent.length).toBe(2);
    expect(recent[0]!.content).toBe('b');
    expect(recent[1]!.content).toBe('c');
  });

  it('get_by_keywords 应匹配关键词', () => {
    const store = new MemoryStore();
    store.add(new KnowledgeEntry({
      content: '用户喜欢写 TypeScript',
      keywords: ['TypeScript', '编码'],
    }));
    store.add(new KnowledgeEntry({
      content: '用户住在中国',
      keywords: ['地址'],
    }));

    const results = store.get_by_keywords(['TypeScript']);
    expect(results.length).toBe(1);
    expect(results[0]!.content).toContain('TypeScript');
  });

  it('clear 应清空所有条目', () => {
    const store = new MemoryStore();
    store.add(new KnowledgeEntry({ content: 'test' }));
    expect(store.count).toBe(1);

    store.clear();
    expect(store.count).toBe(0);
  });

  it('delete 应删除指定条目', () => {
    const store = new MemoryStore();
    const entry = new KnowledgeEntry({ content: 'to delete' });
    store.add(entry);

    store.delete(entry.id);
    expect(store.count).toBe(0);
  });

  it('KnowledgeEntry category_label 应返回中文标签', () => {
    const entry = new KnowledgeEntry({
      content: 'test',
      category: CATEGORY_FACT,
    });
    expect(entry.category_label).toBe('事实');
  });
});
