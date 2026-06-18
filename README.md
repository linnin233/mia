# MIA — MiMo Intelligent Agent

基于 LLM 循环的多 Agent 系统，仿人类思考行为。

## 架构

```
User Input → ReceiverAgent (MiMo VL/ASR) → SchedulerAgent (LLM Loop) → SenderAgent / TaskAgent
```

## 快速开始

```bash
pip install -e .
python -m mia --query "你好"
python -m mia                    # 交互模式
python -m mia --server --port 8080  # HTTP API
```
