# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test

```bash
pip install -e ".[dev]"                     # Install with dev deps
pip install -e ".[dev,audio]"               # Install with audio support (recording + playback)
pip install -e ".[dev,audio,wechat]"        # Full install (WeChat + iLink + SILK decode)

# Run all tests (15 test cases across 3 files)
pytest                                       # Uses asyncio_mode=auto, 180s timeout
pytest tests/test_memory_storage.py          # 6: Level 1/2 extraction, CRUD, fallback, retrieve
pytest tests/test_memory_browser.py          # 5: empty store, single day, flat mode, no file, readonly
pytest tests/test_full_pipeline.py           # 4: WeChat components, bus mirror, passthrough, LLM pipeline

# Run interactively
python -m mia

# Run with single query (supports --image / --voice / --wechat)
python -m mia --query "你好"
python -m mia --query "分析这张图" --image screenshot.png
python -m mia --query "总结" --voice meeting.mp3
python -m mia --wechat                        # Interactive + WeChat channel

# Start HTTP API server
python -m mia --server --port 8080
```

No linter/formatter configured yet. Python 3.11+ required. Build system: hatchling.

## Architecture

MIA is a **message-bus multi-agent system** with LLM-driven decision loops. Every `python -m mia` invocation boots **7 agents** (5 core + 2 optional WeChat) communicating through a single `MessageBus` (async pub-sub on `asyncio.Queue`).

### Message Flow (full pipeline)

```
CLI/API ──→ ReceiverAgent ──→ MemoryAgent ──→ SchedulerAgent ⇄ TaskAgent ──→ SenderAgent ──→ Output
                  ↑                                                                    │
WeChat ──→ WeChatReceiverAgent ──┘                                                    │
                  │                                                                     │
                  └────────────── CONVERSATION_DONE ←─────────────────────────────┘
                  └────────────── BUS_MIRROR (STREAM_END etc.) ───────────────────┘
                  │                              │
                  └── WeChatSenderAgent ←────────┘ (reply routed by session_id prefix)

Session routing: Scheduler reads session_id — "wechat:*" → WeChatSender, else → SenderAgent.
```

**Core Agents (5):**

1. **ReceiverAgent** (`agents/receiver.py`) — accepts `RAW_INPUT`, decodes text/image/voice. Uses MiMo-V2.5 for **multimodal audio understanding** (content + emotion + intent, not just transcription) with ASR fallback. Uses MiMo VL for image understanding. Emits `USER_INTENT` (target="memory_agent").

2. **MemoryAgent** (`agents/memory.py`) — intercepts `USER_INTENT`, retrieves relevant knowledge + conversation history, injects `memory_context` into payload → forwards to Scheduler. Also listens for `CONVERSATION_DONE` + bus mirror messages to extract memories. Two-level memory: Level 1 (in-memory working, fast) → Level 2 (disk persistent, LLM-merged).

3. **SchedulerAgent** (`agents/scheduler.py`) — the core LLM loop. Receives `USER_INTENT`/`TASK_RESULT`/`TASK_ERROR`, calls LLM to decide: `reply` (emit streaming `STREAM_START→CHUNK→END` or `SEND_TEXT` for voice), `execute_task` (emit `EXECUTE_TASK` to TaskAgent), or `done`. Channel-aware routing via `session_id` prefix. Safety caps: 10 iterations, 3 consecutive tasks, 60s task timeout.

4. **TaskAgent** (`agents/task.py`) — receives `EXECUTE_TASK`, runs its own LLM+tool loop (max 5 iterations), returns `TASK_RESULT`/`TASK_ERROR`. Built-in tools: `web_search` (DuckDuckGo), `weather`, `shell`, `file`.

5. **SenderAgent** (`agents/sender.py`) — receives `SEND_TEXT`/`SEND_VOICE`/stream chunks, prints to terminal. For `SEND_VOICE`: calls MiMo TTS (`mimo-v2.5-tts`), saves audio to `workspace/`, and **auto-plays** in background thread via `audio/playback.py`. Emits `CONVERSATION_DONE` to both `main` and `memory_agent`.

