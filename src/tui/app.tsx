/**
 * MIA Ink TUI — 主 App 组件
 *
 * 多面板分屏布局:
 *   Header → [ChatPanel | SidePanel(Thinking/Tools/Memory)] → InputBar
 */

import React, { useReducer, useCallback, useEffect, useState } from 'react';
import { Box, Text, useInput } from 'ink';
import { Header } from './components/Header.js';
import { ChatPanel } from './components/ChatPanel.js';
import { ThinkingPanel } from './components/ThinkingPanel.js';
import { ToolsPanel } from './components/ToolsPanel.js';
import { MemoryPanel } from './components/MemoryPanel.js';
import { InputBar } from './components/InputBar.js';
import { useMessageBus } from './hooks/useMessageBus.js';
import { tuiReducer, createInitialState } from './store.js';
import type { MessageBus } from '../bus/bus.js';
import type { MemoryAgent } from '../agents/memory.js';
import type { ChatMessage, MemoryEntry } from './types.js';

// ─── Props ──────────────────────────────────────────────

interface AppProps {
  bus: MessageBus;
  memoryAgent: MemoryAgent | null;
  onSubmit: (text: string) => void;
  onQuit: () => void;
  initialMessages?: ChatMessage[];
  initialMemories?: MemoryEntry[];
}

// ─── App 组件 ───────────────────────────────────────────

export const App: React.FC<AppProps> = ({
  bus,
  memoryAgent,
  onSubmit,
  onQuit,
  initialMessages = [],
  initialMemories = [],
}) => {
  const [state, dispatch] = useReducer(tuiReducer, {
    ...createInitialState(),
    messages: initialMessages,
    memories: initialMemories,
  });

  const [panelIndex, setPanelIndex] = useState(0);

  // 订阅 MessageBus
  useMessageBus(bus, dispatch, true);

  // 快捷键
  useInput((input, key) => {
    if (key.escape) {
      // Esc: 返回聊天模式
      dispatch({ type: 'SET_FULLSCREEN', mode: 'chat' });
      return;
    }

    if (key.ctrl && input === 'c') {
      onQuit();
      return;
    }

    // Tab: 切换右侧面板焦点
    if (key.tab) {
      setPanelIndex((prev) => (prev + 1) % 3);
      return;
    }

    // 1/2/3: 切换面板折叠
    if (input === '1') {
      dispatch({ type: 'TOGGLE_PANEL', panel: 'thinking' });
    } else if (input === '2') {
      dispatch({ type: 'TOGGLE_PANEL', panel: 'tools' });
    } else if (input === '3') {
      dispatch({ type: 'TOGGLE_PANEL', panel: 'memory' });
    }
  });

  // 处理用户输入提交
  const handleSubmit = useCallback(
    (text: string) => {
      if (text.startsWith('/')) {
        // 本地命令
        if (text === '/quit' || text === '/q') {
          onQuit();
          return;
        }
        if (text === '/memory') {
          dispatch({ type: 'SET_FULLSCREEN', mode: 'memory' });
          return;
        }
        if (text === '/clear') {
          dispatch({ type: 'CLEAR_CHAT' });
          return;
        }
        // Toggle 面板
        if (text === '/thinking') {
          dispatch({ type: 'TOGGLE_PANEL', panel: 'thinking' });
          return;
        }
        if (text === '/tools') {
          dispatch({ type: 'TOGGLE_PANEL', panel: 'tools' });
          return;
        }
      }

      // 添加用户消息到本地状态
      dispatch({
        type: 'ADD_USER_MESSAGE',
        content: text,
        id: Date.now().toString(16),
      });

      // 通知外层处理
      onSubmit(text);
    },
    [onSubmit, onQuit],
  );

  // ─── Memory 全屏模式 ──────────────────────────
  if (state.fullscreenMode === 'memory') {
    return (
      <MemoryFullScreen
        memoryAgent={memoryAgent}
        onBack={() => dispatch({ type: 'SET_FULLSCREEN', mode: 'chat' })}
      />
    );
  }

  // ─── 聊天模式 (默认) ──────────────────────────
  return (
    <Box flexDirection="column" width="100%" height="100%">
      <Header
        model={state.status.model}
        memoryCount={state.status.memoryCount}
        sessionId={state.status.sessionId}
      />

      <Box flexDirection="row" flexGrow={1}>
        {/* 左侧聊天区 */}
        <ChatPanel
          messages={state.messages}
          streamingText={state.streamingText}
          isProcessing={state.isProcessing}
          width={60}
          height={1}
        />

        {/* 右侧信息面板 */}
        <Box
          flexDirection="column"
          flexGrow={2}
          paddingLeft={1}
          borderStyle={panelIndex === 0 ? 'bold' : undefined}
          borderColor={panelIndex === 0 ? 'cyan' : undefined}
        >
          <ThinkingPanel
            thoughts={state.thoughts}
            expanded={state.panels.thinking}
          />
          <ToolsPanel
            toolCalls={state.toolCalls}
            expanded={state.panels.tools}
          />
          <MemoryPanel
            memories={state.memories}
            expanded={state.panels.memory}
          />
        </Box>
      </Box>

      {/* 快捷键提示 */}
      <Box paddingX={1}>
        <Text dimColor>
          Tab切换面板 | 1/2/3折叠 | /memory记忆 | /clear清屏 | Esc返回 |
          Ctrl+C退出
        </Text>
      </Box>

      <InputBar
        onSubmit={handleSubmit}
        isProcessing={state.isProcessing}
      />
    </Box>
  );
};

