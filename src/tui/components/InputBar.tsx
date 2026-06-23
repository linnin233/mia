/**
 * InputBar 组件 — 底部输入栏
 */

import React, { useState } from 'react';
import { Box, Text } from 'ink';
import TextInput from 'ink-text-input';

interface InputBarProps {
  onSubmit: (text: string) => void;
  isProcessing: boolean;
  placeholder?: string;
}

export const InputBar: React.FC<InputBarProps> = ({
  onSubmit,
  isProcessing,
  placeholder = '输入消息... (/help 查看命令)',
}) => {
  const [value, setValue] = useState('');

  const handleSubmit = (text: string) => {
    const trimmed = text.trim();
    if (trimmed) {
      onSubmit(trimmed);
      setValue('');
    }
  };

  return (
    <Box
      flexDirection="row"
      paddingX={1}
      borderStyle="single"
      borderColor="blue"
    >
      <Text color="green" bold>
        You{'>'} {' '}
      </Text>
      {isProcessing ? (
        <Text dimColor>{placeholder}</Text>
      ) : (
        <TextInput
          value={value}
          onChange={setValue}
          onSubmit={handleSubmit}
          placeholder={placeholder}
        />
      )}
    </Box>
  );
};