**Channel Agents (2, optional —收发分离):**

6. **WeChatReceiverAgent** (`channels/wechat/receiver.py`) — iLink long polling + message dedup + SILK→WAV decode → publish `RAW_INPUT` to bus with `session_id="wechat:<user_id>"`.

7. **WeChatSenderAgent** (`channels/wechat/sender.py`) — receives Scheduler's reply routed to WeChat target → optional TTS synthesis → CDN upload (AES-128-ECB) → send as `file_item` to WeChat user via iLink API.

8. **TelegramReceiverAgent** (`channels/telegram/receiver.py`) — Bot API `getUpdates` long polling → message dedup → publish `RAW_INPUT` with `session_id="telegram:<chat_id>"`.

9. **TelegramSenderAgent** (`channels/telegram/sender.py`) — receives Scheduler's reply routed to Telegram target → optional TTS synthesis → `sendAudio`/`sendMessage` via Bot API.

### Prompt Externalization (`prompts/` + `AGENTS.md`)

System prompts are loaded from external files at runtime (not hardcoded), enabling customization without code changes:

| File | Used by | Purpose |
|------|---------|---------|
| `prompts/scheduler.md` | SchedulerAgent | LLM decision-loop instructions (JSON decision format, rules) |
| `prompts/reply.md` | SchedulerAgent | Reply generation instructions (tone, length, format) |
| `prompts/task_agent.md` | TaskAgent | Tool-calling loop instructions (max 2 same-type tools, early finish) |
| `AGENTS.md` | All agents | MIA identity definition (name, personality, tone) — prepended to all system prompts |

`_load_prompt(filename)` reads from `prompts/` with graceful fallback to hardcoded defaults. `_load_agent_identity()` reads `AGENTS.md` from project root. The identity is combined with task-specific instructions: `identity + "\n\n---\n\n" + instructions`.

### Two-Level Memory System (MemoryAgent)

**Level 1 — Working Memory** (in-memory, `_working_memory`): after each `CONVERSATION_DONE`, calls LLM to extract 1-3 atomic `KnowledgeEntry` items (confidence=0.5). Falls back to local extraction on timeout. Capped at `memory_max_working_entries` (default 30) — triggers forced merge when exceeded.

**Level 2 — Persistent Knowledge** (disk, `MemoryStore`): triggered on date change or `/compact`. LLM merges/dedupes Level 1 entries → persists with confidence ≥ 0.7.

**Conversation History** (`_conversation_history`): last N turns of user+assistant raw text injected into LLM context for pronoun resolution. Default 5 turns (`MIA_MEMORY_HISTORY_TURNS`).

### MemoryStore Storage Layout (`memory/store.py`)

Two-tier file architecture under `data/memory/`:
- `index.json` — always loaded (~1-2 KB), one `DaySummary` per day (count, keywords, category distribution, LLM-generated summary). Used for fast scan-based lookup.
- `daily/YYYY-MM-DD.json` — lazily loaded on demand, stores the actual `KnowledgeEntry` list for that day.

Two-phase retrieval: `scan_index(keywords)` → narrow to relevant dates → `load_day(date)` → keyword match + LLM rerank. Beijing timezone (UTC+8) throughout.

### Audio Subsystem (`audio/`)

Optional module (`[project.optional-dependencies] audio`), requires `sounddevice` + `soundfile` + `numpy`:

- **`audio/recorder.py`** — `record_until_keypress()`: blocking microphone recording, press Enter to start/stop. Saves to `%TEMP%/mia_recordings/mia_rec_*.wav` (16kHz mono). Designed for `loop.run_in_executor()` to avoid blocking asyncio.
- **`audio/playback.py`** — `play_audio(filepath, blocking=False)`: cross-platform WAV/FLAC/OGG playback. Non-blocking by default (used for TTS auto-playback).

