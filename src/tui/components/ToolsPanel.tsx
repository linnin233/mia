/**
 * ToolsPanel 组件 — 工具调用记录
 */

import React from 'react';
import { Box, Text } from 'ink';
import type { ToolCallEntry } from '../types.js';

interface ToolsPanelProps {
  toolCalls: ToolCallEntry[];
  expanded: boolean;
}

const STATUS_ICONS: Record<string, string> = {
  running: '⏳',
  success: '✅',
  error: '❌',
};

const STATUS_COLORS: Record<string, string> = {
  running: 'yellow',
  success: 'green',
  error: 'red',
};

export const ToolsPanel: React.FC<ToolsPanelProps> = ({
  toolCalls,
  expanded,
}) => (
  <Box
    flexDirection="column"
    borderStyle="round"
    borderColor="yellow"
    paddingX={1}
    marginBottom={1}
  >
    <Text bold color="yellow">
      🔧 Tools {expanded ? '▼' : '▶'} ({toolCalls.length})
    </Text>

    {expanded &&
      (toolCalls.length === 0 ? (
        <Text dimColor>  暂无工具调用</Text>
      ) : (
        toolCalls.slice(-8).map((tc) => (
          <Box key={tc.id} flexDirection="column" marginTop={1}>
            <Box>
              <Text color={STATUS_COLORS[tc.status] || 'white'}>
                {STATUS_ICONS[tc.status] || '?'} {tc.toolName}
              </Text>
            </Box>
            {tc.toolArgs && (
              <Text dimColor>{'   '}args: {tc.toolArgs.slice(0, 80)}</Text>
            )}
            {tc.result && (
              <Text color={tc.status === 'error' ? 'red' : 'white'}>
                {'   '}
                {tc.result.slice(0, 100)}
                {tc.result.length > 100 ? '...' : ''}
              </Text>
            )}
          </Box>
        ))
      ))}
  </Box>
);
