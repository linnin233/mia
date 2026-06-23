/**
 * ShellTool — 沙箱子进程执行
 *
 * 安全约束:
 *   - 黑名单命令 (rm -rf, sudo, fork bomb 等)
 *   - 30 秒执行超时
 *   - 工作目录沙箱
 *
 * 与 Python 版 tools/shell.py 保持 1:1 语义映射。
 */

import { exec } from 'node:child_process';
import path from 'node:path';
import fs from 'node:fs';
import { Tool, type ToolResult } from './base.js';

// ─── 危险命令黑名单 ────────────────────────────────────

const DANGEROUS_PATTERNS = [
  /rm\s+(-[rRf]+\s+)*[/~]/,     // rm -rf / 或 rm -rf ~
  /sudo\s/,                       // sudo
  /mkfs\./,                       // 格式化磁盘
  /dd\s+if=/,                     // dd 磁盘操作
  />\s*\/dev\//,                  // 写入设备
  /:\(\)\s*\{/,                   // fork bomb
  /chmod\s+(-[Rr]+\s+)?777\s+\//, // chmod 777 /
  /shutdown\s/,                   // 关机
  /reboot\s/,                     // 重启
  /wget\s+.*\|\s*sh/,            // 管道下载执行
  /curl\s+.*\|\s*sh/,            // 管道下载执行
];

/**
 * ShellTool — Shell 命令执行工具
 *
 * 适用于: 运行代码、文件操作、系统查询、数据处理。
 * 注意: 命令会在工作目录沙箱中执行，30秒超时。
 */
export class ShellTool extends Tool {
  readonly name = 'shell';
  readonly description =
    '执行 Shell 命令并返回 stdout/stderr。' +
    '适用于: 运行代码、文件操作、系统查询、数据处理。' +
    '注意: 命令会在工作目录沙箱中执行，30秒超时。';
  readonly parameters = {
    type: 'object',
    properties: {
      command: { type: 'string', description: '要执行的 Shell 命令' },
      workdir: { type: 'string', description: '工作目录 (可选，默认 workspace)' },
    },
    required: ['command'],
  };

  private defaultWorkdir: string;

  constructor(defaultWorkdir?: string) {
    super();
    this.defaultWorkdir = defaultWorkdir || path.resolve('workspace');
    fs.mkdirSync(this.defaultWorkdir, { recursive: true });
  }

  /**
   * 执行 Shell 命令
   *
   * @param kwargs.command - Shell 命令
   * @param kwargs.workdir - 工作目录
   */
  async execute(kwargs: Record<string, unknown>): Promise<ToolResult> {
    const command = String(kwargs['command'] || '');
    const workdir = (kwargs['workdir'] as string) || this.defaultWorkdir;

    if (!command) {
      return { success: false, data: '', error: '命令不能为空' };
    }

    // ─── 安全检查 ───────────────────────────────
    for (const pattern of DANGEROUS_PATTERNS) {
      if (pattern.test(command)) {
        return {
          success: false,
          data: '',
          error: `命令被安全策略拦截 (匹配危险模式: ${pattern.source})`,
        };
      }
    }

    // ─── 工作目录沙箱 ──────────────────────────
    if (!fs.existsSync(workdir)) {
      return { success: false, data: '', error: `工作目录不存在: ${workdir}` };
    }

    const absCwd = path.resolve(workdir);
    const allowedRoot = path.resolve(this.defaultWorkdir);
    if (!absCwd.startsWith(allowedRoot)) {
      return {
        success: false,
        data: '',
        error: `工作目录超出允许范围: ${absCwd}`,
      };
    }

    // ─── 执行命令 ──────────────────────────────
    return new Promise((resolve) => {
      const child = exec(
        command,
        {
          cwd: absCwd,
          timeout: 30_000,
          maxBuffer: 1024 * 1024, // 1MB
          shell: process.platform === 'win32' ? 'cmd.exe' : '/bin/bash',
        },
        (error, stdout, stderr) => {
          if (error) {
            const errMsg = stderr.trim() || error.message;
            resolve({
              success: false,
              data: stdout.trim() || '',
              error: errMsg,
            });
          } else {
            const output = stdout.trim() || '(无输出)';
            resolve({ success: true, data: output, error: '' });
          }
        },
      );

      // 超时由 exec 的 timeout 选项处理，但需要 kill
      child.on('error', (err) => {
        resolve({
          success: false,
          data: '',
          error: `命令执行失败: ${err.message}`,
        });
      });
    });
  }
}
