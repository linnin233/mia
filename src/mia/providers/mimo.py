"""
MiMo Provider — 封装 Xiaomi MiMo 平台的 API

基于 linnin-agent 的 MiniMaxProvider 实现，适配 mia 项目架构。

支持的模型:
  - mimo-v2.5-pro  : 旗舰文本模型 (1M context, reasoning)
  - mimo-v2.5      : 多模态模型 (图片/音频/视频理解)
  - mimo-v2.5-asr  : 语音识别 (audio → text)
  - mimo-v2.5-tts  : 语音合成 (text → audio)

认证方式: api-key header (OpenAI 兼容)
"""

import base64
import json
from typing import AsyncIterator, Optional

import httpx
from loguru import logger
from openai import AsyncOpenAI

from mia.providers.base import BaseProvider


class MiMoProvider(BaseProvider):
    """Xiaomi MiMo 平台 API 封装 — 全部 OpenAI 兼容协议

    自动识别 API Key 类型:
      - tp- 开头 → Token Plan 网关 (token-plan-cn.xiaomimimo.com)
      - sk- 开头 → 按量付费网关 (api.xiaomimimo.com)
    """

    # 默认模型
    CHAT_MODEL = "mimo-v2.5-pro"       # 文本推理
    VISION_MODEL = "mimo-v2.5"          # 多模态 (图片/音频/视频)
    ASR_MODEL = "mimo-v2.5-asr"         # 语音识别
    TTS_MODEL = "mimo-v2.5-tts"         # 语音合成

    # 默认 TTS 音色
    DEFAULT_VOICE = "冰糖"

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        """
        Args:
            api_key: MiMo API Key (tp-xxxxx 或 sk-xxxxx)
            base_url: 自定义 base URL (None 则根据 key 类型自动选择)
        """
        if not base_url:
            if api_key.startswith("tp-"):
                base_url = "https://token-plan-cn.xiaomimimo.com/v1"
            else:
                base_url = "https://api.xiaomimimo.com/v1"

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(120.0, connect=30.0),
            max_retries=2,
        )
        self._base_url = base_url
        logger.info("MiMoProvider 初始化完成, base_url={}", base_url)

    # ─── 对话 (Chat) — 实现 BaseProvider 接口 ──────────────────

    async def chat(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        stream: bool = True,
        tools: Optional[list[dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ):
        """流式/非流式对话 — OpenAI 兼容"""
        kwargs = {
            "model": model or self.CHAT_MODEL,
            "messages": messages,
            "stream": stream,
            "max_tokens": max_tokens,
            "temperature": temperature,
            # 注意: 不再传 thinking: disabled
            # Anthropic 格式 {"thinking": {"type": "disabled"}} 在 MiMo OpenAI 兼容端点下
            # 可能被忽略或触发兼容性问题 (400 Param Incorrect)
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        logger.debug(
            "MiMo chat: model={}, stream={}, msg_count={}",
            kwargs["model"], stream, len(messages),
        )
        return await self.client.chat.completions.create(**kwargs)

    async def chat_sync(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """非流式对话 — 返回完整文本"""
        response = await self.chat(
            messages=messages,
            model=model,
            stream=False,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    async def chat_stream(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """流式对话 — 逐 token 返回文本增量

        调用 self.chat(stream=True) 获取 OpenAI 兼容的 SSE 流，
        然后逐个 chunk 提取 delta.content 并 yield。

        用法:
            async for delta in provider.chat_stream(messages, ...):
                print(delta, end="", flush=True)
        """
        stream = await self.chat(
            messages=messages,
            model=model,
            stream=True,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        async for chunk in stream:
            # OpenAI 兼容流格式: chunk.choices[0].delta.content
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    # ─── 图片理解 (Vision) ───────────────────────────────────────

    async def understand_image(
        self,
        image_data: str,
        prompt: str = "请详细描述这张图片的内容",
        model: Optional[str] = None,
    ) -> str:
        """
        图片理解 — 支持 URL 或 Base64 图片

        Args:
            image_data: 图片 URL 或 data:image/xxx;base64,... 格式
            prompt: 理解问题/指令
            model: 模型名 (默认 mimo-v2.5)

        Returns:
            模型对图片的描述文本
        """
        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_data}},
                {"type": "text", "text": prompt},
            ],
        }]

        response = await self.client.chat.completions.create(
            model=model or self.VISION_MODEL,
            messages=messages,
            max_tokens=1024,
            stream=False,
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def encode_image_file(path: str, mime_type: str = "image/png") -> str:
        """将本地图片文件编码为 base64 data URL"""
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime_type};base64,{b64}"

    # ─── 语音识别 (ASR) — 单独 ASR 模型 ──────────────────────────

    async def transcribe(
        self,
        audio_data: str,
        language: str = "auto",
    ) -> str:
        """
        语音识别 — 使用专用 ASR 模型将音频转为纯文本

        注意: 此方法使用 mimo-v2.5-asr 模型，只做文字转写。
        如需理解语气/情感/意图，请使用 understand_audio()。

        Args:
            audio_data: data:audio/xxx;base64,... 格式的音频
            language: 语种 (auto/zh/en)

        Returns:
            识别出的文本
        """
        messages = [{
            "role": "user",
            "content": [{
                "type": "input_audio",
                "input_audio": {"data": audio_data},
            }],
        }]

        response = await self.client.chat.completions.create(
            model=self.ASR_MODEL,
            messages=messages,
            stream=False,
            extra_body={"asr_options": {"language": language}},
        )
        return response.choices[0].message.content or ""

    # ─── 多模态音频理解 (MiMo-V2.5 原生) ──────────────────────

    async def understand_audio(
        self,
        audio_data: str,
        prompt: str = "请转写这段语音的内容，并分析说话人的情绪和意图。",
        model: Optional[str] = None,
    ) -> str:
        """
        多模态音频理解 — 使用 MiMo-V2.5 原生理解音频内容、语气、情感和意图

        与 transcribe() 的区别:
          - transcribe() 用专用 ASR 模型 (mimo-v2.5-asr)，只做文字转写
          - understand_audio() 用多模态模型 (mimo-v2.5)，可以同时理解:
            · 文字内容 (转写)
            · 说话人情绪
            · 语气/语调
            · 意图/目的
            · 背景信息

        MiMo-V2.5 有 261M 参数的 Audio Transformer，可以原生理解音频，
        不需要先转文字再分析的两步流程。

        Args:
            audio_data: data:audio/xxx;base64,... 格式的音频
            prompt: 理解指令 (可以要求转写、总结、分析情绪等)
            model: 模型名 (默认 mimo-v2.5 多模态)

        Returns:
            模型对音频的理解文本 (包含转写内容和分析)
        """
        messages = [{
            "role": "user",
            "content": [
                {"type": "input_audio", "input_audio": {"data": audio_data}},
                {"type": "text", "text": prompt},
            ],
        }]

        response = await self.client.chat.completions.create(
            model=model or self.VISION_MODEL,  # mimo-v2.5 多模态
            messages=messages,
            max_tokens=1024,
            stream=False,
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def encode_audio_file(path: str, mime_type: str = "audio/wav") -> str:
        """将本地音频文件编码为 base64 data URL"""
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime_type};base64,{b64}"

    # ─── 语音合成 (TTS) ──────────────────────────────────────────

    async def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        audio_format: str = "wav",
        instructions: Optional[str] = None,
    ) -> bytes:
        """
        语音合成 — 将文本转为语音音频 (非流式)

        Args:
            text: 要合成的文本 (放在 assistant 消息中)
            voice: 音色 ID (冰糖/茉莉/苏打/白桦/Mia/Chloe/Milo/Dean)
            audio_format: 输出格式 (wav/pcm16)
            instructions: 风格控制指令 (放在 user 消息中)

        Returns:
            音频文件的二进制数据 (WAV 或 PCM16)
        """
        messages = [
            {"role": "user", "content": instructions or ""},
            {"role": "assistant", "content": text},
        ]

        response = await self.client.chat.completions.create(
            model=self.TTS_MODEL,
            messages=messages,
            stream=False,
            extra_body={
                "audio": {
                    "format": audio_format,
                    "voice": voice or self.DEFAULT_VOICE,
                }
            },
        )

        # TTS 返回的 audio data 是 base64 编码的
        audio_b64 = response.choices[0].message.audio.data
        return base64.b64decode(audio_b64)

    async def synthesize_stream(
        self,
        text: str,
        voice: Optional[str] = None,
        instructions: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        """
        流式语音合成 — 边生成边返回 PCM16 音频片段

        Args:
            text: 要合成的文本
            voice: 音色 ID
            instructions: 风格控制指令

        Yields:
            逐块 pcm16 音频数据
        """
        messages = [
            {"role": "user", "content": instructions or ""},
            {"role": "assistant", "content": text},
        ]

        stream = await self.client.chat.completions.create(
            model=self.TTS_MODEL,
            messages=messages,
            stream=True,
            extra_body={
                "audio": {
                    "format": "pcm16",
                    "voice": voice or self.DEFAULT_VOICE,
                }
            },
        )

        async for chunk in stream:
            if not chunk.choices:
                continue
            audio = getattr(chunk.choices[0].delta, "audio", None)
            if audio and audio.get("data"):
                yield base64.b64decode(audio["data"])
