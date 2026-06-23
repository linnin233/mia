/**
 * MemoryStore — 两级文件知识存储
 *
 * 结构: index.json + daily/YYYY-MM-DD.json 分片
 * Phase 4 将完整实现。
 *
 * 与 Python 版 memory/store.py 保持 1:1 语义映射。
 */

import crypto from 'node:crypto';

// ─── 常量 ────────────────────────────────────────────────

export const CATEGORY_FACT = 'fact';
export const CATEGORY_PREFERENCE = 'preference';
export const CATEGORY_DECISION = 'decision';
export const CATEGORY_TASK = 'task';
export const CATEGORY_INSIGHT = 'insight';

/** 类别中文标签映射 */
const CATEGORY_LABELS: Record<string, string> = {
  [CATEGORY_FACT]: '事实',
  [CATEGORY_PREFERENCE]: '偏好',
  [CATEGORY_DECISION]: '决策',
  [CATEGORY_TASK]: '任务',
  [CATEGORY_INSIGHT]: '洞察',
};

// ─── KnowledgeEntry ──────────────────────────────────────

/** 知识条目 — 记忆的基本单元 */
export class KnowledgeEntry {
  /** 唯一 ID */
  id: string;

  /** 知识内容 */
  content: string;

  /** 类别 */
  category: string;

  /** 置信度 0.0-1.0 */
  confidence: number;

  /** 关键词列表 */
  keywords: string[];

  /** 重要度 0.0-1.0 */
  importance: number;

  /** 来源会话 ID 列表 */
  source_sessions: string[];

  /** 创建时间 ISO */
  created_at: string;

  /** 更新时间 ISO */
  updated_at: string;

  constructor(opts: {
    content: string;
    category?: string;
    confidence?: number;
    keywords?: string[];
    importance?: number;
    source_sessions?: string[];
    id?: string;
    created_at?: string;
  }) {
    this.id = opts.id || crypto.randomBytes(8).toString('hex');
    this.content = opts.content;
    this.category = opts.category || CATEGORY_FACT;
    this.confidence = opts.confidence ?? 0.5;
    this.keywords = opts.keywords || [];
    this.importance = opts.importance ?? 0.5;
    this.source_sessions = opts.source_sessions || [];
    const now = new Date().toISOString();
    this.created_at = opts.created_at || now;
    this.updated_at = now;
  }

  /** 类别中文标签 */
  get category_label(): string {
    return CATEGORY_LABELS[this.category] || this.category;
  }
}

// ─── 时间工具 ────────────────────────────────────────────

/** 北京时区偏移 (UTC+8) */
const BEIJING_OFFSET = 8 * 60 * 60 * 1000;

/** 获取当前北京时间的 Date 对象 */
export function nowBeijing(): Date {
  return new Date(Date.now() + BEIJING_OFFSET - new Date().getTimezoneOffset() * 60 * 1000);
}

/** 获取今天的日期字符串 (YYYY-MM-DD) */
export function todayStr(): string {
  const now = new Date();
  // 使用 UTC+8
  const bj = new Date(now.getTime() + BEIJING_OFFSET);
  return bj.toISOString().split('T')[0]!;
}

// ─── MemoryStore Stub ────────────────────────────────────

/**
 * MemoryStore — 文件持久化知识存储
 *
 * Phase 4 完整实现。当前为 stub。
 */
export class MemoryStore {
  private _entries: KnowledgeEntry[] = [];

  load(): void {
    // Phase 4: 从 index.json 加载
  }

  add(entry: KnowledgeEntry): void {
    this._entries.push(entry);
  }

  get count(): number {
    return this._entries.length;
  }

  get_all(): KnowledgeEntry[] {
    return [...this._entries];
  }

  get_recent(n: number): KnowledgeEntry[] {
    return this._entries.slice(-n);
  }

  get_by_keywords(keywords: string[]): KnowledgeEntry[] {
    // 简单关键词匹配
    return this._entries.filter((e) =>
      keywords.some((kw) =>
        e.content.includes(kw) || e.keywords.some((k) => k.includes(kw)),
      ),
    );
  }

  delete(_entryId: string): void {
    this._entries = this._entries.filter((e) => e.id !== _entryId);
  }

  clear(): void {
    this._entries = [];
  }

  compact(_summary: string, _sourceSessionIds?: string[]): void {
    // Phase 4: 压缩为摘要
  }
}
