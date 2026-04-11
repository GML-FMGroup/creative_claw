<div align="center">
  <br />
  <h1>🎨 CreativeClaw</h1>
  <strong>A channel-oriented creative agent system built on Google's Agent Development Kit (ADK).</strong>
  <br />
  <br />
</div>


#
## **Architecture**

#
CreativeClaw is a stateful, channel-oriented runtime for creative tasks.

*   **Main Agent (`Orchestrator`)**: The orchestrator is the primary user-facing agent. It inspects the current session state, uses local tools directly, and calls expert agents only when specialized capability is needed.

*   **Expert Invocation (`invoke_agent`)**: Expert calls now flow through `invoke_agent(agent_name, prompt)`. The prompt is usually a JSON string that contains the expert parameters.

*   **Runtime Dispatcher**: `runtime/expert_dispatcher.py` normalizes expert parameters, creates a child expert session, runs the expert, and merges the useful result back into the parent session.

*   **Expert Agents**: Experts such as image generation, image editing, image understanding, search, and knowledge agents still read `current_parameters` from session state and write `current_output` back to session state.

*   **State-Driven Execution**: Conversation history, generated files, expert results, and progress traces are stored in `session.state`.

*   **Workspace-Based Files**: User uploads are staged into the workspace, and generated files are tracked with workspace-relative paths in session state.

*   **Multi-turn Dialogue**: Sessions persist across turns. Later requests can refer to earlier outputs in the same chat.

*   **Channel Adapters**: Local CLI is the reference interface. Telegram and Feishu are supported through channel adapters.


## **Interactive client**

We currently provide a channel-oriented local CLI:

1.  **Local channel CLI (`apps/art_cli.py`)**:
    *   Supports text dialogue through the local terminal channel.
    *   Supports up to two local file attachments per message.
    *   Reuses the same chat session through `user-id` and `chat-id`.
    *   Supports `/new` to start a fresh conversation session inside the same channel chat.
    *   Supports `/help` to show built-in chat commands.
2.  **Telegram channel runner (`apps/run_telegram.py`)**:
    *   Uses Telegram long polling.
    *   Required in `.env`: `TELEGRAM_BOT_TOKEN`
    *   Recommended in `.env`: `TELEGRAM_ALLOW_FROM`
3.  **Feishu channel runner (`apps/run_feishu.py`)**:
    *   Uses Feishu long connection.
    *   Required in `.env`: `FEISHU_APP_ID`, `FEISHU_APP_SECRET`
    *   Optional in `.env`: `FEISHU_ENCRYPT_KEY`, `FEISHU_VERIFICATION_TOKEN`
    *   Recommended in `.env`: `FEISHU_ALLOW_FROM`

---


## 🛠️ **Installation and Running**
* Environment setup
```bash
cd creative_claw
source ./.venv/bin/activate
pip install -r requirements.txt
```

If you prefer to recreate the environment yourself, use Python 3.12+.

* Set API-KEY
```bash
cp .env.template .env
# edit `.env`
```

The runtime loads channel and tool configuration only from `.env`.

Minimum required keys at startup:

```env
GOOGLE_API_KEY=""
DASHSCOPE_API_KEY=""
```

Recommended additional keys:

```env
SERPER_API_KEY=""
ARK_API_KEY=""
```

Notes:
- `GOOGLE_API_KEY` is required by the current config loader.
- `DASHSCOPE_API_KEY` is required for enabled image and vision tools.
- `SERPER_API_KEY` is needed for web/image search features.
- `ARK_API_KEY` is only needed when you want to use `seedream`-based image generation or editing paths.
- The default system model in `conf/jsons/system.json` is currently `openai/gpt-5.4`. Make sure the model provider you use is configured in your local environment as well.

Suggested channel fields in `.env`:

```env
# Telegram
TELEGRAM_BOT_TOKEN=""
TELEGRAM_ALLOW_FROM=""

# Feishu
FEISHU_APP_ID=""
FEISHU_APP_SECRET=""
FEISHU_ENCRYPT_KEY=""
FEISHU_VERIFICATION_TOKEN=""
FEISHU_ALLOW_FROM=""
```

Notes:
- For Telegram, `TELEGRAM_BOT_TOKEN` is required. `TELEGRAM_ALLOW_FROM` is strongly recommended so the bot is not open to everyone by default.
- For Feishu long connection mode, `FEISHU_APP_ID` and `FEISHU_APP_SECRET` are required.
- For Feishu long connection mode, `FEISHU_ENCRYPT_KEY` and `FEISHU_VERIFICATION_TOKEN` can usually stay empty.
- `FEISHU_ALLOW_FROM` is optional but recommended if you want to limit who can trigger the agent.

## **Chat Commands**

The following commands are supported across the local CLI, Telegram, and Feishu channels:

- `/help`: Show the built-in chat commands.
- `/new`: Start a fresh conversation session inside the current channel chat.

* Run the local channel CLI (interactive)
```bash
cd creative_claw
source ./.venv/bin/activate
python apps/art_cli.py
```

* Run the local channel CLI (single message)
```bash
cd creative_claw
source ./.venv/bin/activate
python apps/art_cli.py --message "Generate a poster-style cat image"
```

* Run the local channel CLI with attachments
```bash
cd creative_claw
source ./.venv/bin/activate
python apps/art_cli.py \
  --message "Describe this image and write a better prompt" \
  --img1 /absolute/path/to/image.png
```

* Run the Telegram channel
```bash
cd creative_claw
source ./.venv/bin/activate
python apps/run_telegram.py
```

* Run the Feishu channel
```bash
cd creative_claw
source ./.venv/bin/activate
python apps/run_feishu.py
```

## **Testing**

Run the current core regression suite:

```bash
cd creative_claw
source ./.venv/bin/activate
python -m unittest unit_test.test_runtime_session unit_test.test_expert_dispatcher unit_test.test_orchestrator
```

Quick syntax check for the recently touched runtime files:

```bash
cd creative_claw
source ./.venv/bin/activate
python -m py_compile \
  src/runtime/tool_context_artifact_service.py \
  src/runtime/expert_dispatcher.py \
  src/agents/orchestrator/orchestrator_agent.py
```
