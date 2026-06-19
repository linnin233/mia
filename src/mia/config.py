"""
配置管理模块 — 从环境变量 / .env 文件加载所有配置

使用 pydantic-settings 实现类型安全的配置，
支持 .env 文件自动加载和环境变量覆盖。
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic_settings import BaseSettings


# 加载项目根目录的 .env 文件
_project_root = Path(__file__).parent.parent.parent
_env_file = _project_root / ".env"
if _env_file.exists():
    load_dotenv(_env_file)


class MiMoConfig(BaseSettings):
    """小米 MiMo API 配置

    API Key 类型自动识别:
      - tp- 开头 → Token Plan 网关 (token-plan-cn.xiaomimimo.com)
      - sk- 开头 → 按量付费网关 (api.xiaomimimo.com)
    """

    api_key: str = ""
    base_url: str = ""

    # 模型名
    chat_model: str = "mimo-v2.5-pro"       # 文本推理
    vision_model: str = "mimo-v2.5"          # 图片理解
    asr_model: str = "mimo-v2.5-asr"         # 语音识别
    tts_model: str = "mimo-v2.5-tts"         # 语音合成

    # 默认 TTS 音色
    default_voice: str = "冰糖"

    def get_base_url(self) -> str:
        """获取 MiMo API Base URL，自动识别 key 类型"""
        if self.base_url:
            return self.base_url
        if self.api_key.startswith("tp-"):
            return "https://token-plan-cn.xiaomimimo.com/v1"
        return "https://api.xiaomimimo.com/v1"

    model_config = {"env_prefix": "MIMO_"}


class DeepSeekConfig(BaseSettings):
    """DeepSeek API 配置 (备选 Provider)"""

    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    chat_model: str = "deepseek-chat"

    model_config = {"env_prefix": "DEEPSEEK_"}


class AgentConfig(BaseSettings):
    """Agent 行为配置"""

    # Scheduler 安全保护
    scheduler_max_iterations: int = 10
    scheduler_task_timeout: int = 60      # 单任务超时 (秒)
    scheduler_loop_timeout: int = 120     # 整轮对话超时 (秒)
    scheduler_max_consecutive_tasks: int = 3

    # 工作目录 (工具执行沙箱)
    workspace_dir: str = str(_project_root / "workspace")

    # MemoryAgent 记忆管理
    memory_history_turns: int = 5         # 对话历史保留轮数
    memory_max_working_entries: int = 30  # 临时记忆上限（触发强制合并）
    memory_extraction_timeout: float = 8.0  # 知识提取超时秒数

    # 流式输出
    enable_streaming: bool = True         # 是否启用流式输出 (MIA_ENABLE_STREAMING)

    # TUI 界面
    tui_enabled: bool = True              # 是否启用 TUI 界面 (MIA_TUI_ENABLED)
    tui_show_thoughts: bool = True        # TUI 中默认显示思考过程 (MIA_TUI_SHOW_THOUGHTS)

    model_config = {"env_prefix": "MIA_"}


class Config:
    """全局配置聚合"""

    def __init__(self):
        self.mimo = MiMoConfig()
        self.deepseek = DeepSeekConfig()
        self.agent = AgentConfig()

        # 确保工作目录存在
        Path(self.agent.workspace_dir).mkdir(parents=True, exist_ok=True)


# 全局单例
_config: Optional[Config] = None


def get_config() -> Config:
    """获取全局配置单例"""
    global _config
    if _config is None:
        _config = Config()
    return _config
