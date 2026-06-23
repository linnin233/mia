#!/usr/bin/env node
/**
 * MIA Ink TUI — CLI 入口
 *
 * npm run tui              # 启动 TUI
 * npm run tui -- --wechat   # TUI + 微信
 */

import { runTui } from './run.js';

async function main() {
  const args = process.argv.slice(2);
  const enableWechat = args.includes('--wechat') || args.includes('-w');

  try {
    await runTui(enableWechat);
  } catch (err) {
    console.error('MIA TUI 启动失败:', err);
    process.exit(1);
  }
}

main();
