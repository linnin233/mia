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
    # Telegram 渠道已启用时显示 Telegram Agent
    if runtime.telegram_enabled:
        agents.append(
            ("telegram_sender", "Telegram Sender (TG输出)",
             f"TTS: {'✓' if runtime.telegram_sender_tts_enabled else '✗'} "
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
    elif agent_id == "telegram_sender":
        return await _config_sender_agent(runtime, "telegram_sender")

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
    elif sender_type == "telegram_sender":
        agent_name = "Telegram Sender (TG输出)"
        tts_enabled_attr = "telegram_sender_tts_enabled"
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
# /interface — 消息接口管理（查看绑定、重新绑定、删除绑定）
# ═══════════════════════════════════════════════════════════════

async def handle_interface_command(runtime: RuntimeConfig) -> CommandAction:
    """/interface 命令 — 消息接口绑定管理

    与 /channel 的职责分离:
      - /channel: 开关渠道（是否启用微信收发）
      - /interface: 管理绑定凭证（查看 token、重新扫码登录、删除绑定）

    第一级: 选择消息接口（当前只有微信，未来可扩展 Telegram/Discord 等）
    第二级: 查看 token 详情 / 重新绑定 / 删除绑定
    """
    from pathlib import Path

    from mia.config import get_config

    config = get_config()
    action = CommandAction.NONE

    while True:
        # ─── 第一级: 选择消息接口 ──────────────────────
        interfaces = _get_available_interfaces(runtime, config)
        choices = [questionary.Choice(title=iface["label"], value=iface["id"]) for iface in interfaces]
        choices.append(questionary.Choice(title="← 返回", value="__back__"))

        choice = await questionary.select(
            "选择要管理的消息接口:",
            choices=choices,
        ).ask_async()

        if choice is None or choice == "__back__":
            return action

        # ─── 第二级: 接口详情管理 ──────────────────────
        if choice == "wechat":
            iface_action = await _handle_wechat_interface(runtime, config)
            if iface_action == CommandAction.RECONFIGURE_WECHAT:
                action = CommandAction.RECONFIGURE_WECHAT
        elif choice == "telegram":
            iface_action = await _handle_telegram_interface(runtime, config)
            if iface_action == CommandAction.RECONFIGURE_WECHAT:
                action = CommandAction.RECONFIGURE_WECHAT

    return action


def _get_available_interfaces(runtime: RuntimeConfig, config) -> list[dict]:
    """扫描可用的消息接口列表

    Returns:
        列表，每项包含 id, label（含状态信息），用于第一级菜单展示
    """
    from pathlib import Path

    # ─── 微信接口 ──────────────────────────────────
    token_file = _get_wechat_token_file(config)
    has_token_file = token_file.exists()
    has_env_token = bool(config.wechat.bot_token)
    has_saved_token = has_token_file or has_env_token
    channel_enabled = runtime.wechat_enabled

    # 状态描述
    if channel_enabled:
        if has_saved_token:
            status = "✓ 已启用 · 已绑定"
        else:
            status = "✓ 已启用 · 未绑定 (需扫码)"
    else:
        if has_saved_token:
            status = "✗ 未启用 · 已绑定"
        else:
            status = "✗ 未启用 · 未绑定"

    wechat_label = f"微信 (iLink Bot)    {status}"

    # ─── Telegram 接口 ──────────────────────────────
    tg_token_file = Path.home() / ".mia" / "telegram_bot_token"
    has_tg_token = tg_token_file.exists() or bool(config.telegram.bot_token)
    tg_enabled = runtime.telegram_enabled

    if tg_enabled:
        tg_status = "✓ 已启用 · 已绑定" if has_tg_token else "✓ 已启用 · 未配置 Token"
    else:
        tg_status = "✗ 未启用 · 已绑定" if has_tg_token else "✗ 未启用 · 未配置"

    tg_label = f"Telegram (Bot API)   {tg_status}"

    interfaces = [
        {"id": "wechat", "label": wechat_label},
        {"id": "telegram", "label": tg_label},
        # 未来扩展点: Discord, Slack...
    ]

    return interfaces


def _get_wechat_token_file(config) -> Path:
    """获取微信 bot_token 持久化文件路径"""
    from pathlib import Path

    token_file_str = config.wechat.bot_token_file
    if token_file_str:
        return Path(token_file_str).expanduser()
    return Path.home() / ".mia" / "wechat_bot_token"


def _get_wechat_context_tokens_file(config) -> Path:
    """获取微信 context_tokens 持久化文件路径"""
    token_file = _get_wechat_token_file(config)
    return token_file.parent / "wechat_context_tokens.json"


async def _handle_wechat_interface(
    runtime: RuntimeConfig, config,
) -> CommandAction:
    """微信接口详情管理 — 查看信息 / 重新绑定 / 删除绑定

    二级子菜单，展示 token 文件状态并提供操作选项。
    """
    from datetime import datetime
    from pathlib import Path

    token_file = _get_wechat_token_file(config)
    ctx_file = _get_wechat_context_tokens_file(config)

    action = CommandAction.NONE

    while True:
        # 每次进入都重新扫描文件状态
        _print_wechat_status(runtime, config, token_file, ctx_file)

        # 构建菜单
        menu = _build_wechat_interface_menu(runtime, config, token_file)

        choice = await questionary.select(
            "选择操作:",
            choices=menu,
        ).ask_async()

        if choice is None or choice == "__back__":
            return action

        if choice == "__rebind__":
            result = await _do_wechat_rebind(config, token_file)
            if result == CommandAction.RECONFIGURE_WECHAT:
                action = CommandAction.RECONFIGURE_WECHAT

        elif choice == "__delete__":
            result = await _do_wechat_delete_binding(config, token_file, ctx_file)
            if result == CommandAction.RECONFIGURE_WECHAT:
                action = CommandAction.RECONFIGURE_WECHAT
                # 删除后回到上级，因为状态已完全改变
                print()
                return action

    return action


def _print_wechat_status(runtime, config, token_file, ctx_file) -> None:
    """打印微信接口当前状态信息"""
    from datetime import datetime

    print()
    print(f"  {'─'*50}")
    print(f"  微信接口 (iLink Bot)")
    print(f"  {'─'*50}")

    # ─── 渠道开关状态 ──────────────────────────────
    channel_status = "✓ 已启用" if runtime.wechat_enabled else "✗ 未启用"
    print(f"  渠道状态:    {channel_status}")
    print()

    # ─── Token 文件信息 ────────────────────────────
    print(f"  ── Bot Token ──")
    if token_file.exists():
        try:
            stat = token_file.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size_kb = stat.st_size / 1024
            # 读 token 并掩码
            token_raw = token_file.read_text(encoding="utf-8").strip()
            print(f"  文件路径:    {token_file}")
            print(f"  文件大小:    {size_kb:.1f} KB")
            print(f"  最后修改:    {mtime}")
            print(f"  Token:       {_mask_key(token_raw)}")
        except Exception:
            print(f"  文件路径:    {token_file}")
            print(f"  \033[33m  ⚠ 无法读取 token 文件\033[0m")
    else:
        print(f"  状态:        \033[90m未保存 (无 token 文件)\033[0m")
        print(f"  预期路径:    {token_file}")

    # 环境变量 token 提示
    if config.wechat.bot_token and not token_file.exists():
        print(f"  Token:       {_mask_key(config.wechat.bot_token)} (来自环境变量 MIA_WECHAT_BOT_TOKEN)")

    print()

    # ─── Context Tokens 文件信息 ────────────────────
    print(f"  ── Context Tokens (用户路由缓存) ──")
    _print_context_tokens_status(ctx_file)


def _print_context_tokens_status(ctx_file) -> None:
    """打印 context_tokens 文件状态"""
    from datetime import datetime
    import json

    if ctx_file.exists():
        try:
            stat = ctx_file.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size_kb = stat.st_size / 1024
            data = json.loads(ctx_file.read_text(encoding="utf-8"))
            user_count = len(data) if isinstance(data, dict) else 0
            print(f"  文件路径:    {ctx_file}")
            print(f"  用户数:      {user_count} 个活跃用户")
            print(f"  文件大小:    {size_kb:.1f} KB")
            print(f"  最后修改:    {mtime}")
        except Exception:
            print(f"  文件路径:    {ctx_file}")
            print(f"  \033[33m  ⚠ 无法读取 context tokens 文件\033[0m")
    else:
        print(f"  状态:        \033[90m无缓存 (暂无活跃微信用户)\033[0m")
        print(f"  预期路径:    {ctx_file}")


def _build_wechat_interface_menu(
    runtime, config, token_file,
) -> list:
    """构建微信接口管理菜单

    根据当前状态动态生成可用操作:
      - 已绑定 → 可查看 / 重新绑定 / 删除绑定
      - 未绑定 → 可扫码绑定
    """
    has_token = token_file.exists() or bool(config.wechat.bot_token)
    menu = []

    if has_token:
        menu.append(questionary.Choice(
            title="[重新绑定] 删除旧 token 并重新扫码登录",
            value="__rebind__",
        ))
        menu.append(questionary.Choice(
            title="[删除绑定] 删除已保存的 token（将无法收发微信消息）",
            value="__delete__",
        ))
    else:
        menu.append(questionary.Choice(
            title="[扫码绑定] 使用微信扫描 QR 码登录 iLink Bot",
            value="__rebind__",
        ))

    menu.append(questionary.Choice(title="← 返回上级", value="__back__"))
    return menu


async def _do_wechat_rebind(config, token_file) -> CommandAction:
    """执行微信重新绑定 — 删除旧 token → QR 码扫码登录

    创建独立的 ILinkClient 进行 QR 码登录流程，
    不依赖 WeChatReceiverAgent 的存在。
    """
    from mia.channels.wechat.client import ILinkClient

    # 1. 删除旧 token 文件（如果存在）
    if token_file.exists():
        try:
            token_file.unlink()
            print(f"  ✓ 已删除旧 token 文件: {token_file}")
        except OSError as e:
            print(f"  ⚠ 无法删除旧 token 文件: {e}")

    # 2. 清除环境变量中的 token（如果有）
    config.wechat.bot_token = ""

    # 3. 开始 QR 码登录
    print()
    print(f"  {'─'*50}")
    print(f"  \033[1;33m  MIA 微信登录 — 请使用手机微信扫描二维码\033[0m")
    print(f"  {'─'*50}")
    print()

    client = ILinkClient(
        bot_token="",
        base_url=config.wechat.base_url or "https://ilinkai.weixin.qq.com",
    )
    await client.start()

    try:
        # 获取二维码
        qr_data = await client.get_bot_qrcode()
        qrcode = qr_data.get("qrcode", "")
        qrcode_url = qr_data.get("url") or qr_data.get("qrcode_img_content", "")

        # 显示二维码 URL（终端环境下用户需要手动访问）
        print(f"  QR 码 URL: {qrcode_url or '(请查看日志)'}")
        print()
        print(f"  \033[90m等待扫码中... (最长 300 秒，按 Ctrl+C 取消)\033[0m")

        # 轮询等待扫码确认
        token, base_url = await client.wait_for_login(qrcode)

        # 4. 保存新 token
        config.wechat.bot_token = token
        _save_wechat_token(token_file, token)

        # 5. 更新 base_url（如果服务端返回了新的）
        if base_url and base_url.rstrip("/") != config.wechat.base_url:
            config.wechat.base_url = base_url.rstrip("/")
            print(f"  ℹ API 基础 URL 已更新: {config.wechat.base_url}")

        print()
        print(f"  \033[32m✓ 微信绑定成功！\033[0m")
        print(f"  Token 已保存至: {token_file}")
        print(f"  Token: {_mask_key(token)}")

        return CommandAction.RECONFIGURE_WECHAT

    except asyncio.CancelledError:
        print(f"\n  \033[33m⚠ 扫码登录已取消\033[0m")
        return CommandAction.NONE
    except TimeoutError:
        print(f"\n  \033[31m✗ QR 码登录超时（300 秒未扫码）\033[0m")
        return CommandAction.NONE
    except Exception as e:
        print(f"\n  \033[31m✗ 登录失败: {e}\033[0m")
        return CommandAction.NONE
    finally:
        await client.stop()


async def _do_wechat_delete_binding(
    config, token_file, ctx_file,
) -> CommandAction:
    """删除微信绑定 — 清除 token 文件和 context tokens 缓存

    需要用户确认后执行。删除后微信渠道将无法收发消息。
    """
    # 确认操作
    confirm = await questionary.confirm(
        "确认删除微信绑定？\n"
        "  - 将删除已保存的 Bot Token\n"
        "  - 将清除用户路由缓存 (context tokens)\n"
        "  - 微信渠道将立即无法收发消息\n"
        "  - 如需恢复，需要重新扫码登录\n"
        "\n"
        "确认删除？",
        default=False,
    ).ask_async()

    if not confirm:
        print(f"  \033[90m已取消\033[0m")
        return CommandAction.NONE

    # 删除 token 文件
    deleted_count = 0
    if token_file.exists():
        try:
            token_file.unlink()
            print(f"  ✓ 已删除: {token_file}")
            deleted_count += 1
        except OSError as e:
            print(f"  \033[31m✗ 无法删除 token 文件: {e}\033[0m")

    # 删除 context tokens 文件
    if ctx_file.exists():
        try:
            ctx_file.unlink()
            print(f"  ✓ 已删除: {ctx_file}")
            deleted_count += 1
        except OSError as e:
            print(f"  \033[31m✗ 无法删除 context tokens 文件: {e}\033[0m")

    # 清除环境变量中的 token
    config.wechat.bot_token = ""

    if deleted_count > 0:
        print()
        print(f"  \033[32m✓ 微信绑定已删除\033[0m")
        # 如果渠道已启用，需要关闭（没有 token 无法工作）
        if runtime.wechat_enabled:
            print(f"  \033[33mℹ 微信渠道仍处于启用状态，将尝试 QR 码登录\033[0m")
        return CommandAction.RECONFIGURE_WECHAT
    else:
        print(f"  \033[90m没有需要删除的文件\033[0m")
        return CommandAction.NONE


def _save_wechat_token(token_file, token: str) -> None:
    """持久化 bot_token 到文件"""
    try:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token, encoding="utf-8")
    except OSError as e:
        print(f"  \033[33m⚠ 无法保存 token 到文件: {e}\033[0m")
        print(f"  \033[33m  Token 仅在当前会话有效，重启后需要重新登录\033[0m")


async def _handle_telegram_interface(
    runtime: RuntimeConfig, config,
) -> CommandAction:
    """Telegram 接口详情管理 — 查看状态 / 编辑 Token

    Telegram 比微信简单：不需要 QR 码登录，只需 Bot Token 即可。
    """
    from pathlib import Path

    token_file = Path.home() / ".mia" / "telegram_bot_token"

    while True:
        print()
        print(f"  {'─'*50}")
        print(f"  Telegram 接口 (Bot API)")
        print(f"  {'─'*50}")

        channel_status = "✓ 已启用" if runtime.telegram_enabled else "✗ 未启用"
        print(f"  渠道状态:    {channel_status}")
        print()

        # Token 信息
        if token_file.exists():
            try:
                from datetime import datetime
                stat = token_file.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                token_raw = token_file.read_text(encoding="utf-8").strip()
                print(f"  文件路径:    {token_file}")
                print(f"  最后修改:    {mtime}")
                print(f"  Token:       {_mask_key(token_raw)}")
            except Exception:
                print(f"  文件路径:    {token_file}")
                print(f"  \033[33m  ⚠ 无法读取 token 文件\033[0m")
        else:
            if config.telegram.bot_token:
                print(f"  Token:       {_mask_key(config.telegram.bot_token)} (来自环境变量)")
            else:
                print(f"  状态:        \033[90m未配置 Token\033[0m")
                print(f"  预期路径:    {token_file}")
        print()

        # 菜单
        menu = []
        menu.append(questionary.Choice(
            title=f"[编辑] 更新 Bot Token (当前: {_mask_key(config.telegram.bot_token or '未配置')})",
            value="__edit_token__",
        ))
        if token_file.exists():
            menu.append(questionary.Choice(
                title="[删除] 删除已保存的 Token",
                value="__delete_token__",
            ))
        menu.append(questionary.Choice(title="← 返回上级", value="__back__"))

        choice = await questionary.select(
            "选择操作:",
            choices=menu,
        ).ask_async()

        if choice is None or choice == "__back__":
            return CommandAction.NONE

        if choice == "__edit_token__":
            new_token = await questionary.password(
                "输入 Telegram Bot Token (留空保持不变):",
            ).ask_async()
            if new_token is not None and new_token.strip():
                config.telegram.bot_token = new_token.strip()
                # 持久化到文件
                try:
                    token_file.parent.mkdir(parents=True, exist_ok=True)
                    token_file.write_text(new_token.strip(), encoding="utf-8")
                    print(f"  ✓ Token 已保存至: {token_file}")
                except OSError:
                    print(f"  \033[33m⚠ 无法保存 token 到文件\033[0m")
                print(f"  ✓ Bot Token 已更新")
                if runtime.telegram_enabled:
                    return CommandAction.RECONFIGURE_WECHAT
            continue

        if choice == "__delete_token__":
            confirm = await questionary.confirm(
                "确认删除已保存的 Telegram Bot Token？",
                default=False,
            ).ask_async()
            if confirm:
                if token_file.exists():
                    try:
                        token_file.unlink()
                        print(f"  ✓ Token 文件已删除")
                    except OSError:
                        print(f"  \033[33m⚠ 无法删除文件\033[0m")
                config.telegram.bot_token = ""
                if runtime.telegram_enabled:
                    return CommandAction.RECONFIGURE_WECHAT
            continue

    return CommandAction.NONE


# ═══════════════════════════════════════════════════════════════
# /channel — 通信渠道配置
# ═══════════════════════════════════════════════════════════════

async def handle_channel_command(runtime: RuntimeConfig) -> CommandAction:
    """/channel 命令 — 配置通信渠道（微信、Telegram 等）

    开关渠道时会触发 Agent 重建（RECONFIGURE_WECHAT）。
    """
    from mia.config import get_config

    while True:
        print()
        print(f"  {'─'*55}")
        print(f"  通信渠道配置")
        print(f"  {'─'*55}")

        wechat_label = "✓ 已启用" if runtime.wechat_enabled else "✗ 未启用"
        telegram_label = "✓ 已启用" if runtime.telegram_enabled else "✗ 未启用"
        print(f"  微信 (iLink Bot):     {wechat_label}")
        print(f"  Telegram (Bot API):   {telegram_label}")
        print()

        menu = []
        if runtime.wechat_enabled:
            menu.append(questionary.Choice(
                title="[关闭] 关闭微信渠道",
                value="__toggle_wechat_off__",
            ))
        else:
            menu.append(questionary.Choice(
                title="[开启] 开启微信渠道 (iLink Bot 长轮询 + QR 码登录)",
                value="__toggle_wechat_on__",
            ))

        # Telegram 开关
        if runtime.telegram_enabled:
            menu.append(questionary.Choice(
                title="[关闭] 关闭 Telegram 渠道",
                value="__toggle_telegram_off__",
            ))
        else:
            menu.append(questionary.Choice(
                title="[开启] 开启 Telegram 渠道 (Bot API 长轮询)",
                value="__toggle_telegram_on__",
            ))

        # 编辑 Token
        config = get_config()
        has_wechat_token = bool(config.wechat.bot_token)
        if has_wechat_token:
            menu.append(questionary.Choice(
                title=f"[Token] 编辑微信 Token ({_mask_key(config.wechat.bot_token)})",
                value="__edit_wechat_token__",
            ))
        has_tg_token = bool(config.telegram.bot_token)
        if has_tg_token:
            menu.append(questionary.Choice(
                title=f"[Token] 编辑 Telegram Token ({_mask_key(config.telegram.bot_token)})",
                value="__edit_telegram_token__",
            ))

        menu.append(questionary.Separator("  (未来: Discord, Slack...)"))

        menu.append(questionary.Choice(title="← 返回", value="__back__"))

        choice = await questionary.select(
            "选择操作:",
            choices=menu,
        ).ask_async()

        if choice is None or choice == "__back__":
            return CommandAction.NONE

        if choice == "__toggle_wechat_on__":
            runtime.wechat_enabled = True
            print(f"  ✓ 微信渠道已开启")
            print(f"  ℹ 如果未配置 Bot Token，启动时会自动弹出 QR 码登录")
            return CommandAction.RECONFIGURE_WECHAT

        if choice == "__toggle_wechat_off__":
            confirm = await questionary.confirm(
                "确认关闭微信渠道？这会导致 WeChat Agent 停止工作。",
                default=False,
            ).ask_async()
            if confirm:
                runtime.wechat_enabled = False
                print(f"  ✓ 微信渠道已关闭")
                return CommandAction.RECONFIGURE_WECHAT

        if choice == "__toggle_telegram_on__":
            runtime.telegram_enabled = True
            print(f"  ✓ Telegram 渠道已开启")
            if not config.telegram.bot_token:
                print(f"  ℹ 请通过 /interface 命令配置 Bot Token")
            return CommandAction.RECONFIGURE_WECHAT

        if choice == "__toggle_telegram_off__":
            confirm = await questionary.confirm(
                "确认关闭 Telegram 渠道？",
                default=False,
            ).ask_async()
            if confirm:
                runtime.telegram_enabled = False
                print(f"  ✓ Telegram 渠道已关闭")
                return CommandAction.RECONFIGURE_WECHAT

        if choice == "__edit_wechat_token__":
            new_token = await questionary.password(
                "输入 iLink Bot Token (留空保持不变):",
            ).ask_async()
            if new_token is not None and new_token.strip():
                config.wechat.bot_token = new_token.strip()
                print(f"  ✓ 微信 Bot Token 已更新")
                if runtime.wechat_enabled:
                    return CommandAction.RECONFIGURE_WECHAT

        if choice == "__edit_telegram_token__":
            new_token = await questionary.password(
                "输入 Telegram Bot Token (留空保持不变):",
            ).ask_async()
            if new_token is not None and new_token.strip():
                config.telegram.bot_token = new_token.strip()
                print(f"  ✓ Telegram Bot Token 已更新")
                if runtime.telegram_enabled:
                    return CommandAction.RECONFIGURE_WECHAT

    return CommandAction.NONE


# ═══════════════════════════════════════════════════════════════
# /session — 会话管理（列表/切换/新建/重命名/删除）
# ═══════════════════════════════════════════════════════════════

async def handle_session_command(
    runtime,
    session_manager,
    memory_agent,
) -> CommandAction:
    """/session 命令 — 会话管理

    功能:
      1. 显示当前活跃会话和所有会话列表
      2. 切换到其他会话（自动保存当前、加载目标）
      3. 创建新 CLI 会话
      4. 重命名会话
      5. 删除会话

    Returns:
        CommandAction.NONE — 会话切换由命令内部处理，不需要重建 Agent
    """
    while True:
        current = session_manager.get_current()
        all_sessions = session_manager.list_sessions()

        # ─── 显示当前会话信息 ────────────────────────
        print()
        print(f"  {'─'*55}")
        if current:
            source_display = {
                "cli": "CLI 终端",
                "wechat": "微信",
                "api": "HTTP API",
            }.get(current.source, current.source)
            print(
                f"  当前会话: \033[36m{current.name}\033[0m "
                f"(\033[90m{current.session_id}\033[0m)"
            )
            print(
                f"  来源: {source_display}  ·  "
                f"对话轮次: {current.turn_count}  ·  "
                f"活跃: {current.updated_at[:16] if current.updated_at else '未知'}"
            )
        else:
            print(f"  当前会话: \033[33m(无)\033[0m")
        print(f"  {'─'*55}")
        print()

        # ─── 构建菜单 ─────────────────────────────────
        choices = []

        # 会话列表
        choices.append(questionary.Separator("  ── 会话列表 ──"))
        for s in all_sessions:
            is_current = (current and s.session_id == current.session_id)
            marker = " ◀ 当前" if is_current else ""
            source_tag = {
                "cli": "",
                "wechat": "[微信]",
                "api": "[API]",
            }.get(s.source, f"[{s.source}]")
            label = (
                f"{s.name:<22} {source_tag:<7} "
                f"{s.turn_count}轮{marker}"
            )
            choices.append(questionary.Choice(
                title=label,
                value=f"__switch__{s.session_id}",
            ))

        # 操作
        choices.append(questionary.Separator("  ── 操作 ──"))
        choices.append(questionary.Choice(
            title="[新建] 创建新 CLI 会话",
            value="__create__",
        ))
        if current:
            choices.append(questionary.Choice(
                title=f"[重命名] 重命名当前会话 '{current.name}'",
                value="__rename__",
            ))
            if len(all_sessions) > 1:
                choices.append(questionary.Choice(
                    title=f"[删除] 删除当前会话 '{current.name}'",
                    value="__delete__",
                ))

        choices.append(questionary.Choice(title="← 返回", value="__back__"))

        choice = await questionary.select(
            "选择会话或操作:",
            choices=choices,
        ).ask_async()

        if choice is None or choice == "__back__":
            return CommandAction.NONE

        # ─── 切换会话 ──────────────────────────────────
        if choice.startswith("__switch__"):
            target_id = choice[len("__switch__"):]

            # 已经是当前会话，跳过
            if current and target_id == current.session_id:
                continue

            target_info = session_manager.get_session(target_id)
            if not target_info:
                print(f"  \033[33m会话不存在: {target_id}\033[0m")
                continue

            # 保存当前 → 清空 → 加载目标
            await memory_agent.save_state()
            memory_agent.clear_state()
            session_manager.set_current(target_id)
            await memory_agent.load_state(target_id)
            # 统计加载的内容
            hist_count = len(memory_agent._conversation_history)
            working_count = len(memory_agent._working_memory)
            detail = ""
            if hist_count > 0:
                detail = f" (恢复 {hist_count}轮历史)"
            elif working_count > 0:
                detail = f" (恢复 {working_count}条记忆)"
            print(f"  \033[32m✓\033[0m 已切换到会话: \033[36m{target_info.name}\033[0m{detail}")
            print()
            continue

        # ─── 新建会话 ──────────────────────────────────
        if choice == "__create__":
            name = await questionary.text(
                "输入新会话名称 (留空取消):",
                default="",
            ).ask_async()

            if not name or not name.strip():
                print(f"  \033[90m已取消\033[0m")
                continue

            clean_name = name.strip()[:50]
            if ":" in clean_name:
                print(f"  \033[33m会话名不能包含冒号（:），请重试\033[0m")
                continue

            # 创建新会话
            new_session = session_manager.create_session(clean_name, source="cli")

            # 保存当前 → 清空 → 加载新（空）会话
            await memory_agent.save_state()
            memory_agent.clear_state()
            session_manager.set_current(new_session.session_id)
            await memory_agent.load_state(new_session.session_id)
            print(f"  \033[32m✓\033[0m 已创建并切换到会话: \033[36m{clean_name}\033[0m")
            print()
            continue

        # ─── 重命名会话 ────────────────────────────────
        if choice == "__rename__":
            if not current:
                print(f"  \033[33m没有活跃会话可重命名\033[0m")
                continue

            # WeChat 会话不允许重命名（名称固定）
            if current.source == "wechat":
                print(f"  \033[33m微信会话自动命名，不可手动重命名\033[0m")
                continue

            new_name = await questionary.text(
                f"输入新名称 (当前: {current.name}, 留空取消):",
                default=current.name,
            ).ask_async()

            if not new_name or not new_name.strip():
                print(f"  \033[90m已取消\033[0m")
                continue

            clean_name = new_name.strip()[:50]
            if clean_name == current.name:
                print(f"  \033[90m名称未变化\033[0m")
                continue

            if ":" in clean_name:
                print(f"  \033[33m会话名不能包含冒号（:），请重试\033[0m")
                continue

            if session_manager.rename_session(current.session_id, clean_name):
                print(f"  \033[32m✓\033[0m 已重命名为: \033[36m{clean_name}\033[0m")
            else:
                print(f"  \033[31m✗ 重命名失败\033[0m")
            print()
            continue

        # ─── 删除会话 ──────────────────────────────────
        if choice == "__delete__":
            if not current:
                print(f"  \033[33m没有活跃会话可删除\033[0m")
                continue

            if len(all_sessions) <= 1:
                print(f"  \033[33m无法删除最后一个会话\033[0m")
                continue

            # WeChat 会话不允许手动删除（自动管理）
            if current.source == "wechat":
                print(f"  \033[33m微信会话由系统自动管理，不可手动删除\033[0m")
                continue

            confirm = await questionary.confirm(
                f"确认删除会话 '\033[31m{current.name}\033[0m'？\n"
                f"\n"
                f"  · 对话历史 (\033[33m{current.turn_count}轮\033[0m) 将被永久删除\n"
                f"  · 临时记忆将被清除\n"
                f"  · 持久知识 (MemoryStore) 不受影响\n"
                f"  · 此操作不可撤销\n"
                f"\n"
                f"确认删除？",
                default=False,
            ).ask_async()

            if not confirm:
                print(f"  \033[90m已取消\033[0m")
                continue

            # 保存当前状态 → 删除会话 → 切换到剩余会话中最新的
            await memory_agent.save_state()
            memory_agent.clear_state()
            deleted_id = current.session_id
            deleted_name = current.name

            if not session_manager.delete_session(deleted_id):
                print(f"  \033[31m✗ 删除失败\033[0m")
                continue

            # 自动切换到剩余会话中最新的
            remaining = session_manager.list_sessions()
            if remaining:
                fallback = remaining[0]
                session_manager.set_current(fallback.session_id)
                await memory_agent.load_state(fallback.session_id)
                print(f"  \033[32m✓\033[0m 已删除 '\033[31m{deleted_name}\033[0m'，"
                      f"自动切换到: \033[36m{fallback.name}\033[0m")
            print()
            continue

    return CommandAction.NONE
