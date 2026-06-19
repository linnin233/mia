# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test

```bash
pip install -e ".[dev]"              # Install with dev deps (pytest, pytest-asyncio, etc.)
pip install -e ".[dev,audio]"        # Install with audio support (recording + playback)

# Run all tests (11 test cases across 2 files)
pytest                              # Uses asyncio_mode=auto, 180s timeout from pyproject.toml
python tests/test_memory_storage.py  # Individual test file
python tests/test_memory_browser.py

# Run interactively
python -m mia

# Run with single query (supports --image / --voice)
python -m mia --query "你好"
python -m mia --query "分析这张图" --image screenshot.png
python -m mia --query "总结" --voice meeting.mp3

# Start HTTP API server
python -m mia --server --port 8080
```

No linter/formatter configured yet. Python 3.11+ required. Build system: hatchling.

## Architecture

MIA is a **message-bus multi-agent system** with LLM-driven decision loops. Every `python -m mia` invocation boots 5 agents communicating through a single `MessageBus` (async pub-sub on `asyncio.Queue`).

### Message Flow (full pipeline)

```
CLI/API → ReceiverAgent → MemoryAgent → SchedulerAgent ⇄ TaskAgent → SenderAgent → Output
                                  ↑                                              │
                                  └──── CONVERSATION_DONE ───────────────────────┘
```

1. **ReceiverAgent** (`agents/receiver.py`) — accepts `RAW_INPUT`, decodes text/image/voice. Uses MiMo-V2.5 for **multimodal audio understanding** (content + emotion + intent, not just transcription) with ASR fallback. Uses MiMo VL for image understanding. Emits `USER_INTENT` (target="memory_agent").

2. **MemoryAgent** (`agents/memory.py`) — intercepts `USER_INTENT`, retrieves relevant knowledge + conversation history, injects `memory_context` into payload → forwards to Scheduler. Also listens for `CONVERSATION_DONE` to extract memories.

3. **SchedulerAgent** (`agents/scheduler.py`) — the core LLM loop. Receives `USER_INTENT`/`TASK_RESULT`/`TASK_ERROR`, calls LLM to decide: `reply` (emit streaming `STREAM_START→CHUNK→END` or `SEND_TEXT` for voice), `execute_task` (emit `EXECUTE_TASK` to TaskAgent), or `done`. Safety caps: 10 iterations, 3 consecutive tasks, 60s task timeout.

4. **TaskAgent** (`agents/task.py`) — receives `EXECUTE_TASK`, runs its own LLM+tool loop (max 5 iterations), returns `TASK_RESULT`/`TASK_ERROR`. Built-in tools: `web_search` (DuckDuckGo), `weather`, `shell`, `file`.

5. **SenderAgent** (`agents/sender.py`) — receives `SEND_TEXT`/`SEND_VOICE`/stream chunks, prints to terminal. For `SEND_VOICE`: calls MiMo TTS (`mimo-v2.5-tts`), saves audio to `workspace/`, and **auto-plays** in background thread via `audio/playback.py`. Emits `CONVERSATION_DONE` to both `main` and `memory_agent`.

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

`pydantic-settings` with `.env` auto-load. Three config groups:
- `MiMoConfig` (prefix `MIMO_`) — API key, model names, auto-detects `tp-` (token plan) vs `sk-` (pay-per-use) base URLs.
- `DeepSeekConfig` (prefix `DEEPSEEK_`) — fallback provider.
- `AgentConfig` (prefix `MIA_`) — scheduler limits (`scheduler_max_iterations`, `scheduler_task_timeout`, `scheduler_loop_timeout`, `scheduler_max_consecutive_tasks`), memory settings (`memory_history_turns`, `memory_max_working_entries`, `memory_extraction_timeout`), streaming toggle (`enable_streaming`), verbose mode (`verbose`).

Singleton accessed via `get_config()`.

### Message Format (`bus/message.py`)

`MessageType` enum (19 types) + `Message` dataclass (msg_type, source, target, payload, session_id, parent_id). Factory functions (`make_user_intent`, `make_send_text`, `make_execute_task`, etc.) enforce type-safe payload construction. Streaming uses `STREAM_START` → `STREAM_CHUNK` (N times) → `STREAM_END` sequence.

### Tool Framework (`tools/base.py`)

`Tool` ABC with `name`, `description`, `parameters` (JSON Schema), and `async execute(**kwargs) → ToolResult`. TaskAgent registers tools in a `dict[name, Tool]` and passes their descriptions into the LLM prompt for tool selection.

## Key Patterns

- **Agent lifecycle**: `start()` (subscribes to bus, emits `SYSTEM_READY`) → `run()` message loop (background `asyncio.Task`) → `stop()` (emits `SYSTEM_SHUTDOWN`, unsubscribes).
- **Provider fallback**: Every LLM call tries primary → catches exception → tries fallback. Used consistently across Scheduler, TaskAgent, and MemoryAgent. Streaming replies have their own fallback chain (primary stream → fallback stream → error text).
- **LLM JSON parsing**: `_parse_decision()` extracts JSON from ```json``` blocks or raw `{...}` via regex — robust to LLM formatting quirks. On parse failure, retries once with explicit formatting instructions.
- **Streaming reply flow**: Scheduler decides `reply` with `use_voice=false` + streaming enabled → builds reply context (identity + memory + decision history + trigger) → sends `STREAM_START` → iterates `provider.chat_stream()` → sends `STREAM_CHUNK` per token → sends `STREAM_END` with full text. Sender prints deltas immediately with `flush=True`. Voice reply (`use_voice=true`) skips streaming — needs complete text for TTS.
- **Interactive mode input**: `input()` runs in `loop.run_in_executor(None, input, ...)` to avoid blocking the event loop (so background MemoryAgent can process concurrent messages).
- **Verbose mode**: Controlled by `MIA_VERBOSE`/`/verbose` command. All agents check `get_config().agent.verbose` before printing structured debug output.
- **Memory context injection**: MemoryAgent retrieves relevant knowledge + history → injects into `USER_INTENT.payload["memory_context"]` → Scheduler injects it into both decision and reply LLM contexts.
- **Duplicate task detection**: Scheduler tracks `_task_history` list — if the same task description appears again, it's skipped with a synthetic "duplicate" result.
- **Conversation lifecycle**: Each turn gets a `session_id` (12-char hex). Interactive mode reuses the same agent instances and bus across turns; single-query mode creates fresh instances each time.
- **Logging**: In interactive mode, loguru terminal output is suppressed (to avoid interfering with `You>` prompt) — logs go to `logs/mia.log` with 10MB rotation, 3-day retention.

## CLI Commands (interactive mode)

| Command | Description |
|---------|-------------|
| `/memory` | TUI knowledge browser (3-level drill-down: date → entry → detail) |
| `/compact` | Compress conversation history into knowledge summary |
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

Required: `MIMO_API_KEY` (tp-... or sk-...). Optional: `DEEPSEEK_API_KEY`, `MIA_MEMORY_HISTORY_TURNS` (default 5), `MIA_ENABLE_STREAMING` (default true), `MIA_MEMORY_EXTRACTION_TIMEOUT` (default 8.0s), `MIA_MEMORY_MAX_WORKING_ENTRIES` (default 30).
