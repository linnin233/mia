"""
模型能力注册表 — 硬编码的模型能力真值表

职责:
  1. 定义每个模型的能力（文本推理、视觉、语音理解、TTS 等）
  2. 提供能力查询和校验函数
  3. 提供 Provider 工厂函数（根据平台名创建对应的 Provider 实例）

这是系统关于"哪个模型能做什么"的唯一真相来源。
新增模型只需在此文件中添加一条记录即可。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Set


class Capability(Enum):
    """模型能力枚举"""
    TEXT_CHAT = "text_chat"               # 文本对话/推理
    VISION = "vision"                      # 图片/视频理解
    AUDIO_UNDERSTANDING = "audio_understanding"  # 多模态音频理解（内容+情绪+意图）
    ASR = "asr"                            # 纯语音识别（语音→文字）
    TTS = "tts"                            # 语音合成（文字→语音）
    STREAMING = "streaming"                # 流式文本输出


@dataclass
class ModelInfo:
    """模型能力描述

    Attributes:
        provider: 所属平台名称 ("mimo" / "deepseek")
        capabilities: 该模型具备的能力集合
        desc: 人类可读的描述文本
    """
    provider: str
    capabilities: Set[Capability]
    desc: str


# ═══════════════════════════════════════════════════════════════
# 模型注册表 — 每个模型一条记录
# ═══════════════════════════════════════════════════════════════

MODEL_REGISTRY: dict[str, ModelInfo] = {
    # ─── MiMo 平台 ─────────────────────────────────
    "mimo-v2.5-pro": ModelInfo(
        provider="mimo",
        capabilities={Capability.TEXT_CHAT, Capability.STREAMING},
        desc="MiMo V2.5 Pro — 旗舰文本模型 (1M context, MoE 1.02T/42B)",
    ),
    "mimo-v2.5": ModelInfo(
        provider="mimo",
        capabilities={
            Capability.TEXT_CHAT,
            Capability.VISION,
            Capability.AUDIO_UNDERSTANDING,
            Capability.STREAMING,
        },
        desc="MiMo V2.5 — 全模态模型 (文本+图片+音频理解, 310B/15B MoE)",
    ),
    "mimo-v2.5-asr": ModelInfo(
        provider="mimo",
        capabilities={Capability.ASR},
        desc="MiMo V2.5 ASR — 语音识别 (95%准确率, 支持方言+中英混)",
    ),
    "mimo-v2.5-tts": ModelInfo(
        provider="mimo",
        capabilities={Capability.TTS, Capability.STREAMING},
        desc="MiMo V2.5 TTS — 语音合成 (40+语言, 200+音色, 情感表达)",
    ),

    # ─── DeepSeek 平台 ─────────────────────────────
    "deepseek-v4-pro": ModelInfo(
        provider="deepseek",
        capabilities={Capability.TEXT_CHAT, Capability.STREAMING},
        desc="DeepSeek V4 Pro — 旗舰推理模型 (1.6T/49B MoE, 1M context)",
    ),
    "deepseek-v4-flash": ModelInfo(
        provider="deepseek",
        capabilities={Capability.TEXT_CHAT, Capability.STREAMING},
        desc="DeepSeek V4 Flash — 高吞吐变体 (284B/13B MoE, 1M context)",
    ),
}


# ═══════════════════════════════════════════════════════════════
# 查询函数
# ═══════════════════════════════════════════════════════════════

def get_models_by_provider(provider_name: str) -> list[str]:
    """获取指定平台的所有模型 ID 列表"""
    return [
        mid for mid, info in MODEL_REGISTRY.items()
        if info.provider == provider_name
    ]


def get_models_with_capability(cap: Capability) -> list[str]:
    """获取所有具备指定能力的模型 ID 列表

    Args:
        cap: 目标能力

    Returns:
        模型 ID 列表（按注册顺序排列）

    Example:
        >>> get_models_with_capability(Capability.VISION)
        ['mimo-v2.5']
    """
    return [
        mid for mid, info in MODEL_REGISTRY.items()
        if cap in info.capabilities
    ]


def get_models_with_all_capabilities(caps: Set[Capability]) -> list[str]:
    """获取同时具备多个能力的模型 ID 列表（AND 逻辑）

    Args:
        caps: 需要同时具备的能力集合

    Returns:
        满足所有能力的模型 ID 列表
    """
    return [
        mid for mid, info in MODEL_REGISTRY.items()
        if caps.issubset(info.capabilities)
    ]


def validate_assignment(model_id: str, required_caps: Set[Capability]) -> None:
    """校验模型是否具备所需能力，不具备则抛出 ValueError

    Args:
        model_id: 模型 ID
        required_caps: 该任务需要的能力集合

    Raises:
        ValueError: 模型不存在
        ValueError: 模型缺少所需能力
    """
    info = MODEL_REGISTRY.get(model_id)
    if not info:
        raise ValueError(f"未知模型: {model_id}")
    missing = required_caps - info.capabilities
    if missing:
        caps_str = ", ".join(c.value for c in missing)
        raise ValueError(
            f"模型 {model_id} 缺少以下能力: {caps_str}\n"
            f"  该模型具备: {', '.join(c.value for c in info.capabilities)}"
        )


def get_model_info(model_id: str) -> Optional[ModelInfo]:
    """获取模型的完整信息，不存在返回 None"""
    return MODEL_REGISTRY.get(model_id)


# ═══════════════════════════════════════════════════════════════
# 可用模型筛选（结合 RuntimeConfig）
# ═══════════════════════════════════════════════════════════════

def get_available_models(runtime) -> list[str]:
    """获取当前可用的模型列表（Key 已配 + 开关已启用）

    同时满足两个条件才算"可用":
      1. 该模型所属平台的 API Key 已配置
      2. 该模型的开关已启用

    Args:
        runtime: RuntimeConfig 实例

    Returns:
        可用模型 ID 列表
    """
    available = []
    for model_id, info in MODEL_REGISTRY.items():
        provider = info.provider
        has_key = bool(runtime.provider_api_keys.get(provider, ""))
        enabled = runtime.model_enabled.get(model_id, False)
        if has_key and enabled:
            available.append(model_id)
    return available


def get_available_models_with_capability(runtime, cap: Capability) -> list[str]:
    """获取当前可用 + 具备指定能力的模型列表

    在 get_available_models 的基础上增加能力过滤。

    Args:
        runtime: RuntimeConfig 实例
        cap: 目标能力

    Returns:
        同时满足"可用"和"具备能力"的模型 ID 列表
    """
    available = get_available_models(runtime)
    return [mid for mid in available if cap in MODEL_REGISTRY[mid].capabilities]


# ═══════════════════════════════════════════════════════════════
# Provider 工厂
# ═══════════════════════════════════════════════════════════════

def create_provider(provider_name: str, api_key: str):
    """根据平台名创建对应的 Provider 实例

    同一个平台的多个模型共享一个 Provider 实例。
    具体用哪个模型由调用时传入的 model= 参数决定。

    Args:
        provider_name: 平台名 ("mimo" / "deepseek")
        api_key: 该平台的 API Key

    Returns:
        MiMoProvider 或 DeepSeekProvider 实例

    Raises:
        ValueError: 未知平台名
    """
    if provider_name == "mimo":
        from mia.providers.mimo import MiMoProvider
        return MiMoProvider(api_key=api_key)

    if provider_name == "deepseek":
        from mia.providers.deepseek import DeepSeekProvider
        return DeepSeekProvider(api_key=api_key)

    raise ValueError(f"未知平台: {provider_name}")


def get_all_provider_names() -> list[str]:
    """获取所有已注册的平台名称（去重）"""
    seen = set()
    result = []
    for info in MODEL_REGISTRY.values():
        if info.provider not in seen:
            seen.add(info.provider)
            result.append(info.provider)
    return result
