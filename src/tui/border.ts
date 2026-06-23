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

  // 归位 + 清屏（不用 \x1b[3J，Windows Terminal 兼容性更好）
  let out = '\x1b[1;1H\x1b[2J';

  // 顶边框
  const topLine = B.tl + B.h.repeat(innerW) + B.tr + '\n';
  out += topLine;

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
  // 交替屏幕 + 隐藏光标
  process.stdout.write('\x1b[?1049h\x1b[?25l');

  // 初始绘制（同步，紧跟交替屏幕）
  draw();

  // raw mode
  readline.emitKeypressEvents(process.stdin);
  if (process.stdin.isTTY) {
    process.stdin.setRawMode(true);
  }

  // resize 时重绘
  process.stdout.on('resize', draw);

  // 按键
  process.stdin.on('keypress', (_str, key) => {
    if (key.ctrl && key.name === 'c') {
      cleanup();
      process.exit(0);
    }
  });
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
