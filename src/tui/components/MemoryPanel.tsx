/**
 * MemoryPanel 组件 — 相关记忆展示
 */

import React from 'react';
import { Box, Text } from 'ink';
import type { MemoryEntry } from '../types.js';

interface MemoryPanelProps {
  memories: MemoryEntry[];
  expanded: boolean;
}

const CATEGORY_COLORS: Record<string, string> = {
  fact: 'white',
  preference: 'cyan',
  decision: 'yellow',
  task: 'green',
  insight: 'magenta',
};

export const MemoryPanel: React.FC<MemoryPanelProps> = ({
  memories,
  expanded,
}) => (
  <Box
    flexDirection="column"
    borderStyle="round"
    borderColor="blue"
    paddingX={1}
  >
    <Text bold color="blue">
      🧠 Memory {expanded ? '▼' : '▶'} ({memories.length})
    </Text>

    {expanded &&
      (memories.length === 0 ? (
        <Text dimColor>  暂无相关记忆</Text>
      ) : (
        memories.slice(0, 8).map((m) => (
          <Box key={m.id} flexDirection="row" marginTop={1}>
            <Text color={CATEGORY_COLORS[m.category] || 'white'}>
              [{m.categoryLabel}]
            </Text>
            <Text>{' ' + m.content.slice(0, 100)}</Text>
            <Text dimColor>
              {' '}
              ({(m.confidence * 100).toFixed(0)}%)
            </Text>
          </Box>
        ))
      ))}
  </Box>
);
