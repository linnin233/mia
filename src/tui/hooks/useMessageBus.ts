/**
 * useMessageBus hook — 订阅 MIA MessageBus 事件并更新 TUI 状态
 *
 * 监听 TUI_THOUGHT / TUI_TOOL / TUI_STATUS 消息，
 * 转换为 React dispatch actions。
 */

import { useEffect, useCallback, type Dispatch } from 'react';
import type { MessageBus } from '../../bus/bus.js';
import { MessageType } from '../../bus/message.js';
import type { TuiAction } from '../store.js';

/** 将 MIA MessageBus 事件连接到 TUI dispatch */
export function useMessageBus(
  bus: MessageBus,
  dispatch: Dispatch<TuiAction>,
  isActive: boolean,
): void {
  const pollBus = useCallback(async () => {
    while (isActive) {
      // 订阅 'tui' 来接收所有 TUI 消息
      // 注意: 需要先确保 'tui' 已订阅
      const msg = await bus.receive('tui', 100);
      if (!msg) continue;

      switch (msg.msg_type) {
        case MessageType.TUI_THOUGHT: {
          const agent = (msg.payload['agent'] as string) || '';
          const title = (msg.payload['title'] as string) || '';
          const detail = (msg.payload['detail'] as string) || '';
          dispatch({
            type: 'ADD_THOUGHT',
            entry: {
              id: msg.msg_id,
              agent,
              title,
              detail,
              timestamp: msg.timestamp,
            },
          });
          break;
        }

        case MessageType.TUI_TOOL: {
          const toolName = (msg.payload['tool_name'] as string) || '';
          const toolArgs = (msg.payload['tool_args'] as string) || '';
          const result = (msg.payload['result'] as string) || '';
          const status = (msg.payload['status'] as string) || 'running';

          if (status === 'running') {
            dispatch({
              type: 'ADD_TOOL_CALL',
              entry: {
                id: msg.msg_id,
                toolName,
                toolArgs,
                result: '',
                status: 'running',
                timestamp: msg.timestamp,
              },
            });
          } else {
            dispatch({
              type: 'UPDATE_TOOL_CALL',
              id: msg.msg_id,
              result,
              status: status as 'success' | 'error',
            });
          }
          break;
        }

        case MessageType.TUI_STATUS: {
          const key = (msg.payload['key'] as string) || '';
          const value = (msg.payload['value'] as string) || '';
          if (key === 'memory_count') {
            dispatch({
              type: 'SET_STATUS',
              memoryCount: parseInt(value, 10) || 0,
            });
          }
          break;
        }
      }
    }
  }, [bus, dispatch, isActive]);

  useEffect(() => {
    if (!isActive) return;

    // 确保 tui 订阅了 MessageBus
    bus.subscribe('tui').then(() => {
      pollBus();
    });

    return () => {
      bus.unsubscribe('tui');
    };
  }, [bus, isActive, pollBus]);
}
