"""
CLI 斜杠命令处理 — /model /agent /channel 的 TUI 交互逻辑

所有命令处理函数返回 CommandAction 枚举，由 main.py 根据返回值
决定是否需要重建 Agent 或重新配置渠道。

设计原则:
  - 命令处理函数只做两件事: ① 修改 RuntimeConfig  ② 返回 Action
  - Agent 的生命周期管理（stop/create/start）由 main.py 统一处理
  - TUI 使用 questionary 库（项目已有依赖）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

import questionary

if TYPE_CHECKING:
    from mia.config import RuntimeConfig


class CommandAction(Enum):
    """命令执行后的操作指示

    main.py 根据此枚举决定后续操作:
      - NONE: 仅显示信息，无需操作
      - RECONFIGURE_AGENTS: Agent 模型配置已改变，需要重建 Agent
      - RECONFIGURE_WECHAT: 微信渠道开关改变，需要创建/销毁微信 Agent
    """
    NONE = "none"
    RECONFIGURE_AGENTS = "reconfigure_agents"
    RECONFIGURE_WECHAT = "reconfigure_wechat"


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _mask_key(key: str) -> str:
    """掩码显示 API Key，只显示前4后4位"""
    if not key:
        return "(未配置)"
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}****{key[-4:]}"


def _get_available_models_for_selection(
    runtime: RuntimeConfig,
    required_caps: set = None,
) -> list[str]:
    """获取可供选择的模型列表（已过滤不可用和不满足能力的）

    Args:
        runtime: 运行时配置
        required_caps: 需要的能力集合（None 表示只要文本推理即可）

    Returns:
        模型 ID 列表，附带描述信息
    """
    from mia.model_registry import (
        MODEL_REGISTRY, Capability, get_available_models,
    )

    available_ids = get_available_models(runtime)

    if required_caps is None:
        required_caps = {Capability.TEXT_CHAT}

    result = []
    for mid in available_ids:
        info = MODEL_REGISTRY.get(mid)
        if not info:
            continue
        # 检查是否满足所需能力
        if required_caps.issubset(info.capabilities):
            result.append(mid)
    return result


def _build_model_choices(model_ids: list[str], current: str) -> list:
    """构建 questionary 选择列表，标注当前使用的模型"""
    from mia.model_registry import MODEL_REGISTRY

    choices = []
    for mid in model_ids:
        info = MODEL_REGISTRY.get(mid)
        desc = info.desc if info else ""
        label = f"{mid:<22} {desc}"
        if mid == current:
            label = f"{mid:<22} {desc}  ← 当前"
        choices.append(questionary.Choice(title=label, value=mid))
    return choices


# ═══════════════════════════════════════════════════════════════
# /model — 模型平台配置
# ═══════════════════════════════════════════════════════════════

async def handle_model_command(runtime: RuntimeConfig) -> CommandAction:
    """/model 命令 — 配置 API Key 和模型开关

    两级菜单:
      第一级: 选择平台 (MiMo / DeepSeek)
      第二级: 该平台下编辑 API Key + 开关模型
    """
    from mia.model_registry import get_all_provider_names, get_models_by_provider, MODEL_REGISTRY

    action = CommandAction.NONE

    while True:
        # ─── 第一级: 选择平台 ────────────────────────
        provider_names = get_all_provider_names()
        choices = []
        for pname in provider_names:
            key = runtime.provider_api_keys.get(pname, "")
            key_status = _mask_key(key)
            enabled_count = sum(
                1 for mid in get_models_by_provider(pname)
                if runtime.model_enabled.get(mid, False)
            )
            total_count = len(get_models_by_provider(pname))
            label = (
                f"{pname.upper():8} 平台  "
                f"Key: {key_status}  "
                f"模型: {enabled_count}/{total_count} 已启用"
            )
            choices.append(questionary.Choice(title=label, value=pname))
        choices.append(questionary.Choice(title="← 返回", value="__back__"))

        choice = await questionary.select(
            "选择要配置的模型平台:",
            choices=choices,
        ).ask_async()

        if choice is None or choice == "__back__":
            return action

        # ─── 第二级: 平台详情 ────────────────────────
        provider_action = await _handle_provider_config(runtime, choice)
        if provider_action == CommandAction.RECONFIGURE_AGENTS:
            action = CommandAction.RECONFIGURE_AGENTS


async def _handle_provider_config(
    runtime: RuntimeConfig, provider_name: str,
) -> CommandAction:
    """编辑指定平台的 API Key 和模型开关"""
    from mia.model_registry import get_models_by_provider, MODEL_REGISTRY

    current_key = runtime.provider_api_keys.get(provider_name, "")
    models = get_models_by_provider(provider_name)

    while True:
        # 显示当前状态
        key_display = _mask_key(current_key)
        print()
        print(f"  {'─'*50}")
        print(f"  {provider_name.upper()} 平台配置")
        print(f"  API Key: {key_display}")
        print(f"  {'─'*50}")

        # 构建菜单: 编辑 Key + 模型开关 + 返回
        menu_choices = [
            questionary.Choice(
                title=f"[K] 编辑 API Key (当前: {key_display})",
                value="__edit_key__",
            ),
            questionary.Separator("  模型开关:"),
        ]

        for mid in models:
            info = MODEL_REGISTRY.get(mid)
            enabled = runtime.model_enabled.get(mid, False)
            status = "✓" if enabled else "✗"
            desc = info.desc if info else ""
            menu_choices.append(
                questionary.Choice(
                    title=f"    [{status}] {mid:<20} {desc}",
                    value=f"__toggle__{mid}",
                )
            )

        menu_choices.append(questionary.Separator())
        menu_choices.append(
            questionary.Choice(title="← 返回上级", value="__back__"),
        )

        choice = await questionary.select(
            f"选择操作 (空格键切换):",
            choices=menu_choices,
        ).ask_async()

        if choice is None or choice == "__back__":
            return CommandAction.NONE

        if choice == "__edit_key__":
            # 编辑 API Key — 使用 password 类型掩码输入
            new_key = await questionary.password(
                f"输入 {provider_name.upper()} 的 API Key (留空保持不变):",
            ).ask_async()
            if new_key is not None and new_key.strip():
                runtime.provider_api_keys[provider_name] = new_key.strip()
                current_key = new_key.strip()
                print(f"  ✓ API Key 已更新 ({_mask_key(current_key)})")
                # Key 变更可能影响可用模型列表 → 可能需要重建 Agent
                return CommandAction.RECONFIGURE_AGENTS
            continue

        if choice.startswith("__toggle__"):
            mid = choice[len("__toggle__"):]
            # 切换开关
            current = runtime.model_enabled.get(mid, False)
            runtime.model_enabled[mid] = not current
            new_status = "启用" if not current else "禁用"
            print(f"  ✓ {mid} 已{new_status}")
            # 模型可用性变更 → 可能需要重建 Agent
            continue


# ═══════════════════════════════════════════════════════════════
# /agent — Agent 模型分配
# ═══════════════════════════════════════════════════════════════

async def handle_agent_command(runtime: RuntimeConfig) -> CommandAction:
    """/agent 命令 — 为每个 Agent 分配模型

    两级菜单:
      第一级: 选择 Agent
      第二级: 配置该 Agent 的模型和功能开关

    功能开关会限制可选模型范围（能力校验）:
      - 开启"语音理解" → 只能选支持 AUDIO_UNDERSTANDING 的模型
      - 开启"视觉理解" → 只能选支持 VISION 的模型
      - 开启"TTS" → 只能选支持 TTS 的模型
    """
    action = CommandAction.NONE

    while True:
        # ─── 第一级: 选择 Agent ──────────────────────
        agent_choices = _build_agent_list(runtime)
        agent_choices.append(questionary.Choice(title="← 返回", value="__back__"))

        choice = await questionary.select(
            "选择要配置的 Agent:",
            choices=agent_choices,
        ).ask_async()

        if choice is None or choice == "__back__":
            return action

        # ─── 第二级: Agent 详情配置 ──────────────────
        agent_action = await _handle_agent_detail(runtime, choice)
        if agent_action == CommandAction.RECONFIGURE_AGENTS:
            action = CommandAction.RECONFIGURE_AGENTS


def _build_agent_list(runtime: RuntimeConfig) -> list:
    """构建 Agent 列表（含当前模型显示）"""
    agents = [
        ("scheduler", "Scheduler (决策引擎)",
         f"主: {runtime.scheduler_model}  备: {runtime.scheduler_fallback}"),
        ("task", "TaskAgent (任务执行)",
         f"主: {runtime.task_model}  备: {runtime.task_fallback}"),
        ("memory", "MemoryAgent (记忆管理)",
         f"主: {runtime.memory_model}  备: {runtime.memory_fallback}"),
        ("receiver", "Receiver (输入理解)",
         f"文本: {runtime.receiver_text_model}  "
         f"视觉: {'✓' if runtime.receiver_vision_enabled else '✗'}  "
         f"语音: {'✓' if runtime.receiver_audio_enabled else '✗'}"),
        ("sender", "Sender (终端输出)",
         f"TTS: {'✓' if runtime.sender_tts_enabled else '✗'} "
         f"· {runtime.sender_tts_model}"),
    ]

    # 微信渠道已启用时显示微信 Agent
    if runtime.wechat_enabled:
        agents.append(
            ("wechat_sender", "WeChat Sender (微信输出)",
             f"TTS: {'✓' if runtime.wechat_sender_tts_enabled else '✗'} "
             f"· {runtime.sender_tts_model}"),
        )

    choices = []
    for agent_id, name, detail in agents:
        choices.append(questionary.Choice(
            title=f"{name:<28} {detail}",
            value=agent_id,
        ))
    return choices


async def _handle_agent_detail(
    runtime: RuntimeConfig, agent_id: str,
) -> CommandAction:
    """配置指定 Agent 的模型和功能开关"""
    from mia.model_registry import Capability

    if agent_id in ("scheduler", "task", "memory"):
        return await _config_text_agent(runtime, agent_id)
    elif agent_id == "receiver":
        return await _config_receiver_agent(runtime)
    elif agent_id == "sender":
        return await _config_sender_agent(runtime, "sender")
    elif agent_id == "wechat_sender":
        return await _config_sender_agent(runtime, "wechat_sender")

    return CommandAction.NONE


async def _config_text_agent(
    runtime: RuntimeConfig, agent_id: str,
) -> CommandAction:
    """配置纯文本推理 Agent (Scheduler / TaskAgent / MemoryAgent)

    这些 Agent 只需要 TEXT_CHAT 能力，主模型和备选模型都可以选。
    但至少需要主模型可用。
    """
    from mia.model_registry import Capability

    # Agent 名称映射
    name_map = {
        "scheduler": "Scheduler (决策引擎)",
        "task": "TaskAgent (任务执行)",
        "memory": "MemoryAgent (记忆管理)",
    }
    agent_name = name_map.get(agent_id, agent_id)

    # 当前模型
    model_attr = f"{agent_id}_model"
    fallback_attr = f"{agent_id}_fallback"
    current_model = getattr(runtime, model_attr)
    current_fallback = getattr(runtime, fallback_attr)

    # 可用文本模型
    available = _get_available_models_for_selection(
        runtime, required_caps={Capability.TEXT_CHAT},
    )

    if not available:
        print(f"  ⚠ 没有可用的文本模型。请先用 /model 配置 API Key 并启用模型。")
        return CommandAction.NONE

    while True:
        print()
        print(f"  {'─'*50}")
        print(f"  {agent_name}")
        print(f"  主模型: {current_model}")
        print(f"  备选:   {current_fallback}")
        print(f"  {'─'*50}")

        menu = [
            questionary.Choice(
                title=f"[主] 更换主模型 (当前: {current_model})",
                value="__primary__",
            ),
            questionary.Choice(
                title=f"[备] 更换备选模型 (当前: {current_fallback})",
                value="__fallback__",
            ),
            questionary.Choice(title="← 返回上级", value="__back__"),
        ]

        choice = await questionary.select(
            "选择操作:",
            choices=menu,
        ).ask_async()

        if choice is None or choice == "__back__":
            return CommandAction.NONE

        if choice == "__primary__":
            model_choices = _build_model_choices(available, current_model)
            new_model = await questionary.select(
                f"选择 {agent_name} 的主模型:",
                choices=model_choices + [
                    questionary.Choice(title="← 取消", value="__cancel__"),
                ],
            ).ask_async()

            if new_model and new_model != "__cancel__":
                setattr(runtime, model_attr, new_model)
                current_model = new_model
                print(f"  ✓ 主模型已更改为: {new_model}")
                return CommandAction.RECONFIGURE_AGENTS

        if choice == "__fallback__":
            fb_choices = _build_model_choices(available, current_fallback)
            # 备选可以是空（不使用备选）
            fb_choices.insert(0, questionary.Choice(
                title="(不使用备选)",
                value="",
            ))
            new_fallback = await questionary.select(
                f"选择 {agent_name} 的备选模型:",
                choices=fb_choices + [
                    questionary.Choice(title="← 取消", value="__cancel__"),
                ],
            ).ask_async()

            if new_fallback is not None and new_fallback != "__cancel__":
                setattr(runtime, fallback_attr, new_fallback)
                current_fallback = new_fallback
                fb_display = new_fallback or "(无)"
                print(f"  ✓ 备选模型已更改为: {fb_display}")
                return CommandAction.RECONFIGURE_AGENTS

    return CommandAction.NONE


async def _config_receiver_agent(runtime: RuntimeConfig) -> CommandAction:
    """配置 Receiver Agent — 文本/视觉/语音三个维度"""
    from mia.model_registry import Capability

    action = CommandAction.NONE

    # 预计算可用模型列表
    text_models = _get_available_models_for_selection(
        runtime, required_caps={Capability.TEXT_CHAT},
    )
    vision_models = _get_available_models_for_selection(
        runtime, required_caps={Capability.TEXT_CHAT, Capability.VISION},
    )
    audio_models = _get_available_models_for_selection(
        runtime, required_caps={Capability.AUDIO_UNDERSTANDING},
    )

    while True:
        print()
        print(f"  {'─'*50}")
        print(f"  Receiver (输入理解)")
        print(f"  文本模型: {runtime.receiver_text_model}")
        vis_status = "✓ 开启" if runtime.receiver_vision_enabled else "✗ 关闭"
        aud_status = "✓ 开启" if runtime.receiver_audio_enabled else "✗ 关闭"
        print(f"  视觉理解: {vis_status} · {runtime.receiver_vision_model}")
        print(f"  语音理解: {aud_status} · {runtime.receiver_audio_model}")
        print(f"  {'─'*50}")

        menu = [
            questionary.Choice(
                title=f"[文本] 更换文本模型 (当前: {runtime.receiver_text_model})",
                value="__text__",
            ),
            questionary.Choice(
                title=f"[视觉] {'✓' if runtime.receiver_vision_enabled else '✗'} 视觉理解  "
                       f"({'开启' if runtime.receiver_vision_enabled else '关闭'}) "
                       f"· {runtime.receiver_vision_model}",
                value="__vision__",
            ),
            questionary.Choice(
                title=f"[语音] {'✓' if runtime.receiver_audio_enabled else '✗'} 语音理解  "
                       f"({'开启' if runtime.receiver_audio_enabled else '关闭'}) "
                       f"· {runtime.receiver_audio_model}",
                value="__audio__",
            ),
            questionary.Choice(title="← 返回上级", value="__back__"),
        ]

        choice = await questionary.select(
            "选择操作:",
            choices=menu,
        ).ask_async()

        if choice is None or choice == "__back__":
            return action

        if choice == "__text__":
            if not text_models:
                print(f"  ⚠ 没有可用的文本模型。")
                continue
            model_choices = _build_model_choices(text_models, runtime.receiver_text_model)
            new_model = await questionary.select(
                "选择 Receiver 的文本模型:",
                choices=model_choices + [
                    questionary.Choice(title="← 取消", value="__cancel__"),
                ],
            ).ask_async()
            if new_model and new_model != "__cancel__":
                runtime.receiver_text_model = new_model
                print(f"  ✓ 文本模型已更改为: {new_model}")
                action = CommandAction.RECONFIGURE_AGENTS

        elif choice == "__vision__":
            # 子菜单: 开关视觉 + 选模型
            sub_action = await _config_receiver_vision(runtime, vision_models)
            if sub_action == CommandAction.RECONFIGURE_AGENTS:
                action = CommandAction.RECONFIGURE_AGENTS

        elif choice == "__audio__":
            # 子菜单: 开关语音 + 选模型
            sub_action = await _config_receiver_audio(runtime, audio_models)
            if sub_action == CommandAction.RECONFIGURE_AGENTS:
                action = CommandAction.RECONFIGURE_AGENTS

    return action


async def _config_receiver_vision(
    runtime: RuntimeConfig, vision_models: list[str],
) -> CommandAction:
    """配置 Receiver 视觉理解"""
    menu = []
    if runtime.receiver_vision_enabled:
        menu.append(questionary.Choice(
            title="[关闭] 关闭视觉理解",
            value="__toggle_off__",
        ))
    else:
        menu.append(questionary.Choice(
            title="[开启] 开启视觉理解 (需要视觉模型)",
            value="__toggle_on__",
        ))

    if runtime.receiver_vision_enabled and vision_models:
        menu.append(questionary.Choice(
            title=f"[模型] 更换视觉模型 (当前: {runtime.receiver_vision_model})",
            value="__change_model__",
        ))

    menu.append(questionary.Choice(title="← 取消", value="__cancel__"))

    choice = await questionary.select(
        "视觉理解配置:",
        choices=menu,
    ).ask_async()

    if choice == "__toggle_on__":
        # 开启前检查是否有可用视觉模型
        if not vision_models:
            print(f"  ⚠ 没有可用的视觉模型。请先配置 MiMo API Key 并启用 mimo-v2.5。")
            return CommandAction.NONE
        runtime.receiver_vision_enabled = True
        print(f"  ✓ 视觉理解已开启")
        return CommandAction.RECONFIGURE_AGENTS

    if choice == "__toggle_off__":
        runtime.receiver_vision_enabled = False
        print(f"  ✓ 视觉理解已关闭")
        return CommandAction.RECONFIGURE_AGENTS

    if choice == "__change_model__":
        model_choices = _build_model_choices(vision_models, runtime.receiver_vision_model)
        new_model = await questionary.select(
            "选择视觉模型 (需要支持视觉):",
            choices=model_choices + [
                questionary.Choice(title="← 取消", value="__cancel__"),
            ],
        ).ask_async()
        if new_model and new_model != "__cancel__":
            runtime.receiver_vision_model = new_model
            print(f"  ✓ 视觉模型已更改为: {new_model}")
            return CommandAction.RECONFIGURE_AGENTS

    return CommandAction.NONE


async def _config_receiver_audio(
    runtime: RuntimeConfig, audio_models: list[str],
) -> CommandAction:
    """配置 Receiver 语音理解"""
    menu = []
    if runtime.receiver_audio_enabled:
        menu.append(questionary.Choice(
            title="[关闭] 关闭语音理解",
            value="__toggle_off__",
        ))
    else:
        menu.append(questionary.Choice(
            title="[开启] 开启语音理解 (需要音频模型)",
            value="__toggle_on__",
        ))

    if runtime.receiver_audio_enabled and audio_models:
        menu.append(questionary.Choice(
            title=f"[模型] 更换语音模型 (当前: {runtime.receiver_audio_model})",
            value="__change_model__",
        ))

    menu.append(questionary.Choice(title="← 取消", value="__cancel__"))

    choice = await questionary.select(
        "语音理解配置:",
        choices=menu,
    ).ask_async()

    if choice == "__toggle_on__":
        if not audio_models:
            print(f"  ⚠ 没有可用的音频模型。请先配置 MiMo API Key 并启用 mimo-v2.5。")
            return CommandAction.NONE
        runtime.receiver_audio_enabled = True
        print(f"  ✓ 语音理解已开启")
        return CommandAction.RECONFIGURE_AGENTS

    if choice == "__toggle_off__":
        runtime.receiver_audio_enabled = False
        print(f"  ✓ 语音理解已关闭")
        return CommandAction.RECONFIGURE_AGENTS

    if choice == "__change_model__":
        model_choices = _build_model_choices(audio_models, runtime.receiver_audio_model)
        new_model = await questionary.select(
            "选择语音模型 (需要支持音频理解):",
            choices=model_choices + [
                questionary.Choice(title="← 取消", value="__cancel__"),
            ],
        ).ask_async()
        if new_model and new_model != "__cancel__":
            runtime.receiver_audio_model = new_model
            print(f"  ✓ 语音模型已更改为: {new_model}")
            return CommandAction.RECONFIGURE_AGENTS

    return CommandAction.NONE


async def _config_sender_agent(
    runtime: RuntimeConfig, sender_type: str,
) -> CommandAction:
    """配置 Sender Agent — TTS 开关 + 模型选择"""
    from mia.model_registry import Capability

    if sender_type == "wechat_sender":
        agent_name = "WeChat Sender (微信输出)"
        tts_enabled_attr = "wechat_sender_tts_enabled"
    else:
        agent_name = "Sender (终端输出)"
        tts_enabled_attr = "sender_tts_enabled"

    tts_enabled = getattr(runtime, tts_enabled_attr)
    tts_models = _get_available_models_for_selection(
        runtime, required_caps={Capability.TTS},
    )

    while True:
        print()
        print(f"  {'─'*50}")
        print(f"  {agent_name}")
        tts_status = "✓ 开启" if tts_enabled else "✗ 关闭"
        print(f"  TTS: {tts_status} · {runtime.sender_tts_model}")
        print(f"  {'─'*50}")

        menu = []
        if tts_enabled:
            menu.append(questionary.Choice(
                title="[关闭] 关闭 TTS 语音合成",
                value="__toggle_off__",
            ))
        else:
            menu.append(questionary.Choice(
                title="[开启] 开启 TTS 语音合成 (需要 TTS 模型)",
                value="__toggle_on__",
            ))

        if tts_enabled and tts_models:
            menu.append(questionary.Choice(
                title=f"[模型] 更换 TTS 模型 (当前: {runtime.sender_tts_model})",
                value="__change_model__",
            ))

        menu.append(questionary.Choice(title="← 返回上级", value="__back__"))

        choice = await questionary.select(
            f"选择操作:",
            choices=menu,
        ).ask_async()

        if choice is None or choice == "__back__":
            return CommandAction.NONE

        if choice == "__toggle_on__":
            if not tts_models:
                print(f"  ⚠ 没有可用的 TTS 模型。请先配置 MiMo API Key 并启用 mimo-v2.5-tts。")
                continue
            setattr(runtime, tts_enabled_attr, True)
            tts_enabled = True
            print(f"  ✓ TTS 已开启")
            return CommandAction.RECONFIGURE_AGENTS

        if choice == "__toggle_off__":
            setattr(runtime, tts_enabled_attr, False)
            tts_enabled = False
            print(f"  ✓ TTS 已关闭")
            return CommandAction.RECONFIGURE_AGENTS

        if choice == "__change_model__":
            model_choices = _build_model_choices(tts_models, runtime.sender_tts_model)
            new_model = await questionary.select(
                "选择 TTS 模型:",
                choices=model_choices + [
                    questionary.Choice(title="← 取消", value="__cancel__"),
                ],
            ).ask_async()
            if new_model and new_model != "__cancel__":
                runtime.sender_tts_model = new_model
                print(f"  ✓ TTS 模型已更改为: {new_model}")
                return CommandAction.RECONFIGURE_AGENTS

    return CommandAction.NONE


# ═══════════════════════════════════════════════════════════════
# /channel — 通信渠道配置
# ═══════════════════════════════════════════════════════════════

async def handle_channel_command(runtime: RuntimeConfig) -> CommandAction:
    """/channel 命令 — 配置通信渠道（微信等）

    开启微信渠道时会创建 WeChatReceiverAgent + WeChatSenderAgent
    关闭时会销毁它们。
    """
    from mia.config import get_config

    while True:
        print()
        print(f"  {'─'*50}")
        print(f"  通信渠道配置")
        print(f"  {'─'*50}")

        wechat_label = "✓ 已启用" if runtime.wechat_enabled else "✗ 未启用"
        print(f"  微信 (iLink Bot): {wechat_label}")
        print()

        menu = []
        if runtime.wechat_enabled:
            menu.append(questionary.Choice(
                title="[关闭] 关闭微信渠道",
                value="__toggle_off__",
            ))
        else:
            menu.append(questionary.Choice(
                title="[开启] 开启微信渠道 (iLink Bot 长轮询 + QR 码登录)",
                value="__toggle_on__",
            ))

        # 编辑 Token（仅在当前已启用或曾配置过时显示）
        config = get_config()
        has_token = bool(config.wechat.bot_token)
        if has_token:
            menu.append(questionary.Choice(
                title=f"[Token] 编辑 Bot Token (当前: {_mask_key(config.wechat.bot_token)})",
                value="__edit_token__",
            ))

        menu.append(questionary.Separator("  (未来: Telegram, Discord, Slack...)"))

        menu.append(questionary.Choice(title="← 返回", value="__back__"))

        choice = await questionary.select(
            "选择操作:",
            choices=menu,
        ).ask_async()

        if choice is None or choice == "__back__":
            return CommandAction.NONE

        if choice == "__toggle_on__":
            runtime.wechat_enabled = True
            print(f"  ✓ 微信渠道已开启")
            print(f"  ℹ 如果未配置 Bot Token，启动时会自动弹出 QR 码登录")
            return CommandAction.RECONFIGURE_WECHAT

        if choice == "__toggle_off__":
            # 关闭前确认
            confirm = await questionary.confirm(
                "确认关闭微信渠道？这会导致 WeChat Agent 停止工作。",
                default=False,
            ).ask_async()
            if confirm:
                runtime.wechat_enabled = False
                print(f"  ✓ 微信渠道已关闭")
                return CommandAction.RECONFIGURE_WECHAT

        if choice == "__edit_token__":
            new_token = await questionary.password(
                "输入 iLink Bot Token (留空保持不变):",
            ).ask_async()
            if new_token is not None and new_token.strip():
                config.wechat.bot_token = new_token.strip()
                print(f"  ✓ Bot Token 已更新")
                # Token 变更不会自动生效，需要重建 WeChat Agent
                if runtime.wechat_enabled:
                    return CommandAction.RECONFIGURE_WECHAT

    return CommandAction.NONE
