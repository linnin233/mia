/**
 * Header 组件 — 顶栏状态信息
 */

import React from 'react';
import { Box, Text } from 'ink';

interface HeaderProps {
  model: string;
  memoryCount: number;
  sessionId: string;
}

export const Header: React.FC<HeaderProps> = ({ model, memoryCount, sessionId }) => (
  <Box
    flexDirection="row"
    justifyContent="space-between"
    paddingX={1}
    borderStyle="single"
    borderColor="cyan"
  >
    <Box>
      <Text bold color="cyan">
        MIA v0.2.0
      </Text>
      <Text dimColor> | </Text>
      <Text color="yellow">Model: {model}</Text>
      <Text dimColor> | </Text>
      <Text color="green">Mem: {memoryCount}条</Text>
    </Box>
    <Box>
      <Text dimColor>Session: {sessionId.slice(0, 8)}</Text>
    </Box>
  </Box>
);
