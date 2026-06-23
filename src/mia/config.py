"""
配置管理模块 — 从环境变量 / .env 文件加载所有配置

使用 pydantic-settings 实现类型安全的配置，
支持 .env 文件自动加载和环境变量覆盖。
"""

import os
from dataclasses import dataclass, field
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


class WeChatConfig(BaseSettings):
    """微信 iLink Bot 渠道配置

    使用腾讯 iLink Bot API 接入微信个人号。
    需要 QR 码扫码登录获取 bot_token。
    """

    enabled: bool = False                 # 是否启用微信渠道
    bot_token: str = ""                   # iLink Bot token (空则自动 QR 码登录)
    bot_token_file: str = ""              # Token 持久化文件路径 (默认 ~/.mia/wechat_bot_token)
    base_url: str = "https://ilinkai.weixin.qq.com"  # iLink API 基础 URL
    media_dir: str = ""                   # 媒体文件下载目录 (默认 ~/.mia/media)

    model_config = {"env_prefix": "MIA_WECHAT_"}


class TelegramConfig(BaseSettings):
    """Telegram Bot 渠道配置

    使用标准 Telegram Bot API，只需要 Bot Token 即可认证。
    不需要 QR 码登录，比微信 iLink 简单得多。
    """

    enabled: bool = False                 # 是否启用 Telegram 渠道
    bot_token: str = ""                   # Bot Token (从 @BotFather 获取)
    bot_token_file: str = ""              # Token 持久化文件路径 (默认 ~/.mia/telegram_bot_token)

    model_config = {"env_prefix": "MIA_TELEGRAM_"}


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

    # 详细日志
    verbose: bool = True                  # 是否输出详细 Agent 思考过程 (/verbose 切换)

    model_config = {"env_prefix": "MIA_"}


@dataclass
class RuntimeConfig:
    """运行时可变配置 — 由斜杠命令 (/model /agent /channel) 在运行时修改

    与 pydantic-settings 的静态配置不同，RuntimeConfig 是纯 dataclass，
    支持在交互会话中动态修改，不会被环境变量覆盖。
    """

    # ─── 平台级 API Key（一个平台一个 Key）───
    # MiMo 平台下所有模型共享同一个 API Key
    # DeepSeek 平台下所有模型共享同一个 API Key
    # 初始值从 env 读取，运行时可通过 /model 命令修改
    provider_api_keys: dict[str, str] = field(default_factory=dict)

    # ─── 模型开关（在 Key 已配置的前提下，精确控制可用模型）───
    model_enabled: dict[str, bool] = field(default_factory=lambda: {
        "mimo-v2.5-pro": True,
        "mimo-v2.5": True,
        "mimo-v2.5-asr": True,
        "mimo-v2.5-tts": True,
        "deepseek-v4-pro": True,
        "deepseek-v4-flash": True,
    })

    # ─── Agent 模型分配 ──────────────────────────
    # Scheduler (决策引擎) — 文本推理
    scheduler_model: str = "mimo-v2.5-pro"
    scheduler_fallback: str = "deepseek-v4-flash"

    # TaskAgent (任务执行) — 文本推理 + function calling
    task_model: str = "mimo-v2.5-pro"
    task_fallback: str = "deepseek-v4-flash"

    # MemoryAgent (记忆管理) — 文本推理
    memory_model: str = "mimo-v2.5-pro"
    memory_fallback: str = "deepseek-v4-flash"

    # Receiver (输入理解) — 文本 + 视觉 + 语音
    receiver_text_model: str = "mimo-v2.5-pro"
    receiver_vision_model: str = "mimo-v2.5"
    receiver_audio_model: str = "mimo-v2.5"
    receiver_vision_enabled: bool = True
    receiver_audio_enabled: bool = True

    # Sender (输出) — TTS 语音合成
    sender_tts_model: str = "mimo-v2.5-tts"
    sender_tts_enabled: bool = True

    # WeChat Sender (微信输出) — 同 Sender
    wechat_sender_tts_enabled: bool = True

    # Telegram Sender (Telegram 输出) — 同 Sender
    telegram_sender_tts_enabled: bool = True

    # ─── 渠道开关 ────────────────────────────────
    wechat_enabled: bool = False
    telegram_enabled: bool = False

    def get_api_key(self, provider: str) -> str:
        """获取指定平台的 API Key，未配置返回空字符串"""
        return self.provider_api_keys.get(provider, "")

    def is_model_available(self, model_id: str) -> bool:
        """检查模型是否可用（Key + 开关）"""
        from mia.model_registry import MODEL_REGISTRY
        info = MODEL_REGISTRY.get(model_id)
        if not info:
            return False
        has_key = bool(self.provider_api_keys.get(info.provider, ""))
        enabled = self.model_enabled.get(model_id, False)
        return has_key and enabled


class Config:
    """全局配置聚合"""

    def __init__(self):
        self.mimo = MiMoConfig()
        self.deepseek = DeepSeekConfig()
        self.agent = AgentConfig()
        self.wechat = WeChatConfig()
        self.telegram = TelegramConfig()

        # 运行时可变配置 — 从 env 初始化 API Key
        self.runtime = RuntimeConfig(
            provider_api_keys={
                "mimo": self.mimo.api_key,
                "deepseek": self.deepseek.api_key,
            },
        )

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
