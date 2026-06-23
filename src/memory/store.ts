/**
 * MemoryStore — 文件持久化知识存储
 *
 * 存储文件: <workspace_dir>/memory/store.json
 * 格式: JSON 数组，每条 KnowledgeEntry 序列化为 plain object
 *
 * 特性:
 *   - 自动加载/保存，重启不丢失
 *   - 关键词匹配检索
 *   - 去重 (同 content 合并)
 *   - 最多保留 500 条
 *
 * 与 Python 版 memory/store.py 保持 1:1 语义映射。
 */

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { getConfig } from '../config.js';

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

/** 知识条目序列化格式 (用于 JSON 持久化) */
interface KnowledgeEntryData {
  id: string;
  content: string;
  category: string;
  confidence: number;
  keywords: string[];
  importance: number;
  source_sessions: string[];
  created_at: string;
  updated_at: string;
}

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
    updated_at?: string;
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
    this.updated_at = opts.updated_at || now;
  }

  /** 类别中文标签 */
  get category_label(): string {
    return CATEGORY_LABELS[this.category] || this.category;
  }

  /** 序列化为 plain object (用于 JSON) */
  toJSON(): KnowledgeEntryData {
    return {
      id: this.id,
      content: this.content,
      category: this.category,
      confidence: this.confidence,
      keywords: this.keywords,
      importance: this.importance,
      source_sessions: this.source_sessions,
      created_at: this.created_at,
      updated_at: this.updated_at,
    };
  }

  /** 从 plain object 反序列化 */
  static fromJSON(data: KnowledgeEntryData): KnowledgeEntry {
    return new KnowledgeEntry({
      id: data.id,
      content: data.content,
      category: data.category,
      confidence: data.confidence,
      keywords: data.keywords,
      importance: data.importance,
      source_sessions: data.source_sessions,
      created_at: data.created_at,
      updated_at: data.updated_at,
    });
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

// ─── MemoryStore ─────────────────────────────────────────

/** 最大存储条目数 */
const MAX_ENTRIES = 500;

/**
 * MemoryStore — 文件持久化知识存储
 *
 * 数据存储在 <workspace>/memory/store.json。
 * 每次 add/delete/clear 后自动保存。
 */
export class MemoryStore {
  private _entries: KnowledgeEntry[] = [];
  private _filePath: string | null = null;

  /** 获取存储文件路径 */
  private get filePath(): string {
    if (this._filePath) return this._filePath;
    try {
      const ws = getConfig().agent.workspace_dir;
      const dir = path.join(ws, 'memory');
      fs.mkdirSync(dir, { recursive: true });
      this._filePath = path.join(dir, 'store.json');
    } catch {
      // 回退到项目根目录
      this._filePath = path.resolve('memory', 'store.json');
      fs.mkdirSync(path.dirname(this._filePath), { recursive: true });
    }
    return this._filePath;
  }

  /** 从文件加载 — 兼容 Python 版 index.json + daily/ 格式 */
  load(): void {
    // 1. 优先加载 JS 格式 store.json
    try {
      if (fs.existsSync(this.filePath)) {
        const raw = fs.readFileSync(this.filePath, 'utf-8');
        const data: KnowledgeEntryData[] = JSON.parse(raw);
        if (data.length > 0) {
          this._entries = data.map((d) => KnowledgeEntry.fromJSON(d));
          console.log(
            `\x1b[34m[MemoryStore]\x1b[0m 已加载 ${this._entries.length} 条持久记忆 (store.json)`,
          );
          return;
        }
      }
    } catch {
      // store.json 损坏，继续尝试 Python 格式
    }

    // 2. 尝试加载 Python 格式: index.json + daily/YYYY-MM-DD.json
    const memoryDir = path.dirname(this.filePath);
    const indexPath = path.join(memoryDir, 'index.json');
    if (!fs.existsSync(indexPath)) {
      // 检查备用路径: data/memory/ (Python 版默认)
      const altDir = path.resolve('data', 'memory');
      const altIndex = path.join(altDir, 'index.json');
      if (fs.existsSync(altIndex)) {
        this._loadPythonFormat(altDir);
        return;
      }
      console.log(`\x1b[34m[MemoryStore]\x1b[0m 无记忆数据`);
      return;
    }
    this._loadPythonFormat(memoryDir);
  }

  /** 加载 Python 版 index.json + daily/ 格式 */
  private _loadPythonFormat(memoryDir: string): void {
    try {
      const indexPath = path.join(memoryDir, 'index.json');
      const indexRaw = fs.readFileSync(indexPath, 'utf-8');
      const index = JSON.parse(indexRaw) as {
        version: number;
        updated: string;
        days: Record<string, { file: string; entry_count: number }>;
      };

      const allEntries: KnowledgeEntry[] = [];

      for (const [_date, dayInfo] of Object.entries(index.days || {})) {
        const dayFile = path.join(memoryDir, dayInfo.file);
        if (!fs.existsSync(dayFile)) continue;
        try {
          const dayRaw = fs.readFileSync(dayFile, 'utf-8');
          const dayEntries: KnowledgeEntryData[] = JSON.parse(dayRaw);
          for (const de of dayEntries) {
            allEntries.push(KnowledgeEntry.fromJSON(de));
          }
        } catch {
          // 跳过损坏的 daily 文件
        }
      }

      this._entries = allEntries;
      console.log(
        `\x1b[34m[MemoryStore]\x1b[0m 已加载 ${this._entries.length} 条持久记忆 (Python index.json + daily/)`,
      );

      // 自动迁移到 JS 格式 store.json
      if (this._entries.length > 0) {
        this.save();
        console.log(
          `\x1b[34m[MemoryStore]\x1b[0m 已迁移到 store.json`,
        );
      }
    } catch (err) {
      console.warn(
        `\x1b[33m[MemoryStore]\x1b[0m Python 格式加载失败: ${err}`,
      );
      this._entries = [];
    }
  }

  /** 保存到文件 */
  save(): void {
    if (!this._filePath) {
      try {
        this.filePath; // 触发路径初始化
      } catch {
        return;
      }
    }
    try {
      // 确保父目录存在（测试场景直接设置 _filePath 时可能不存在）
      const dir = path.dirname(this._filePath!);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      const data = this._entries.map((e) => e.toJSON());
      fs.writeFileSync(this._filePath!, JSON.stringify(data, null, 2), 'utf-8');
    } catch (err) {
      console.warn(
        `\x1b[33m[MemoryStore]\x1b[0m 保存失败: ${err}`,
      );
    }
  }

  /** 添加条目 — 自动去重 + 保存 */
  add(entry: KnowledgeEntry): void {
    // 去重: 相同 content 的条目合并
    const existing = this._entries.find(
      (e) => e.content === entry.content,
    );
    if (existing) {
      // 更新置信度 (取高值)
      existing.confidence = Math.max(existing.confidence, entry.confidence);
      existing.keywords = [...new Set([...existing.keywords, ...entry.keywords])];
      existing.importance = Math.max(existing.importance, entry.importance);
      existing.source_sessions = [
        ...new Set([...existing.source_sessions, ...entry.source_sessions]),
      ];
      existing.updated_at = new Date().toISOString();
      this.save();
      return;
    }

    this._entries.push(entry);

    // 超过上限时删除最旧的条目
    if (this._entries.length > MAX_ENTRIES) {
      this._entries = this._entries.slice(-MAX_ENTRIES);
    }

    this.save();
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
      keywords.some(
        (kw) =>
          e.content.includes(kw) || e.keywords.some((k) => k.includes(kw)),
      ),
    );
  }

  delete(entryId: string): void {
    this._entries = this._entries.filter((e) => e.id !== entryId);
    this.save();
  }

  clear(): void {
    this._entries = [];
    this.save();
  }

  compact(_summary: string, _sourceSessionIds?: string[]): void {
    // 简单压缩: 保留最近 50 条高重要度条目
    const sorted = [...this._entries].sort(
      (a, b) => b.importance - a.importance,
    );
    this._entries = sorted.slice(0, 50);
    this.save();
  }
}
