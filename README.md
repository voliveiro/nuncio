# Nuncio

A personal AI agent built on Claude, deployed on a Hetzner server. This is a learning project — I'm building Nuncio to understand how AI agents actually work in practice: how they use tools, maintain memory, and integrate with real-world services.

I'm a hobbyist programmer, so this is as much about the journey as the destination.

## What Nuncio does

- Converses with persistent memory across sessions
- Reads and manages Gmail
- Creates and reads Google Calendar events
- Searches and retrieves files from Google Drive
- Reads and writes local files, including PDFs and Word documents
- Maintains a structured local inbox for incoming documents
- Searches the web and fetches URLs for research
- Runs a weekly Book Scout — a specialist subagent that finds new book recommendations matching personal preferences and delivers a digest
- Sends and receives messages via Telegram (bidirectional, with confirmation gates for sensitive actions)

## Interfaces

Nuncio can be reached two ways, both sharing the same conversation history and memory:

- **Terminal** — interactive CLI session (`python3 agents/nuncio.py`)
- **Telegram** — persistent bot running as a systemd service on Hetzner; only the owner's Telegram user ID is accepted

## What I'm learning

Building Nuncio has been a hands-on way to explore:

- How agentic AI systems use tools to interact with the world
- How memory and conversation history work in practice
- The real challenges of integrating AI with external services (OAuth, API limits, error handling)
- What AI governance looks like from the inside — confirmation gates, action logging, trust boundaries
- Multi-interface agent design — sharing state across CLI and messaging interfaces

## Tech stack

- **LLM**: Claude Sonnet (main agent), Claude Haiku (Book Scout subagent) via Anthropic API
- **Language**: Python 3.12
- **Integrations**: Google Calendar, Gmail, Google Drive via OAuth 2.0; Telegram Bot API
- **Browser automation**: Playwright (headless Chromium)
- **Document parsing**: PyMuPDF (PDF), python-docx (Word)
- **Deployment**: Hetzner VPS, auto-deployed via GitHub Actions on push to main

## Setup

### Prerequisites

- Python 3.12
- An Anthropic API key
- Google Cloud project with OAuth 2.0 credentials
- A Telegram bot token and your Telegram user ID (for the Telegram interface)

### Installation
```bash
git clone https://github.com/yourusername/nuncio.git
cd nuncio
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Configuration

Create a `keys/` folder in the project root and add:

- `anthropic.key` — your Anthropic API key
- `google_credentials.json` — your Google OAuth client credentials
- `google_token.json` — generated on first auth run
- `telegram.key` — your Telegram bot token (from @BotFather)
- `telegram_user_id.key` — your numeric Telegram user ID (from @userinfobot)

### Running Nuncio

**Terminal (interactive):**
```bash
python3 agents/nuncio.py
```

**Terminal (headless single task):**
```bash
python3 agents/nuncio.py --task "your task here"
```

**Telegram interface:**
```bash
python3 agents/telegram_interface.py
```

On a server, the Telegram interface runs as a systemd service (see `deploy/nuncio-telegram.service`).

## Project structure
```
nuncio/
├── agents/
│   ├── nuncio.py                # Main agent — tools, memory, conversation loop
│   ├── telegram_interface.py    # Telegram bot interface
│   ├── book_scout_cron.py       # Headless weekly book scout runner
│   └── book_preferences.md      # Book Scout search criteria
├── deploy/
│   └── nuncio-telegram.service  # systemd service definition
├── logs/                        # Persistent state (not tracked by Git)
│   ├── conversation_history.json
│   ├── memory.json
│   └── action_log.jsonl
├── keys/                        # Credentials (not tracked by Git)
├── nuncio-inbox/                # Local inbox for incoming documents (not tracked by Git)
└── requirements.txt
```

## Security note

The `keys/`, `logs/`, and `nuncio-inbox/` directories are excluded from version control via `.gitignore`. Never commit API keys or OAuth tokens.

Sensitive actions (sending email, creating calendar events, uploading files, modifying memory) require explicit confirmation before execution. In the terminal this is a typed prompt; in Telegram it is an inline keyboard button.

## License

MIT
