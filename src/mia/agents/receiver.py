"""
ReceiverAgent — 消息接收 Agent

职责:
  1. 接收 CLI/API 传来的原始用户输入 (RAW_INPUT)
  2. 检测输入类型 (text / image / voice)
  3. 调用 MiMo VL/ASR 理解内容
  4. 产出标准化的 USER_INTENT 发送给 Scheduler
"""

from pathlib import Path
from typing import Optional

from loguru import logger

from mia.agents.base import BaseAgent
from mia.bus.bus import MessageBus
from mia.bus.message import (
    Message,
    MessageType,
    make_user_intent,
)
from mia.providers.mimo import MiMoProvider
from mia.util import ts


class ReceiverAgent(BaseAgent):
    """消息接收 Agent — 理解用户输入并转为标准意图

    支持的输入类型:
      - text: 纯文本，直接作为意图传递
      - image: 图片路径，调用 MiMo VL 理解图片内容
      - voice: 音频路径，调用 MiMo ASR 转文字
    """

    def __init__(
        self,
        bus: MessageBus,
        mimo: MiMoProvider,
    ):
        """
        Args:
            bus: 消息总线
            mimo: MiMo Provider (用于图片理解和语音识别)
        """
        super().__init__(name="receiver", bus=bus)
        self.mimo = mimo

    async def handle(self, msg: Message) -> None:
        """处理 RAW_INPUT 消息"""
        if msg.msg_type != MessageType.RAW_INPUT:
            logger.debug("[Receiver] 忽略消息类型: {}", msg.msg_type)
            return

        session_id = msg.session_id

        # 分析输入内容
        text = msg.payload.get("text", "")
        image_path = msg.payload.get("image")
        voice_path = msg.payload.get("voice")

        intent_parts: list[str] = []
        media_refs: list[str] = []

        has_text = bool(text and text.strip())

        # ─── 处理图片 ───────────────────────────────
        if image_path:
            media_refs.append(image_path)
            img_desc = await self._understand_image(image_path, text)
            if img_desc:
                intent_parts.append(f"图片内容: {img_desc}")

        # ─── 处理语音 (多模态理解) ──────────────────
        if voice_path:
            media_refs.append(voice_path)
            voice_understanding = await self._understand_audio(voice_path, text)
            if voice_understanding:
                if has_text:
                    # 用户有文字说明 + 语音内容
                    intent_parts.append(f"用户说: {text}")
                    intent_parts.append(f"语音内容: {voice_understanding}")
                else:
                    # 纯语音输入: 音频理解结果 = 用户的消息
                    # 告诉 Scheduler 直接回应，不要分析语音本身
                    intent_parts.append(
                        f"用户发送了一段语音消息，请直接基于以下理解回复用户"
                        f"（把转写内容视为用户亲口说的话，不要分析语音本身）:\n"
                        f"{voice_understanding}"
                    )
        else:
            # 无语音: 纯文本输入
            intent_parts.append(f"用户说: {text}")

        # ─── 构建 USER_INTENT ────────────────────────
        if not intent_parts:
            intent_parts.append("用户发送了空消息")

        full_intent = "\n".join(intent_parts)

        # 透传渠道元数据（WeChat: context_token+to_user_id, Telegram: chat_id）
        context_token = msg.payload.get("context_token", "")
        to_user_id = msg.payload.get("to_user_id", "")
        chat_id = msg.payload.get("chat_id", "")

        # 结构化展示
        from mia.config import get_config
        verbose = get_config().agent.verbose
        if verbose:
            print(f"{ts()} \033[35m[Receiver]\033[0m 理解用户输入")
            print(f"   \033[90m├─\033[0m 原始输入: {text if text else '(无文本)'}")
            if image_path:
                print(f"   \033[90m├─\033[0m 图片: {image_path}")
            if voice_path:
                print(f"   \033[90m├─\033[0m 语音: {voice_path}")
            print(f"   \033[90m└─\033[0m 意图: {full_intent}")

        # 发送到 MemoryAgent → Scheduler（透传渠道元数据）
        intent_msg = make_user_intent(
            original=text or "",
            intent=full_intent,
            media_refs=media_refs,
            session_id=session_id,
            context_token=context_token,
            to_user_id=to_user_id,
            chat_id=chat_id,
        )
        await self.send(intent_msg)
        logger.info("[Receiver] USER_INTENT 已发送, intent_len={}", len(full_intent))

    # ─── 私有方法 ──────────────────────────────────────

    async def _understand_image(
        self,
        image_path: str,
        context: str = "",
    ) -> Optional[str]:
        """
        调用 MiMo VL 理解图片内容

        Args:
            image_path: 图片文件路径 或 URL
            context: 用户同时发送的文本 (可作为理解提示)

        Returns:
            图片描述文本，失败返回 None
        """
        try:
            # 判断是本地文件还是 URL
            if image_path.startswith(("http://", "https://")):
                image_data = image_path
            else:
                path = Path(image_path)
                if not path.exists():
                    logger.error("[Receiver] 图片文件不存在: {}", image_path)
                    return None
                # 根据扩展名确定 MIME 类型
                ext = path.suffix.lower()
                mime_map = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                }
                mime_type = mime_map.get(ext, "image/png")
                image_data = MiMoProvider.encode_image_file(str(path), mime_type)

            prompt = f"请详细描述这张图片的内容。{'用户同时说: ' + context if context else ''}"
            description = await self.mimo.understand_image(image_data, prompt=prompt)
            logger.info("[Receiver] 图片理解完成: {}", description)
            return description

        except Exception as e:
            logger.error("[Receiver] 图片理解失败: {}", e)
            return f"[图片理解失败: {e}]"

    async def _understand_audio(
        self,
        voice_path: str,
        context: str = "",
    ) -> Optional[str]:
        """
        多模态音频理解 — 使用 MiMo-V2.5 原生理解音频内容、情感和意图

        与旧版 _transcribe_voice() 的区别:
          - 旧: 用专用 ASR 模型 (mimo-v2.5-asr) 只做文字转写
          - 新: 用多模态模型 (mimo-v2.5) 同时理解内容、情绪、语气、意图

        MiMo-V2.5 有 261M 参数的 Audio Transformer，可以原生理解音频，
        不需要先转文字再分析的两步流程。

        Args:
            voice_path: 音频文件路径 (支持 wav/mp3/m4a/ogg)
            context: 用户同时发送的文本 (辅助理解)

        Returns:
            模型对音频的理解文本 (包含转写内容 + 情绪意图分析)，
            失败返回 None
        """
        try:
            path = Path(voice_path)
            if not path.exists():
                logger.error("[Receiver] 音频文件不存在: {}", voice_path)
                return None

            # 根据扩展名确定 MIME 类型
            ext = path.suffix.lower()
            mime_map = {
                ".wav": "audio/wav",
                ".mp3": "audio/mpeg",
                ".m4a": "audio/mp4",
                ".ogg": "audio/ogg",
            }
            mime_type = mime_map.get(ext, "audio/wav")
            audio_data = MiMoProvider.encode_audio_file(str(path), mime_type)

            # 构建多模态理解 prompt
            # 包含上下文文本（如有）帮助模型更准确理解
            context_hint = f"用户同时输入了文字: {context}" if context else ""
            prompt = (
                f"请理解这段语音内容。{context_hint}\n"
                "请完成以下任务:\n"
                "1. 转写语音的文字内容\n"
                "2. 分析说话人的情绪状态（如高兴、焦虑、愤怒、平静等）\n"
                "3. 判断说话人的意图和目的\n"
                "请简洁回复，直接给出分析结果。"
            )

            understanding = await self.mimo.understand_audio(audio_data, prompt=prompt)
            logger.info("[Receiver] 多模态音频理解完成: {}", understanding)
            return understanding

        except Exception as e:
            logger.error("[Receiver] 多模态音频理解失败: {}，降级为纯 ASR", e)
            # 降级: 多模态失败时回退到纯 ASR 转写
            try:
                audio_data = MiMoProvider.encode_audio_file(str(Path(voice_path)), "audio/wav")
                text = await self.mimo.transcribe(audio_data)
                logger.info("[Receiver] ASR 降级转写完成: {}", text)
                return f"[降级转写] {text}"
            except Exception as e2:
                logger.error("[Receiver] ASR 降级也失败: {}", e2)
                return f"[音频理解失败: {e}]"