// ─── Memory 全屏浏览器 ──────────────────────────────────

interface MemoryFullScreenProps {
  memoryAgent: MemoryAgent | null;
  onBack: () => void;
}

const MemoryFullScreen: React.FC<MemoryFullScreenProps> = ({
  memoryAgent,
  onBack,
}) => {
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 10;

  // 加载记忆
  useEffect(() => {
    if (!memoryAgent) return;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const store = (memoryAgent as any).store;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const working = (memoryAgent as any)._workingMemory || [];

    const all = [
      ...store.get_all(),
      ...working,
    ].map(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (e: any) => ({
        id: e.id,
        content: e.content,
        category: e.category,
        confidence: e.confidence,
        importance: e.importance,
        categoryLabel: e.category_label || e.category,
      }),
    ) as MemoryEntry[];

    setEntries(all);
  }, [memoryAgent]);

  const totalPages = Math.max(1, Math.ceil(entries.length / PAGE_SIZE));
  const currentEntries = entries.slice(
    page * PAGE_SIZE,
    (page + 1) * PAGE_SIZE,
  );

  useInput((_input, key) => {
    if (key.escape) onBack();
    if (key.upArrow || key.leftArrow) setPage((p) => Math.max(0, p - 1));
    if (key.downArrow || key.rightArrow)
      setPage((p) => Math.min(totalPages - 1, p + 1));
  });

  return (
    <Box flexDirection="column" padding={1}>
      <Box marginBottom={1}>
        <Text bold color="blue">
          🧠 MIA 记忆浏览器
        </Text>
        <Text dimColor>
          {' '}
          — 共 {entries.length} 条 | 第 {page + 1}/{totalPages} 页 | ↑↓翻页 |
          Esc返回
        </Text>
      </Box>

      <Box flexDirection="column" borderStyle="round" borderColor="blue" paddingX={1}>
        {currentEntries.length === 0 ? (
          <Text dimColor>记忆库为空</Text>
        ) : (
          currentEntries.map((entry) => (
            <Box key={entry.id} flexDirection="column" marginY={1}>
              <Box>
                <Text color="cyan">[{entry.categoryLabel}]</Text>
                <Text> {entry.content}</Text>
              </Box>
              <Text dimColor>
                置信度: {(entry.confidence * 100).toFixed(0)}% | 重要度:{' '}
                {(entry.importance * 100).toFixed(0)}%
              </Text>
            </Box>
          ))
        )}
      </Box>
    </Box>
  );
};
