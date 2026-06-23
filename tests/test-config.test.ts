/**
 * Config 配置系统单元测试
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { getConfig, resetConfig, loadMiMoConfig, loadDeepSeekConfig } from '../src/config.js';

describe('Config', () => {
  beforeEach(() => {
    resetConfig();
  });

  it('getConfig 应返回单例', () => {
    const c1 = getConfig();
    const c2 = getConfig();
    expect(c1).toBe(c2);
  });

  it('runtime 配置应有默认值', () => {
    const config = getConfig();
    expect(config.runtime.scheduler_model).toBe('mimo-v2.5-pro');
    expect(config.runtime.scheduler_max_consecutive_tasks).toBeUndefined(); // not in runtime
  });

  it('MiMo 配置应有默认模型名', () => {
    const mimo = loadMiMoConfig();
    expect(mimo.chat_model).toBe('mimo-v2.5-pro');
    expect(mimo.default_voice).toBe('冰糖');
  });

  it('DeepSeek 配置应有默认 base URL', () => {
    const deepseek = loadDeepSeekConfig();
    expect(deepseek.base_url).toBe('https://api.deepseek.com/v1');
  });
});
