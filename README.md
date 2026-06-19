# MIA — Modular Intelligent Agent

基于 **LLM 决策循环** 的多 Agent 对话系统，模拟人类"理解 → 检索 → 规划 → 执行 → 回复"的思考链路。

> **MIA** 不是某个特定模型的产品名，而是 **Agent 架构框架**。当前默认接入 MiMo API，支持任意 OpenAI 兼容 Provider 可插拔替换。

## 架构

```
  CLI / API / 微信 ──▶ Receiver ──▶ Memory ──▶ Scheduler ──┬──▶ Sender ──▶ 终端
                                                           │
                                                           ├──▶ TaskAgent (工具)
                                                           │
                                                           └──▶ WeChatAgent ──▶ 微信
```
6 个 Agent，1 条 **MessageBus**。输入多渠道，输出回原路。

## 特性

- **LLM 决策循环** — Scheduler 不断分析状态 → 派发任务 → 观察结果 → 决定回复，而非一次性生成
- **两级知识记忆** — 临时记忆 (Level 1) + 持久知识 (Level 2)，从对话中自动提炼知识点，支持跨轮关联
- **微信通信渠道** — 接入微信个人号 (iLink Bot API)，支持文字/语音/图片消息，SILK→WAV 解码 + TTS→CDN 语音发送
- **渠道感知路由** — session_id 编码消息来源，回复自动回到原渠道 (微信→微信, CLI→终端)，互不干扰
- **对话历史注入** — 每轮自动将最近 N 轮对话原文注入 LLM 上下文，解决指代和连续对话问题
- **可插拔 Provider** — 支持 OpenAI / MiMo / DeepSeek 或任意兼容 API，通过 `.env` 配置切换
- **消息总线架构** — Agent 间通过 MessageBus 松耦合通信，每个 Agent 独立运行在事件循环中
- **多模态输入** — 支持文本、图片 (MiMo VL)、语音 (MiMo-V2.5 多模态理解 — 内容+情绪+意图)
- **语音收发** — 入站 SILK→WAV 转码 (pilk) → MiMo 多模态理解；出站 TTS→CDN 上传→微信 file_item 发送
- **工具调用** — TaskAgent 支持天气查询、DuckDuckGo 搜索、Shell 命令、文件操作
- **TUI 记忆浏览器** — `/memory` 命令提供交互式知识浏览 (3 级钻取)
- **持久化存储** — 知识条目按日期分片存储，index + daily JSON 文件架构

## 快速开始

### 环境要求

- Python 3.11+
- Windows / Linux / macOS

### 安装

```bash
git clone https://github.com/linnin233/mia.git
cd mia
pip install -e ".[dev,audio,wechat]"

# 微信渠道依赖: pycryptodome (AES 加解密) + pilk (SILK 解码)
```

### 配置

创建 `.env` 文件 (项目根目录):

```bash
# 主 Provider
MIMO_API_KEY=your_api_key_here

# 可选: 备选 Provider
DEEPSEEK_API_KEY=your_deepseek_key

# 可选: Agent 行为配置
MIA_MEMORY_HISTORY_TURNS=5       # 对话历史保留轮数
MIA_MEMORY_EXTRACTION_TIMEOUT=8.0 # 知识提取超时秒数
```

### 运行

```bash
# 交互模式 (推荐)
python -m mia

# 交互模式 + 微信渠道
python -m mia --wechat

# 单次查询
python -m mia --query "你好，我叫linnin"

# 单次查询 + 图片/语音
python -m mia --query "分析这张图" --image screenshot.png
python -m mia --query "总结" --voice meeting.mp3

# HTTP API 服务器
python -m mia --server --port 8080

# 运行测试
pytest
```

### 交互命令

| 命令 | 说明 |
|------|------|
| 直接输入文本 | 开始一轮对话 |
| `/memory` | 打开 TUI 知识浏览器 (临时记忆 + 持久知识) |
| `/compact` | 压缩对话历史为知识摘要 |
| `/verbose` | 切换详细日志 |
| `/image <path>` | 发送图片 (配合下一行文字说明) |
| `/voice <path>` | 发送音频文件 (多模态理解) |
| `/record` | 从麦克风录音并发送 |
| `/help` | 显示帮助 |
| `/quit` | 退出 |

## 项目结构

```
mia/
├── src/mia/
│   ├── agents/           # Agent 实现
│   │   ├── receiver.py   # 多模态理解
│   │   ├── memory.py     # 两级记忆 + 对话历史
│   │   ├── scheduler.py  # LLM 决策循环 + 渠道感知路由
│   │   ├── task.py       # 工具调用
│   │   └── sender.py     # 终端输出 (文本/流式/语音)
│   ├── channels/         # 通信渠道 (可选)
│   │   └── wechat/       # 微信 iLink Bot 渠道
│   │       ├── agent.py  # WeChatAgent (长轮询 + 消息收发)
│   │       ├── client.py # ILinkClient (iLink HTTP API)
│   │       └── utils.py  # AES-128-ECB 加解密
│   ├── audio/            # 音频子系统
│   │   ├── recorder.py   # 麦克风录音
│   │   └── playback.py   # 本地音频播放
│   ├── memory/           # 记忆子系统
│   │   ├── store.py      # 知识存储 (index + daily JSON)
│   │   ├── retriever.py  # 关键词 + LLM 混合检索
│   │   └── browser.py    # TUI 记忆浏览器
│   ├── bus/              # 消息总线
│   ├── providers/        # LLM Provider (OpenAI/MiMo/DeepSeek)
│   ├── tools/            # 工具实现 (天气/搜索/Shell/文件)
│   ├── config.py         # 配置管理 (pydantic-settings)
│   └── main.py           # 入口: CLI 交互 + HTTP 服务
├── tests/                # 测试 (11 个)
├── workspace/            # TaskAgent 工作目录
└── pyproject.toml
```

## License

MIT
