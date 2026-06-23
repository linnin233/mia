/**
 * TUI 边框测试 — 纯 ANSI，零依赖
 *
 * npm run tui
 */

import readline from 'node:readline';

const B = {
  tl: '┌', tr: '┐', bl: '└', br: '┘',
  h: '─', v: '│',
  hl: '├', hr: '┤', vt: '┬', vb: '┴',
};

let cols = process.stdout.columns || 80;
let rows = process.stdout.rows || 24;

function draw() {
  cols = process.stdout.columns || 80;
  rows = process.stdout.rows || 24;
  const w = cols;
  const h = rows;
  const innerW = w - 2;

  // 先清屏再画（不用交替屏幕，纯重绘策略）
  const lines: string[] = [];

  // 顶边框
  lines.push(B.tl + B.h.repeat(innerW) + B.tr);

  // Header
  const title = ' MIA TUI | ' + w + 'x' + h + ' ';
  const titlePad = innerW - title.length;
  lines.push(B.v + title + ' '.repeat(titlePad > 0 ? titlePad : 0) + B.v);

  // 分隔线
  lines.push(B.hl + B.h.repeat(innerW) + B.hr);

  // 内容区
  for (let y = 3; y < h - 1; y++) {
    if (y === h - 2) {
      const hint = ' Ctrl+C 退出 ';
      const pad = innerW - hint.length;
      lines.push(B.v + ' '.repeat(pad > 0 ? pad : 0) + hint + B.v);
    } else {
      lines.push(B.v + ' '.repeat(innerW) + B.v);
    }
  }

  // 底边框
  lines.push(B.bl + B.h.repeat(innerW) + B.br);

  // 先归位清屏，再一次写出全部行
  process.stdout.write('\x1b[1;1H\x1b[2J' + lines.join('\n'));
}

function main() {
  readline.emitKeypressEvents(process.stdin);
  if (process.stdin.isTTY) {
    process.stdin.setRawMode(true);
  }

  process.stdout.write('\x1b[?25l'); // 隐藏光标
  draw();

  process.stdout.on('resize', draw);

  process.stdin.on('keypress', (_str, key) => {
    if (key.ctrl && key.name === 'c') {
      cleanup();
      process.exit(0);
    }
  });
}

function cleanup() {
  process.stdout.write('\x1b[?25h\x1b[2J\x1b[1;1H');
  if (process.stdin.isTTY) {
    process.stdin.setRawMode(false);
  }
  process.stdin.removeAllListeners('keypress');
  process.stdout.removeAllListeners('resize');
}

main();