### Provider Architecture

`BaseProvider` defines `chat()`, `chat_sync()`, `chat_stream()`. Both `MiMoProvider` and `DeepSeekProvider` use `openai.AsyncOpenAI` client. Every agent with LLM access supports primary + fallback provider chaining (e.g., MiMo → DeepSeek).

**MiMoProvider** (`providers/mimo.py`) — the primary provider, wraps Xiaomi MiMo platform:
- `chat()` / `chat_sync()` / `chat_stream()` — OpenAI-compatible text chat
- `understand_image(image_data, prompt)` — VL image understanding via `mimo-v2.5`
- `understand_audio(audio_data, prompt)` — multimodal audio understanding (content + emotion + intent) via `mimo-v2.5`
- `transcribe(audio_data)` — pure ASR transcription via `mimo-v2.5-asr`
- `synthesize(text, voice, format)` — TTS text→audio via `mimo-v2.5-tts` (returns WAV/PCM16 bytes)
- `synthesize_stream(text, voice)` — streaming TTS, yields PCM16 chunks
- Static helpers: `encode_image_file(path, mime_type)`, `encode_audio_file(path, mime_type)` → base64 data URLs

### Configuration (`config.py`)

**Static config** — `pydantic-settings` with `.env` auto-load, four config groups:

| Config Group | Env Prefix | Purpose |
|-------------|-----------|---------|
| `MiMoConfig` | `MIMO_` | MiMo API key, model names, auto-detects `tp-` vs `sk-` base URL |
| `DeepSeekConfig` | `DEEPSEEK_` | Fallback provider |
| `WeChatConfig` | `MIA_WECHAT_` | iLink Bot token, base URL, media dir |
| `AgentConfig` | `MIA_` | Scheduler limits, memory params, streaming toggle, verbose mode |

**Runtime config** (`RuntimeConfig` — pure dataclass, NOT pydantic-settings):
Mutable at runtime via `/model` `/agent` `/channel` slash commands. Stores:
- `provider_api_keys` (dict) — per-platform API keys editable at runtime
- `model_enabled` (dict) — per-model enable/disable toggles
- Agent model assignments — each agent gets `_model` + `_fallback` fields (e.g., `scheduler_model`, `task_fallback`)
- Feature toggles — `receiver_vision_enabled`, `receiver_audio_enabled`, `sender_tts_enabled`, `wechat_sender_tts_enabled`
- Channel toggles — `wechat_enabled`

Singleton accessed via `get_config()` — returns `Config` with `.mimo`, `.deepseek`, `.agent`, `.wechat`, `.runtime`.

### Model Registry (`model_registry.py`)

Hardcoded truth table of model capabilities. Each model has a `ModelInfo(provider, capabilities, desc)`:

- `Capability` enum: `TEXT_CHAT`, `VISION`, `AUDIO_UNDERSTANDING`, `ASR`, `TTS`, `STREAMING`
- `MODEL_REGISTRY` dict — 6 models across 2 providers (MiMo: v2.5-pro, v2.5, v2.5-asr, v2.5-tts; DeepSeek: v4-pro, v4-flash)
- Query functions: `get_models_with_capability()`, `get_available_models(runtime)` (filters by key+enabled), `validate_assignment()` (checks capability requirements)
- `create_provider(provider_name, api_key)` — factory that returns `MiMoProvider` or `DeepSeekProvider`

### CLI Commands (`cli/commands.py`)

Three new slash commands for runtime configuration, all using `questionary` TUI:

| Command | Function | Returns |
|---------|----------|---------|
| `/model` | Configure API keys + toggle models per provider | `CommandAction.RECONFIGURE_AGENTS` if key/models changed |
| `/agent` | Assign models to each agent (Scheduler/Task/Memory/Receiver/Sender) | `CommandAction.RECONFIGURE_AGENTS` if assignment changed |
| `/channel` | Toggle WeChat channel + edit bot token | `CommandAction.RECONFIGURE_WECHAT` if channel state changed |

