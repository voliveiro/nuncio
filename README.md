# Nuncio

A personal AI agent built on Claude, running locally on Ubuntu. This is a learning project — I'm building Nuncio to understand how AI agents actually work in practice: how they use tools, maintain memory, and integrate with real-world services.

I'm a hobbyist programmer, so this is as much about the journey as the destination.

## What Nuncio does

- Converses with persistent memory across sessions
- Reads and manages your Gmail inbox
- Creates, reads, and updates Google Calendar events
- Searches and retrieves files from Google Drive
- Reads and writes local files, including PDFs and Word documents
- Maintains a structured local inbox for incoming documents

## What I'm learning

Building Nuncio has been a hands-on way to explore:

- How agentic AI systems use tools to interact with the world
- How memory and conversation history work in practice
- The real challenges of integrating AI with external services (OAuth, API limits, error handling)
- What AI governance looks like from the inside — what agents can and can't be constrained to do

## Tech stack

- **LLM**: Claude (Anthropic API)
- **Backend**: Python (Flask), running on port 5001
- **Integrations**: Google Calendar, Gmail, Google Drive via OAuth 2.0
- **Environment**: Ubuntu, Lenovo X1 Carbon

## Setup

### Prerequisites

- Python 3
- An Anthropic API key
- Google Cloud project with OAuth 2.0 credentials

### Installation
```bash
git clone https://github.com/yourusername/nuncio.git
cd nuncio
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Create a `keys/` folder in the project root and add:

- `anthropic.key` — your Anthropic API key
- `google_credentials.json` — your Google OAuth client credentials
- `google_token.json` and `drive_token.json` — generated on first auth run

### Running Nuncio
```bash
python3 app.py
```

Nuncio will be available at `http://localhost:5001`.

## Project structure
```
nuncio/
├── app.py               # Main application
├── requirements.txt     # Python dependencies
├── .gitignore
├── keys/                # Credentials (not tracked by Git)
├── nuncio-inbox/        # Local inbox folders (not tracked by Git)
└── conversation_history.json  # Persistent memory (not tracked by Git)
```

## Security note

The `keys/` folder, `nuncio-inbox/`, and `conversation_history.json` are excluded from version control via `.gitignore`. Never commit API keys or OAuth tokens.

## License

MIT
