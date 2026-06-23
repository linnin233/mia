/**
 * TUI 边框测试 — 纯 ANSI，零依赖
 *
 * npm run tui:border
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

  // 清屏 + 光标归位
  let out = '\x1b[2J\x1b[H';

  // 顶边框
  out += B.tl + B.h.repeat(w - 2) + B.tr + '\n';

  // 中间行
  for (let y = 1; y < h - 1; y++) {
    out += B.v;
    if (y === 1) {
      // 第二行：标题
      const title = ` MIA TUI | ${w}x${h} `;
      const pad = w - 2 - title.length;
      out += title + ' '.repeat(pad > 0 ? pad : 0);
    } else if (y === h - 3) {
      // 倒数第三行：快捷键提示
      const hint = ' Ctrl+C 退出 ';
      const pad = w - 2 - hint.length;
      out += ' '.repeat(pad > 0 ? pad : 0) + hint;
    } else {
      out += ' '.repeat(w - 2);
    }
    out += B.v + '\n';
  }

  // 底边框
  out += B.bl + B.h.repeat(w - 2) + B.br;

  process.stdout.write(out);
}

// ─── 启动 ────────────────────────────────────────────────
function main() {
  // 进入 raw mode 捕获按键
  readline.emitKeypressEvents(process.stdin);
  if (process.stdin.isTTY) {
    process.stdin.setRawMode(true);
  }

  // 隐藏光标
  process.stdout.write('\x1b[?25l');

  // 监听 resize
  process.stdout.on('resize', draw);

  // 监听按键
  process.stdin.on('keypress', (_str, key) => {
    if (key.ctrl && key.name === 'c') {
      cleanup();
      process.exit(0);
    }
  });

  // 初始绘制
  draw();
}

function cleanup() {
  // 显示光标 + 清屏
  process.stdout.write('\x1b[?25h\x1b[2J\x1b[H');
  if (process.stdin.isTTY) {
    process.stdin.setRawMode(false);
  }
  process.stdin.removeAllListeners('keypress');
  process.stdout.removeAllListeners('resize');
}

main();
