# CreativeClaw

CreativeClaw is a channel-oriented creative agent system built on Google's Agent Development Kit (ADK). It keeps conversation state in an ADK session, stages files into a local workspace, and lets the main orchestrator call specialized expert agents through `invoke_agent(agent_name, prompt)`.

## Architecture

- `Orchestrator`: the primary user-facing agent. It inspects session state, uses local tools directly, and invokes experts only when specialized capability is needed.
- `invoke_agent(agent_name, prompt)`: the expert delegation entrypoint. For multi-parameter experts, the prompt is usually a JSON string that matches the expert contract.
- `runtime/expert_dispatcher.py`: normalizes expert parameters, creates a child expert session, runs the expert, and merges the useful result back into the parent session.
- `session.state`: stores conversation history, generated files, expert outputs, progress traces, and explicit final file selection.
- `workspace/`: the filesystem source of truth for uploaded and generated files. User uploads are staged into `workspace/inbox/...`, and generated outputs are written into `workspace/generated/...`.
- Channel adapters: Local CLI is the reference interface. Telegram and Feishu are supported through channel adapters.

## Included Channels

- Local CLI: `apps/art_cli.py`
- Telegram long polling: `apps/run_telegram.py`
- Feishu long connection: `apps/run_feishu.py`

## Environment Setup

```bash
cd creative_claw
source ./.venv/bin/activate
pip install -r requirements.txt
cp .env.template .env
```

If you prefer to recreate the environment, use Python `3.12+`.

Important:

- `.env` is ignored by git and should never be committed.
- Only `.env.template` should be committed as the public sample.
- If any real secret was ever shared outside your machine, rotate it before publishing.

## Credential Matrix

The default orchestrator model in [`conf/jsons/system.json`](conf/jsons/system.json) is `openai/gpt-5.4`, so `OPENAI_API_KEY` is the only default model credential for a minimal text-only setup.

Feature-specific capabilities require additional keys:

| Env var | Required when | Used by | Official URL |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | Required for the default orchestrator model (`openai/gpt-5.4`) | Main orchestrator and any feature using the default system model | [OpenAI API keys](https://platform.openai.com/api-keys) |
| `GOOGLE_API_KEY` | Required for Gemini-backed image features | `ImageGenerationAgent` (`nano_banana` path), `ImageEditingAgent` (`nano_banana` path), `ImageUnderstandingAgent`, `ImageToPromptAgent` | [Google AI Studio API keys](https://aistudio.google.com/app/apikey) |
| `ARK_API_KEY` | Optional | Seedream image generation and image editing | [Volcengine Ark console](https://console.volcengine.com/ark) |
| `DDS_API_KEY` | Optional | `ImageGroundingAgent` via DeepDataSpace DINO-XSeek | [DeepDataSpace cloud console](https://cloud.deepdataspace.com/) |
| `SERPER_API_KEY` | Optional | `SearchAgent` image mode | [Serper](https://serper.dev/) |
| `BRAVE_API_KEY` | Optional | Built-in `web_search` tool | [Brave Search API](https://api.search.brave.com/app/keys) |
| `TELEGRAM_BOT_TOKEN` | Required only for Telegram channel | `apps/run_telegram.py` | [Telegram Bot token guide](https://core.telegram.org/bots/tutorial#obtain-your-bot-token) |
| `TELEGRAM_ALLOW_FROM` | Recommended for Telegram channel | Telegram allowlist | [Telegram Bot API docs](https://core.telegram.org/bots/api) |
| `FEISHU_APP_ID` | Required only for Feishu channel | `apps/run_feishu.py` | [Feishu Open Platform](https://open.feishu.cn/app) |
| `FEISHU_APP_SECRET` | Required only for Feishu channel | `apps/run_feishu.py` | [Feishu Open Platform](https://open.feishu.cn/app) |
| `FEISHU_ENCRYPT_KEY` | Optional | Feishu event subscription security | [Feishu Open Platform](https://open.feishu.cn/app) |
| `FEISHU_VERIFICATION_TOKEN` | Optional | Feishu event subscription verification | [Feishu Open Platform](https://open.feishu.cn/app) |
| `FEISHU_ALLOW_FROM` | Recommended for Feishu channel | Feishu allowlist | [Feishu Open Platform](https://open.feishu.cn/app) |

Notes:

- `SERPER_API_KEY` and `BRAVE_API_KEY` are different. They power different search paths.
- `GOOGLE_API_KEY` is not required for a minimal text-only run if you keep Gemini-only experts unused.
- With the default config, `ImageGenerationAgent` may require both `OPENAI_API_KEY` and `GOOGLE_API_KEY`: the default system model handles prompt enhancement, while Gemini returns the generated image.
- If you change `conf/jsons/system.json` to a Gemini model, the orchestrator will also require `GOOGLE_API_KEY`.
- `DASHSCOPE_API_KEY` is not required by the current tracked runtime paths and is intentionally not documented as a setup requirement.

## Example `.env`

Use `.env.template` as the canonical sample. A practical minimal `.env` for the default text-only setup is:

```env
OPENAI_API_KEY=""
```

If you want the common image and search paths as well:

```env
OPENAI_API_KEY=""
GOOGLE_API_KEY=""
ARK_API_KEY=""
DDS_API_KEY=""
SERPER_API_KEY=""
BRAVE_API_KEY=""
```

## Running

### Local CLI

Interactive mode:

```bash
cd creative_claw
source ./.venv/bin/activate
python apps/art_cli.py
```

Single message:

```bash
cd creative_claw
source ./.venv/bin/activate
python apps/art_cli.py --message "Generate a poster-style cat image"
```

Single message with attachments:

```bash
cd creative_claw
source ./.venv/bin/activate
python apps/art_cli.py \
  --message "Describe this image and write a better prompt" \
  --img1 /absolute/path/to/image.png
```

### Telegram

```bash
cd creative_claw
source ./.venv/bin/activate
python apps/run_telegram.py
```

### Feishu

```bash
cd creative_claw
source ./.venv/bin/activate
python apps/run_feishu.py
```

## Chat Commands

These commands are supported across the local CLI, Telegram, and Feishu channels:

- `/help`: show built-in chat commands
- `/new`: start a fresh conversation session in the current channel chat

## Tests

Focused regression suite:

```bash
cd creative_claw
source ./.venv/bin/activate
python -m unittest \
  unit_test.test_orchestrator \
  unit_test.test_runtime_session \
  unit_test.test_feishu_channel \
  unit_test.test_file_tools
```

Quick syntax check for the main touched files:

```bash
cd creative_claw
source ./.venv/bin/activate
python -m py_compile \
  conf/api.py \
  src/agents/orchestrator/orchestrator_agent.py \
  src/agents/experts/search/tool.py \
  unit_test/test_feishu_channel.py \
  unit_test/test_runtime_session.py
```

## Public Release Checklist

- Keep tracked prompts, comments, and public-facing samples in English.
- Commit only `.env.template`, never a real `.env`.
- Verify the documented credentials against the actual runtime code before each release.
- Prefer feature-gated credential checks at call time instead of import-time crashes.
