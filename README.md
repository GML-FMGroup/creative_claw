<div align="center">
  <br />
  <h1>🎨 CreativeClaw</h1>
  <strong>A multi-agent system for autonomous artistic creation. The development is based on Google's Agent Development Kit (ADK) and Gemini Model</strong>
  <br />
  <br />
</div>


#
## **Structure: Recursive Planning-Execution Workflow**

#
This project adopts a multi-agent architecture based on recursive planning and execution.

*   **Looping Workflow**: When a user enters a request, the backend starts a workflow loop based on the current session. In the loop, the orchestrator agent and execution agent recursively generate and execute plans until the task is complete.

*   **Orchestrator Agent**: The orchestrator agent runs in each loop to analyze the current session state (`session.state`), determine the next subtask, and output the decision in JSON format. It contains three sub-agents: planner, critic, and checker. It can either generate plans through multi-round roleplay among those sub-agents or call the planner directly once.

*   **Executor Agent**: The executor agent generates invocation parameters from the orchestrator plan in each loop, writes parameters and input files to `session.state`, and calls expert agents to execute the plan. After each step finishes, it stores the execution result, logs, and output artifacts back into the session.

*   **Expert Agents**: Executor agent will call the corresponding expert agents (such as image generaton experts and image editing experts). The expert agent will read the parameters from session state, perform its tasks and then write the execution result back to the session state.

*   **State-Driven**: We keep all historical information and context in `session.state`.

*   **Artifact Service**: Input and generated files are stored in binary form in `artifact_service`. Inside the context, we only keep file names.

*   **Multi-turn Dialogue**: The agent supports multi-turn dialogue. The user session continues after a task is completed, so later requests can build on earlier results.
*   **Channel-oriented Runtime**: The main interaction path is now a normalized chat-channel runtime. Local CLI is the reference channel, and other chat platforms can be added as adapters.



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
* Environment Setup
```bash
cd creative_claw
conda create -n creativeclaw python=3.12
conda activate creativeclaw
pip install -r requirements.txt
```

* Set API-KEY
```bash
cp .env.template .env
# edit `.env` to fill API keys and optional channel settings
nano .env
```

Channel configuration is also loaded only from `.env`. The runner scripts do not require extra command line flags for Telegram or Feishu credentials.

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

* Run the local channel CLI
```bash
cd creative_claw
conda activate creativeclaw
python apps/art_cli.py --message {your_message}
```

* Run the Telegram channel
```bash
cd creative_claw
conda activate creativeclaw
python apps/run_telegram.py
```

* Run the Feishu channel
```bash
cd creative_claw
conda activate creativeclaw
python apps/run_feishu.py
```