Each command handler only modifies `RuntimeConfig` and returns a `CommandAction` enum. `main.py`'s `_reconfigure_agents()` handles the actual agent lifecycle (stop old → recreate providers → create new agents → start). This separation ensures model changes take effect immediately without restart.

### Message Format (`bus/message.py`)

`MessageType` enum (19 types) + `Message` dataclass (msg_type, source, target, payload, session_id, parent_id). Factory functions (`make_user_intent`, `make_send_text`, `make_execute_task`, etc.) enforce type-safe payload construction. Streaming uses `STREAM_START` → `STREAM_CHUNK` (N times) → `STREAM_END` sequence.

### Tool Framework (`tools/base.py`)

`Tool` ABC with `name`, `description`, `parameters` (JSON Schema), and `async execute(**kwargs) → ToolResult`. TaskAgent registers tools in a `dict[name, Tool]` and passes their descriptions into the LLM prompt for tool selection.

## Key Patterns

- **Agent lifecycle**: `start()` (subscribes to bus, emits `SYSTEM_READY`) → `run()` message loop (background `asyncio.Task`) → `stop()` (emits `SYSTEM_SHUTDOWN`, unsubscribes).
- **Hot model switching**: `/model` `/agent` `/channel` commands modify `RuntimeConfig` in-place → `_reconfigure_agents()` stops all agents, recreates providers from new config, starts new agents. No restart needed.
- **Channel-aware routing**: Scheduler reads `session_id` prefix — `"wechat:*"` → WeChatSender, plain uuid → SenderAgent. Reply goes only to the channel that sent the input (收发分离).
- **Provider factory**: `create_provider(provider_name, api_key)` in `model_registry.py` is the single factory for all Provider instances. Same-platform models share one Provider instance; model selection happens at call time via `model=` parameter.
- **Capability validation**: `validate_assignment(model_id, required_caps)` called at agent creation — raises `ValueError` if a model lacks required capabilities (e.g., assigning a text-only model to Receiver's vision role).
- **Provider fallback**: Every LLM call tries primary → catches exception → tries fallback. Used consistently across Scheduler, TaskAgent, and MemoryAgent. Streaming replies have their own fallback chain (primary stream → fallback stream → error text).
- **LLM JSON parsing**: `_parse_decision()` extracts JSON from ```json``` blocks or raw `{...}` via regex — robust to LLM formatting quirks. On parse failure, retries once with explicit formatting instructions.
- **Streaming reply flow**: Scheduler decides `reply` with `use_voice=false` + streaming enabled → builds reply context (identity + memory + decision history + trigger) → sends `STREAM_START` → iterates `provider.chat_stream()` → sends `STREAM_CHUNK` per token → sends `STREAM_END` with full text. Sender prints deltas immediately with `flush=True`. Voice reply (`use_voice=true`) skips streaming — needs complete text for TTS.
- **Bus mirror**: `MessageBus.subscribe_mirror(msg_type, target)` auto-delivers copies of messages to MemoryAgent without explicit `CONVERSATION_DONE`. Dual guarantee: even if SenderAgent crashes, MemoryAgent still sees `STREAM_END` and can extract memories.
- **Interactive mode input**: `input()` runs in `loop.run_in_executor(None, input, ...)` to avoid blocking the event loop (so background MemoryAgent can process concurrent messages).
- **Verbose mode**: Controlled by `MIA_VERBOSE`/`/verbose` command. All agents check `get_config().agent.verbose` before printing structured debug output.
- **Memory context injection**: MemoryAgent retrieves relevant knowledge + history → injects into `USER_INTENT.payload["memory_context"]` → Scheduler injects it into both decision and reply LLM contexts.
- **Duplicate task detection**: Scheduler tracks `_task_history` list — if the same task description appears again, it's skipped with a synthetic "duplicate" result.
- **Conversation lifecycle**: Each turn gets a `session_id` (12-char hex). Interactive mode reuses the same agent instances and bus across turns; single-query mode creates fresh instances each time. WeChat sessions use `session_id="wechat:<user_id>"`.
- **Logging**: In interactive mode, loguru terminal output is suppressed (to avoid interfering with `You>` prompt) — logs go to `logs/mia.log` with 10MB rotation, 3-day retention.

### Session System (`session/manager.py`)

Cross-restart session management with source-aware routing:

- **SessionInfo**: metadata stored in `data/sessions/index.json` — always loaded
- **SessionState**: conversation history + working memory + daily buffer — stored per-session in `data/sessions/states/<session_id>.json`
- **Session ID convention**: CLI=`"cli_<8hex>"` (no colon, routes to terminal), WeChat=`"wechat:<user_id>"` (colon prefix, routes to WeChat)
- **Auto-create**: first CLI message creates "默认" session; WeChat messages auto-register via `get_or_create_for_id()`
- **Auto-switch**: MemoryAgent detects `session_id` change in incoming messages → saves old session → loads new session (seamless WeChat↔CLI cross-talk)
- **Auto-save**: after each `CONVERSATION_DONE`, session state is atomically written to disk
- **Startup restore**: `MemoryAgent.on_start()` loads last active session's state (conversation history + working memory)
- **Rebuild protection**: `_reconfigure_agents()` saves session state before destroying old MemoryAgent and restores after creating new one
- **Atomic I/O**: tmp+rename pattern (same as `memory/store.py`)

## CLI Commands (interactive mode)

| Command | Description |
|---------|-------------|
| `/model` | TUI model platform config — edit API keys + toggle model enable/disable per provider |
| `/agent` | TUI agent model assignment — assign primary/fallback models to each agent, toggle features |
| `/channel` | TUI channel config — toggle WeChat channel, edit iLink Bot token |
| `/interface` | TUI message interface binding — view token info, re-bind QR login, delete binding |
| `/session` | TUI session manager — list, switch, create, rename, delete sessions (persists across restarts) |
| `/memory` | TUI knowledge browser (3-level drill-down: date → entry → detail, paginated 10/page) |
| `/compact` | Compress conversation history into knowledge summary (triggers L1→L2 merge) |
| `/verbose` | Toggle detailed agent thought/steps output |
| `/image <path>` | Send image for VL analysis (prompts for optional text description) |
| `/voice <path>` | Send audio file for multimodal understanding (prompts for optional text) |
| `/record` | Record from microphone (press Enter to start/stop) → auto-understand |
| `/help`, `/quit` | Self-explanatory |

## HTTP API

Two endpoints on `http://127.0.0.1:{port}`:

- `GET /health` → `{"status": "ok", "version": "0.1.0"}`
- `POST /chat` — body: `{"query": "...", "image": "optional/path", "voice": "optional/path"}` → `{"response": "..."}`. Each request spawns a fresh agent pipeline (new bus + agents), returns after `CONVERSATION_DONE` or 180s timeout.

## Env Configuration

Required: `MIMO_API_KEY` (tp-... or sk-...). Optional:

| Variable | Default | Description |
|----------|---------|-------------|
| `DEEPSEEK_API_KEY` | — | Fallback provider API key |
| `MIA_MEMORY_HISTORY_TURNS` | 5 | Conversation history turns injected into LLM context |
| `MIA_ENABLE_STREAMING` | true | Enable token-by-token streaming output |
| `MIA_MEMORY_EXTRACTION_TIMEOUT` | 8.0 | Seconds before L1 extraction falls back to local |
| `MIA_MEMORY_MAX_WORKING_ENTRIES` | 30 | Trigger forced L1→L2 merge |
| `MIA_VERBOSE` | true | Default verbose mode (show agent thoughts) |
| `MIA_WECHAT_ENABLED` | false | Auto-enable WeChat channel on start |
| `MIA_WECHAT_BOT_TOKEN` | — | iLink Bot token (empty = QR code login on start) |
