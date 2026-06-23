/**
 * TUI 边框测试 — 纯 ANSI，零依赖
 *
 * npm run tui
 */

import readline from 'node:readline';

// ─── Box-drawing 字符 ────────────────────────────────────
const B = {
  tl: '┌', tr: '┐', bl: '└', br: '┘',
  h: '─', v: '│',
  hl: '├', hr: '┤', vt: '┬', vb: '┴',
  x: '┼',
};

let cols = process.stdout.columns || 80;
let rows = process.stdout.rows || 24;

// ─── 绘制边框 ────────────────────────────────────────────
function draw() {
  cols = process.stdout.columns || 80;
  rows = process.stdout.rows || 24;
  const w = cols;
  const h = rows;
  const innerW = w - 2;

  // 清屏 + 光标归位
  let out = '\x1b[2J\x1b[H';

  // ─── 顶边框 ─────────────────────────────────
  out += B.tl + B.h.repeat(innerW) + B.tr + '\n';

  // ─── Header 区 ──────────────────────────────
  const title = ' MIA TUI | ' + w + 'x' + h + ' ';
  const titlePad = innerW - title.length;
  out += B.v + title + ' '.repeat(titlePad > 0 ? titlePad : 0) + B.v + '\n';

  // ─── Header 分隔线 ──────────────────────────
  out += B.hl + B.h.repeat(innerW) + B.hr + '\n';

  // ─── 内容区 ─────────────────────────────────
  for (let y = 3; y < h - 1; y++) {
    out += B.v;
    if (y === h - 2) {
      const hint = ' Ctrl+C 退出 ';
      const pad = innerW - hint.length;
      out += ' '.repeat(pad > 0 ? pad : 0) + hint;
    } else {
      out += ' '.repeat(innerW);
    }
    out += B.v + '\n';
  }

  // ─── 底边框 ─────────────────────────────────
  out += B.bl + B.h.repeat(innerW) + B.br;

  process.stdout.write(out);
}

// ─── 启动 ────────────────────────────────────────────────
function main() {
  // 交替屏幕缓冲区
  process.stdout.write('\x1b[?1049h');
  // 隐藏光标
  process.stdout.write('\x1b[?25l');

  // raw mode
  readline.emitKeypressEvents(process.stdin);
  if (process.stdin.isTTY) {
    process.stdin.setRawMode(true);
  }

  // resize
  process.stdout.on('resize', draw);

  // 按键
  process.stdin.on('keypress', (_str, key) => {
    if (key.ctrl && key.name === 'c') {
      cleanup();
      process.exit(0);
    }
  });

  draw();
}

function cleanup() {
  process.stdout.write('\x1b[?1049l\x1b[?25h');
  if (process.stdin.isTTY) {
    process.stdin.setRawMode(false);
  }
  process.stdin.removeAllListeners('keypress');
  process.stdout.removeAllListeners('resize');
}

main();
