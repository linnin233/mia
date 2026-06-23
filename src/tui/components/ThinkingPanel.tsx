/**
 * ThinkingPanel 组件 — Scheduler/Task Agent 思考过程
 */

import React from 'react';
import { Box, Text } from 'ink';
import type { ThoughtEntry } from '../types.js';

interface ThinkingPanelProps {
  thoughts: ThoughtEntry[];
  expanded: boolean;
}

export const ThinkingPanel: React.FC<ThinkingPanelProps> = ({
  thoughts,
  expanded,
}) => (
  <Box
    flexDirection="column"
    borderStyle="round"
    borderColor="magenta"
    paddingX={1}
    marginBottom={1}
  >
    <Text bold color="magenta">
      💭 Thinking {expanded ? '▼' : '▶'} ({thoughts.length})
    </Text>

    {expanded &&
      (thoughts.length === 0 ? (
        <Text dimColor>  暂无思考记录</Text>
      ) : (
        thoughts.slice(-5).map((t) => (
          <Box key={t.id} flexDirection="column" marginTop={1}>
            <Text color="magenta">
              [{t.agent}] {t.title}
            </Text>
            {t.detail && (
              <Text dimColor>
                {'   '}
                {t.detail.slice(0, 120)}
                {t.detail.length > 120 ? '...' : ''}
              </Text>
            )}
          </Box>
        ))
      ))}
  </Box>
);
