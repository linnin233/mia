/**
 * FileTool — 文件读写工具（沙箱保护）
 *
 * 安全约束:
 *   - 路径沙箱: 只能访问工作目录内的文件
 *   - 不能访问 ../
 *
 * 与 Python 版 tools/file.py 保持 1:1 语义映射。
 */

import fs from 'node:fs/promises';
import path from 'node:path';
import { Tool, type ToolResult } from './base.js';

/**
 * FileTool — 受沙箱保护的文件读写工具
 *
 * 适用: 读取文件内容、写入文件、列出目录。
 * 注意: 只能访问工作目录内的文件，不能访问系统文件。
 */
export class FileTool extends Tool {
  readonly name = 'file';
  readonly description =
    '读写工作目录下的文件。支持: 读取文件内容、写入文件、列出目录。' +
    '注意: 只能访问工作目录内的文件，不能访问系统文件。';
  readonly parameters = {
    type: 'object',
    properties: {
      operation: {
        type: 'string',
        enum: ['read', 'write', 'list'],
        description: '操作类型: read(读), write(写), list(列目录)',
      },
      path: {
        type: 'string',
        description: '相对于工作目录的文件路径',
      },
      content: {
        type: 'string',
        description: '要写入的内容 (仅 write 操作需要)',
      },
    },
    required: ['operation', 'path'],
  };

  private workspace: string;

  constructor(workspaceDir?: string) {
    super();
    this.workspace = workspaceDir || path.resolve('workspace');
  }

  /**
   * 执行文件操作
   *
   * @param kwargs.operation - read / write / list
   * @param kwargs.path - 文件路径 (相对于 workspace)
   * @param kwargs.content - 写入内容
   */
  async execute(kwargs: Record<string, unknown>): Promise<ToolResult> {
    const operation = String(kwargs['operation'] || '');
    const filePath = String(kwargs['path'] || '');
    const content = kwargs['content'] as string | undefined;

    // ─── 路径沙箱检查 ──────────────────────────
    const resolved = path.resolve(this.workspace, filePath);
    const workspaceResolved = path.resolve(this.workspace);

    if (!resolved.startsWith(workspaceResolved + path.sep) &&
        resolved !== workspaceResolved) {
      return {
        success: false,
        data: '',
        error: `路径越界: ${filePath} (只允许访问工作目录内的文件)`,
      };
    }

    try {
      switch (operation) {
        case 'read':
          return await this._read(resolved);
        case 'write':
          return await this._write(resolved, content || '');
        case 'list':
          return await this._list(resolved);
        default:
          return { success: false, data: '', error: `未知操作: ${operation}` };
      }
    } catch (err) {
      return {
        success: false,
        data: '',
        error: String(err instanceof Error ? err.message : err),
      };
    }
  }

  /** 读取文件内容 */
  private async _read(filepath: string): Promise<ToolResult> {
    try {
      await fs.access(filepath);
    } catch {
      return { success: false, data: '', error: `文件不存在: ${path.basename(filepath)}` };
    }

    const stat = await fs.stat(filepath);
    if (!stat.isFile()) {
      return { success: false, data: '', error: `不是文件: ${path.basename(filepath)}` };
    }

    const raw = await fs.readFile(filepath, 'utf-8');
    let content = raw;
    // 限制读取长度
    if (content.length > 5000) {
      content = content.slice(0, 5000) + `\n...(截断，共 ${raw.length} 字符)`;
    }

    return { success: true, data: content, error: '' };
  }

  /** 写入文件内容 */
  private async _write(filepath: string, content: string): Promise<ToolResult> {
    const dir = path.dirname(filepath);
    await fs.mkdir(dir, { recursive: true });
    await fs.writeFile(filepath, content, 'utf-8');

    return {
      success: true,
      data: `文件已写入: ${path.basename(filepath)} (${content.length} 字符)`,
      error: '',
    };
  }

  /** 列出目录内容 */
  private async _list(dirpath: string): Promise<ToolResult> {
    try {
      await fs.access(dirpath);
    } catch {
      return { success: false, data: '', error: `目录不存在: ${path.basename(dirpath)}` };
    }

    const stat = await fs.stat(dirpath);
    if (!stat.isDirectory()) {
      return { success: false, data: '', error: `不是目录: ${path.basename(dirpath)}` };
    }

    const entries = await fs.readdir(dirpath, { withFileTypes: true });
    const items = entries
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((entry) => {
        const prefix = entry.isDirectory() ? '[DIR]' : '[FILE]';
        return `  ${prefix} ${entry.name}`;
      })
      .slice(0, 50);

    if (items.length === 0) {
      return { success: true, data: '(空目录)', error: '' };
    }

    return { success: true, data: items.join('\n'), error: '' };
  }
}
