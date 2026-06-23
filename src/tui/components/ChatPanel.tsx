/**
 * ChatPanel 组件 — 左侧对话区
 */

import React from 'react';
import { Box, Text, Static } from 'ink';
import type { ChatMessage } from '../types.js';

interface ChatPanelProps {
  messages: ChatMessage[];
  streamingText: string;
  isProcessing: boolean;
  width: number;
  height: number;
}

/** 格式化时间戳 */
function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** 渲染一条消息 */
const MessageItem: React.FC<{ msg: ChatMessage }> = ({ msg }) => {
  const isUser = msg.role === 'user';
  const color = isUser ? 'green' : 'cyan';
  const label = isUser ? 'You' : 'MIA';
  const time = formatTime(msg.timestamp);

  return (
    <Box flexDirection="column" marginY={1}>
      <Box>
        <Text color={color} bold>
          {label} {'> '}
        </Text>
        <Text dimColor>{time}</Text>
      </Box>
      <Box paddingLeft={2}>
        <Text>{msg.content}</Text>
      </Box>
    </Box>
  );
};

export const ChatPanel: React.FC<ChatPanelProps> = ({
  messages,
  streamingText,
  isProcessing,
  width,
  height,
}) => {
  // 历史消息（不参与重渲染）
  const historyItems = messages.slice(-50); // 保留最近 50 条

  const staticItems = historyItems.map((msg) => ({
    key: msg.id,
    element: <MessageItem msg={msg} />,
  }));

  return (
    <Box
      flexDirection="column"
      flexGrow={3}
      width={width}
      height={height}
      borderStyle="round"
      borderColor="gray"
      paddingX={1}
    >
      {/* 历史消息（Static 区域，免重复渲染） */}
      <Static items={staticItems}>
        {(item) => item.element}
      </Static>

      {/* 流式输出 */}
      {streamingText && (
        <Box flexDirection="column" marginY={1}>
          <Box>
            <Text color="cyan" bold>
              MIA {'> '}
            </Text>
          </Box>
          <Box paddingLeft={2}>
            <Text>{streamingText}</Text>
            {isProcessing && <Text color="yellow">▊</Text>}
          </Box>
        </Box>
      )}

      {/* 等待中 */}
      {isProcessing && !streamingText && (
        <Box marginY={1}>
          <Text color="yellow">MIA 思考中...</Text>
        </Box>
      )}

      {/* 空状态 */}
      {messages.length === 0 && !isProcessing && (
        <Box flexDirection="column" marginY={1}>
          <Text dimColor>欢迎使用 MIA Ink TUI!</Text>
          <Text dimColor>输入消息开始对话，/help 查看命令。</Text>
        </Box>
      )}
    </Box>
  );
};
