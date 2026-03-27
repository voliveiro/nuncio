import anthropic
import argparse
import datetime
import os
import io
import json
import requests
import time
from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

CONFIRMATION_REQUIRED = {"send_email", "create_calendar_event", "create_multiple_events", "upload_to_drive", "remember", "delete_memory"}

# --- Config ---
CREDS_FILE = os.path.join(os.path.dirname(__file__), '..', 'keys', 'google_credentials.json')
TOKEN_FILE = os.path.join(os.path.dirname(__file__), '..', 'keys', 'google_token.json')

SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/drive.readonly'
]

NUNCIO_FOLDER = os.path.join(os.path.dirname(__file__), '..', 'nuncio-inbox')

# --- Error classification helper ---
def _classify_error(e):
    """Returns (reason, retryable) for structured error responses."""
    if isinstance(e, (FileNotFoundError, IsADirectoryError)):
        return "file_not_found", False
    if isinstance(e, PermissionError):
        return "permission_error", False
    if isinstance(e, (requests.exceptions.Timeout,)):
        return "network_or_timeout_error", True
    if isinstance(e, requests.exceptions.ConnectionError):
        return "network_or_timeout_error", True
    if isinstance(e, requests.exceptions.HTTPError):
        status = e.response.status_code if e.response is not None else 0
        if 400 <= status < 500:
            return f"http_{status}_error", False
        return f"http_{status}_error", True
    try:
        from googleapiclient.errors import HttpError
        if isinstance(e, HttpError):
            status = int(e.resp.status)
            if 400 <= status < 500:
                return f"http_{status}_error", False
            return f"http_{status}_error", True
    except ImportError:
        pass
    return "unknown_error", True

# --- History ---

HISTORY_FILE = os.path.join(os.path.dirname(__file__), '..', 'logs', 'conversation_history.json')
BOOK_SCOUT_FILE = os.path.join(os.path.dirname(__file__), '..', 'logs', 'book_scout_last_run.txt')
BOOK_PREFERENCES_FILE = os.path.join(os.path.dirname(__file__), 'book_preferences.md')

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                history = json.load(f)
            for msg in history:
                if not isinstance(msg, dict) or 'role' not in msg or 'content' not in msg:
                    raise ValueError("Malformed message in history")
                if isinstance(msg['content'], str):
                    raise ValueError("Content is a plain string, not a list of blocks")
            return history
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[Nuncio] Warning: conversation history was corrupted and has been reset. ({e})")
            save_history([])
            return []
    return []

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2, default=str)


# --- Book Scout ---

def get_book_scout_status():
    if not os.path.exists(BOOK_SCOUT_FILE):
        return "never"
    with open(BOOK_SCOUT_FILE, 'r') as f:
        return f.read().strip()

def save_book_scout_timestamp():
    with open(BOOK_SCOUT_FILE, 'w') as f:
        f.write(datetime.datetime.now().strftime("%Y-%m-%d"))

def book_scout_prompt_fragment():
    last_run = get_book_scout_status()
    if last_run == "never":
        return "The book scout has never been run."
    try:
        last_date = datetime.datetime.strptime(last_run, "%Y-%m-%d")
        days_ago = (datetime.datetime.now() - last_date).days
        if days_ago >= 7:
            return f"The book scout was last run {days_ago} days ago (on {last_run}). This is more than a week — proactively ask Vernie at the start of this conversation if she would like you to run it now."
        else:
            return f"The book scout was last run {days_ago} days ago (on {last_run}). No need to prompt yet."
    except ValueError:
        return "The book scout has never been run."


# --- Google Auth ---
def get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return creds

# --- Calendar Tool ---
def get_calendar_events(query=None):
    try:
        creds = get_credentials()
        service = build('calendar', 'v3', credentials=creds)
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=20,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        if query:
            events = [e for e in events if query.lower() in e.get('summary', '').lower() or query.lower() in e.get('description', '').lower()]
        if not events:
            return "No upcoming events found."
        result = ""
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            result += f"{start}: {event['summary']}\n"
        return result
    except Exception as e:
        reason, retryable = _classify_error(e)
        return json.dumps({"status": "error", "reason": reason, "retryable": retryable, "detail": str(e)})

def create_calendar_event(summary, start_datetime, end_datetime, description=None):
    creds = get_credentials()
    service = build('calendar', 'v3', credentials=creds)
    event = {
        'summary': f'[Nuncio] {summary}',
        'start': {'dateTime': start_datetime, 'timeZone': 'Asia/Singapore'},
        'end': {'dateTime': end_datetime, 'timeZone': 'Asia/Singapore'},
    }
    if description:
        event['description'] = description
    created = service.events().insert(calendarId='primary', body=event).execute()
    return f"Event created: {created['summary']} on {start_datetime}"

def create_multiple_events(events_list):
    results = []
    for event in events_list:
        try:
            result = create_calendar_event(
                event['summary'],
                event['start_datetime'],
                event['end_datetime'],
                event.get('description')
            )
            results.append(f"✓ {result}")
        except Exception as e:
            results.append(f"✗ Failed to create '{event['summary']}': {str(e)}")
    return "\n".join(results)

# --- Gmail Tool ---
def get_recent_emails(query=None):
    try:
        creds = get_credentials()
        service = build('gmail', 'v1', credentials=creds)
        q = query if query else ''
        results = service.users().messages().list(userId='me', maxResults=5, q=q).execute()
        messages = results.get('messages', [])
        if not messages:
            return "No emails found."
        result = ""
        for msg in messages:
            txt = service.users().messages().get(userId='me', id=msg['id']).execute()
            headers = txt['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
            result += f"From: {sender}\nSubject: {subject}\n\n"
        return result
    except Exception as e:
        reason, retryable = _classify_error(e)
        return json.dumps({"status": "error", "reason": reason, "retryable": retryable, "detail": str(e)})

def send_email(to, subject, body):
    import base64
    from email.mime.text import MIMEText
    creds = get_credentials()
    service = build('gmail', 'v1', credentials=creds)
    message = MIMEText(body)
    message['to'] = to
    message['subject'] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().messages().send(userId='me', body={'raw': raw}).execute()
    return f"Email sent to {to} with subject '{subject}'"

# --- Drive Tools ---
def list_drive_files(query=None):
    try:
        creds = get_credentials()
        service = build('drive', 'v3', credentials=creds)
        q = f"name contains '{query}'" if query else ""
        results = service.files().list(
            pageSize=20,
            orderBy='modifiedTime desc',
            q=q,
            fields="files(id, name, mimeType, modifiedTime)"
        ).execute()
        files = results.get('files', [])
        if not files:
            return "No files found."
        result = f"Found {len(files)} files:\n\n"
        for f in files:
            result += f"{f['modifiedTime']}: {f['name']} ({f['mimeType']})\n"
        return result
    except Exception as e:
        reason, retryable = _classify_error(e)
        return json.dumps({"status": "error", "reason": reason, "retryable": retryable, "detail": str(e)})

def upload_to_drive(filename, content):
    creds = get_credentials()
    service = build('drive', 'v3', credentials=creds)
    file_metadata = {'name': filename}
    media = MediaIoBaseUpload(
        io.BytesIO(content.encode('utf-8')),
        mimetype='text/plain'
    )
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, name'
    ).execute()
    return f"File '{file['name']}' uploaded to Google Drive with ID: {file['id']}"


# --- Local File Tools ---

def list_files():
    result = []
    for root, dirs, files in os.walk(NUNCIO_FOLDER):
        for file in files:
            full = os.path.join(root, file)
            relative = os.path.relpath(full, NUNCIO_FOLDER)
            result.append(relative)
    return "\n".join(result) if result else "No files found."

def write_file(filepath, content):
    with open(filepath, 'w') as f:
        f.write(content)
    return f"File written to {filepath}"

def read_file(filepath):
    try:
        if os.path.isdir(filepath):
            return json.dumps({"status": "error", "reason": "path_is_directory", "retryable": False, "detail": f"'{filepath}' is a directory, not a file. Use list_files to see its contents."})
        with open(filepath, 'r') as f:
            return f.read()
    except FileNotFoundError as e:
        return json.dumps({"status": "error", "reason": "file_not_found", "retryable": False, "detail": str(e)})
    except PermissionError as e:
        return json.dumps({"status": "error", "reason": "permission_error", "retryable": False, "detail": str(e)})
    except Exception as e:
        reason, retryable = _classify_error(e)
        return json.dumps({"status": "error", "reason": reason, "retryable": retryable, "detail": str(e)})
    
def read_pdf(filepath):
    import fitz
    try:
        if not os.path.exists(filepath):
            return json.dumps({"status": "error", "reason": "file_not_found", "retryable": False, "detail": f"File not found: {filepath}"})
        doc = fitz.open(filepath)
        result = ""
        for page in doc:
            result += page.get_text()
        return result
    except PermissionError as e:
        return json.dumps({"status": "error", "reason": "permission_error", "retryable": False, "detail": str(e)})
    except Exception as e:
        reason, retryable = _classify_error(e)
        return json.dumps({"status": "error", "reason": reason, "retryable": retryable, "detail": str(e)})

def read_docx(filepath):
    from docx import Document
    try:
        if not os.path.exists(filepath):
            return json.dumps({"status": "error", "reason": "file_not_found", "retryable": False, "detail": f"File not found: {filepath}"})
        doc = Document(filepath)
        return "\n".join([para.text for para in doc.paragraphs])
    except PermissionError as e:
        return json.dumps({"status": "error", "reason": "permission_error", "retryable": False, "detail": str(e)})
    except Exception as e:
        reason, retryable = _classify_error(e)
        return json.dumps({"status": "error", "reason": reason, "retryable": retryable, "detail": str(e)})

# --- Browser session (persistent across tool calls) ---
_playwright_instance = None
_browser_instance = None
_page_instance = None

def _get_page():
    global _playwright_instance, _browser_instance, _page_instance
    from playwright.sync_api import sync_playwright
    if _playwright_instance is None:
        _playwright_instance = sync_playwright().start()
        _browser_instance = _playwright_instance.chromium.launch(headless=False)
        _page_instance = _browser_instance.new_page()
    return _page_instance

def browser_navigate(url):
    try:
        page = _get_page()
        page.goto(url, timeout=15000)
        return f"Navigated to: {page.title()}"
    except Exception as e:
        return f"Error: {str(e)}"

def browser_fill(label, text):
    try:
        page = _get_page()
        page.get_by_placeholder(label).fill(text)
        return f"Filled '{label}' with '{text}'"
    except Exception:
        try:
            page.get_by_label(label).fill(text)
            return f"Filled '{label}' with '{text}'"
        except Exception as e:
            return f"Could not find field '{label}': {str(e)}"

def browser_click(text):
    try:
        page = _get_page()
        page.get_by_role("button", name=text).click()
        return f"Clicked button '{text}'"
    except Exception:
        try:
            page.get_by_text(text).first.click()
            return f"Clicked '{text}'"
        except Exception as e:
            return f"Could not click '{text}': {str(e)}"

def browser_read():
    try:
        page = _get_page()
        title = page.title()
        text = page.inner_text("body")
        text = text[:5000] if len(text) > 5000 else text
        return f"Title: {title}\n\n{text}"
    except Exception as e:
        return f"Error reading page: {str(e)}"

def browser_close():
    global _playwright_instance, _browser_instance, _page_instance
    try:
        if _browser_instance:
            _browser_instance.close()
        if _playwright_instance:
            _playwright_instance.stop()
        _playwright_instance = None
        _browser_instance = None
        _page_instance = None
        return "Browser closed."
    except Exception as e:
        return f"Error closing browser: {str(e)}"

def fetch_url(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; Nuncio/1.0)'}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        # Remove script and style noise
        for tag in soup(['script', 'style', 'nav', 'footer']):
            tag.decompose()
        text = soup.get_text(separator='\n', strip=True)
        # Trim to avoid blowing the context window
        return text[:8000] if len(text) > 8000 else text
    except Exception as e:
        reason, retryable = _classify_error(e)
        return json.dumps({"status": "error", "reason": reason, "retryable": retryable, "detail": str(e)})

def run_book_scout():
    if not os.path.exists(BOOK_PREFERENCES_FILE):
        return "Error: book_preferences.md not found."
    with open(BOOK_PREFERENCES_FILE, 'r') as f:
        preferences = f.read()

    today = datetime.datetime.now()
    cutoff = (today - datetime.timedelta(days=30)).strftime("%d %B %Y")
    month_terms = today.strftime("%B %Y")
    prev_month_terms = (today.replace(day=1) - datetime.timedelta(days=1)).strftime("%B %Y")

    scout_system = f"""You are a specialist book research agent. Your sole task is to find new and recently reviewed books matching the reading preferences provided.

Today's date is {today.strftime('%d %B %Y')}.
Recency cutoff: only include books with reviews or announcements published on or after {cutoff}. Skip anything older — if you cannot confirm the date, skip it.

Search Publishers Weekly, The Guardian Books, Literary Hub, Tor.com, Locus Magazine, and the New Statesman.
Append '{month_terms}' or '{prev_month_terms}' to search queries to bias toward recent content.
Check for new releases by the favourite authors listed in the preferences.
Search across all categories (favourite authors, sci-fi, fantasy, literary fiction, non-fiction) before compiling anything.
Only compile the digest once you have 6–10 confirmed books within the recency window.

Format each entry as: **Title** — Author (Publisher, date) followed by 2–3 sentences on why it matches Vernie's taste.
Output ONLY the formatted digest. No preamble, no commentary, nothing else."""

    scout_messages = [{
        "role": "user",
        "content": [{"type": "text", "text": f"Find new book recommendations based on these preferences:\n\n{preferences}"}]
    }]
    scout_tools = [{"type": "web_search_20250305", "name": "web_search"}]

    print("[Book Scout agent starting...]")
    MAX_ITERATIONS = 30
    for _ in range(MAX_ITERATIONS):
        for attempt in range(3):
            try:
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=8192,
                    system=scout_system,
                    tools=scout_tools,
                    messages=scout_messages
                )
                break
            except anthropic.APIStatusError as e:
                if e.status_code == 529 and attempt < 2:
                    time.sleep(15)
                else:
                    raise

        if response.stop_reason == "end_turn":
            digest = "".join(b.text for b in response.content if b.type == "text")
            save_book_scout_timestamp()
            print("[Book Scout agent complete.]")
            return digest

        else:
            print(f"[Book Scout] unexpected stop_reason: {response.stop_reason}")
            break

    return "Book scout did not complete — please try again."


# --- Action Log ---
ACTION_LOG_FILE = os.path.join(os.path.dirname(__file__), '..', 'logs', 'action_log.jsonl')
MEMORY_FILE = MEMORY_FILE = os.path.join(os.path.dirname(__file__), '..', 'logs', 'memory.json')

def append_action_log(tool_name, tool_input, result, confirmation_status):
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "tool": tool_name,
        "input": tool_input,
        "result_preview": str(result)[:300],
        "confirmation": confirmation_status,
    }
    with open(ACTION_LOG_FILE, 'a') as f:
        f.write(json.dumps(entry) + '\n')


# --- Memory Store ---

ALLOWED_MEMORY_CATEGORIES = {"contact", "project", "preference", "standing_instruction"}

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, 'w') as f:
            json.dump([], f)
        return []
    try:
        with open(MEMORY_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return []

def save_memory(memories):
    with open(MEMORY_FILE, 'w') as f:
        json.dump(memories, f, indent=2, default=str)

def remember(key, value, category, source, url=None):
    if category not in ALLOWED_MEMORY_CATEGORIES:
        return f"Error: category '{category}' is not allowed. Must be one of: {', '.join(sorted(ALLOWED_MEMORY_CATEGORIES))}."
    memories = load_memory()
    numbered = [int(m['id'].split('_')[1]) for m in memories if m.get('id', '').startswith('mem_') and m['id'].split('_')[1].isdigit()]
    new_id = f"mem_{(max(numbered) + 1):03d}" if numbered else "mem_001"
    entry = {
        "id": new_id,
        "key": key,
        "value": value,
        "category": category,
        "source": source,
        "timestamp": datetime.datetime.now().isoformat(),
        "url": url if source == "external_url" else None,
    }
    memories.append(entry)
    save_memory(memories)
    return f"Memory saved with id {new_id}: [{category}] {key} = {value}"

def recall(query):
    memories = load_memory()
    q = query.lower()
    matches = [m for m in memories if q in m.get('key', '').lower() or q in m.get('value', '').lower()]
    if not matches:
        return f"No memories found matching '{query}'."
    lines = [f"Found {len(matches)} memory/memories matching '{query}':\n"]
    for m in matches:
        lines.append(f"[{m['id']}] ({m['category']}) {m['key']}: {m['value']}")
        lines.append(f"  source: {m['source']} | {m['timestamp']}")
        if m.get('url'):
            lines.append(f"  url: {m['url']}")
    return "\n".join(lines)

def list_memories():
    memories = load_memory()
    if not memories:
        return "No memories stored yet."
    lines = [f"Stored memories ({len(memories)} total):\n"]
    for m in memories:
        lines.append(f"[{m['id']}] ({m['category']}) {m['key']}: {m['value']}")
        lines.append(f"  source: {m['source']} | {m['timestamp']}")
        if m.get('url'):
            lines.append(f"  url: {m['url']}")
    return "\n".join(lines)

def delete_memory(memory_id):
    memories = load_memory()
    original_count = len(memories)
    memories = [m for m in memories if m.get('id') != memory_id]
    if len(memories) == original_count:
        return f"Error: no memory found with id '{memory_id}'."
    save_memory(memories)
    return f"Memory '{memory_id}' deleted."

def load_memory_for_prompt():
    memories = load_memory()
    trusted = [m for m in memories if m.get('source') in ('user_stated', 'inferred_from_conversation')]
    if not trusted:
        return "## Memory\nNo stored memories yet."
    lines = ["## Memory"]
    for m in trusted:
        lines.append(f"[{m['id']}] ({m['category']}) {m['key']}: {m['value']}  [{m['source']}]")
    return "\n".join(lines)


# --- Tool Definitions ---
tools = [
    {
        "name": "get_calendar_events",
        "description": "Get upcoming calendar events. Optionally filter by a search query such as a person's name or event title.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional search term to filter events"}
            },
            "required": []
        }
    },
    {
        "name": "get_recent_emails",
        "description": "Get recent emails from Gmail. Optionally filter by a search query such as a sender's name or subject.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional search term to filter emails"}
            },
            "required": []
        }
    },
    {
        "name": "send_email",
        "description": "Send an email via Gmail on behalf of Vernie.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body text"}
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "list_drive_files",
        "description": "List files in Google Drive, showing the most recently modified. Optionally filter by filename.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional filename search term"}
            },
            "required": []
        }
    },
    {
        "name": "upload_to_drive",
        "description": "Upload a text file to Google Drive.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Name of the file to create in Drive"},
                "content": {"type": "string", "description": "Text content to write to the file"}
            },
            "required": ["filename", "content"]
        }
    },
    {
        "name": "write_file",
        "description": "Write text content to a file on the local filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Full path where the file should be written"},
                "content": {"type": "string", "description": "Text content to write"}
            },
            "required": ["filepath", "content"]
        }
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file from the local filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Full path of the file to read"}
            },
            "required": ["filepath"]
        }
    },
    {
    "name": "read_pdf",
    "description": "Read the text contents of a PDF file.",
    "input_schema": {
        "type": "object",
        "properties": {
            "filepath": {"type": "string", "description": "Full path to the PDF file"}
        },
        "required": ["filepath"]
        }
    },
    {
        "name": "read_docx",
        "description": "Read the text contents of a Word document (.docx).",
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Full path to the Word document"}
            },
            "required": ["filepath"]
        }
    },
    {
    "name": "create_calendar_event",
    "description": "Create a new event on Vernie's Google Calendar.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Event title"},
            "start_datetime": {"type": "string", "description": "Start time in ISO 8601 format, e.g. 2026-03-15T09:00:00"},
            "end_datetime": {"type": "string", "description": "End time in ISO 8601 format, e.g. 2026-03-15T10:00:00"},
            "description": {"type": "string", "description": "Optional event description"}
        },
        "required": ["summary", "start_datetime", "end_datetime"]
        }
    },
    {
    "name": "create_multiple_events",
    "description": "Create multiple calendar events at once as a batch.",
    "input_schema": {
        "type": "object",
        "properties": {
            "events_list": {
                "type": "array",
                "description": "List of events to create",
                "items": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "start_datetime": {"type": "string"},
                        "end_datetime": {"type": "string"},
                        "description": {"type": "string"}
                    },
                    "required": ["summary", "start_datetime", "end_datetime"]
                }
            }
        },
        "required": ["events_list"]
        }
    },
    {
    "name": "list_files",
    "description": "List files available in the Nuncio working folder.",
    "input_schema": {
        "type": "object",
        "properties": {}
        }
    },
    {
        "name": "browser_navigate",
        "description": "Open a URL in a headless Chromium browser. Use this to start a browsing session or navigate to a new page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The full URL to open, including https://"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "browser_fill",
        "description": "Type text into a form field identified by its placeholder or label text. Use after browser_navigate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "The placeholder or label text of the field to fill"},
                "text": {"type": "string", "description": "The text to type into the field"}
            },
            "required": ["label", "text"]
        }
    },
    {
        "name": "browser_click",
        "description": "Click a button or link on the current page by its visible text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The visible text of the button or link to click"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "browser_read",
        "description": "Read the title and text content of the current browser page.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "browser_close",
        "description": "Close the browser window when done browsing.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "fetch_url",
        "description": "Fetch and read the content of a webpage given its URL. Use this to read research pages, news articles, or any specific URL Vernie provides.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The full URL to fetch, including https://"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "run_book_scout",
        "description": "Run the weekly book scout. Spins up a specialist search agent that finds new and recently reviewed books matching Vernie's preferences, then returns a formatted digest. Call this when Vernie asks for the book search, or when she confirms she wants it run.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "remember",
        "description": "Store a fact in persistent memory. Use this to save things Vernie tells you about contacts, projects, preferences, or standing instructions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Short label for this memory, e.g. 'preferred_sign_off' or 'contact_role'"},
                "value": {"type": "string", "description": "The fact to store"},
                "category": {"type": "string", "description": "One of: contact, project, preference, standing_instruction"},
                "source": {"type": "string", "description": "One of: user_stated, inferred_from_conversation, external_url"},
                "url": {"type": "string", "description": "Source URL — required if source is external_url, otherwise omit"}
            },
            "required": ["key", "value", "category", "source"]
        }
    },
    {
        "name": "recall",
        "description": "Search persistent memory for entries matching a query. Returns all entries where the query appears in the key or value.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term to look for in memory keys and values"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "list_memories",
        "description": "List all entries in persistent memory, showing id, key, value, category, source, and timestamp.",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "delete_memory",
        "description": "Delete a memory entry by its id (e.g. mem_001). Use list_memories to find the id first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The id of the memory entry to delete, e.g. mem_001"}
            },
            "required": ["id"]
        }
    },
    {
        "type": "web_search_20250305",
        "name": "web_search",
    },
]

# --- Tool Executor ---
def execute_tool(tool_name, tool_input):
    if tool_name == "get_calendar_events":
        return get_calendar_events(tool_input.get("query"))
    elif tool_name == "get_recent_emails":
        return get_recent_emails(tool_input.get("query"))
    elif tool_name == "send_email":
        return send_email(tool_input["to"], tool_input["subject"], tool_input["body"])
    elif tool_name == "list_drive_files":
        return list_drive_files(tool_input.get("query"))
    elif tool_name == "upload_to_drive":
        return upload_to_drive(tool_input["filename"], tool_input["content"])
    elif tool_name == "list_files":
        return list_files()
    elif tool_name == "write_file":
        return write_file(tool_input["filepath"], tool_input["content"])
    elif tool_name == "read_file":
        return read_file(tool_input["filepath"])
    elif tool_name == "read_pdf":
        return read_pdf(tool_input["filepath"])
    elif tool_name == "read_docx":
        return read_docx(tool_input["filepath"])
    elif tool_name == "create_calendar_event":
        return create_calendar_event(
            tool_input["summary"],
            tool_input["start_datetime"],
            tool_input["end_datetime"],
            tool_input.get("description")
        )
    elif tool_name == "create_multiple_events":
        return create_multiple_events(tool_input["events_list"])
    elif tool_name == "browser_navigate":
        return browser_navigate(tool_input["url"])
    elif tool_name == "browser_fill":
        return browser_fill(tool_input["label"], tool_input["text"])
    elif tool_name == "browser_click":
        return browser_click(tool_input["text"])
    elif tool_name == "browser_read":
        return browser_read()
    elif tool_name == "browser_close":
        return browser_close()
    elif tool_name == "fetch_url":
        return fetch_url(tool_input["url"])
    elif tool_name == "run_book_scout":
        return run_book_scout()
    elif tool_name == "remember":
        return remember(
            tool_input["key"],
            tool_input["value"],
            tool_input["category"],
            tool_input["source"],
            tool_input.get("url")
        )
    elif tool_name == "recall":
        return recall(tool_input["query"])
    elif tool_name == "list_memories":
        return list_memories()
    elif tool_name == "delete_memory":
        return delete_memory(tool_input["id"])
    return "Tool not found."

# --- Anthropic client (module-level so run_book_scout can use it when imported) ---
client = anthropic.Anthropic()

# --- Main Loop ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=str, default=None, help="Run headlessly with this task and exit")
    args = parser.parse_args()
    headless = args.task is not None

    conversation_history = load_history()
    _book_scout_status = book_scout_prompt_fragment()
    _memory_section = load_memory_for_prompt()

    system_prompt = f"""You are Nuncio, an AI agent with delegated authority to act on behalf of your principal, Vernie.
Today's date is {datetime.datetime.now().strftime("%A, %d %B %Y")}.
You are precise, loyal, and operate within clearly defined boundaries.
You are informed by Jesuit Catholic values.
You have access to Vernie's calendar, Gmail, Google Drive, and local filesystem.
You can search the web and fetch URLs to find current information, news, and research when Vernie asks about external topics.
Use your tools whenever a question requires real data.
Always tell Vernie what you found, not just that you looked.
When sending any email, always prefix the subject line with "[Nuncio] " and append the following line at the very bottom of the email body: "Email sent by Nuncio, Vernie's agent".
You have access to a local inbox folder at {NUNCIO_FOLDER}. Use the list_files tool to see what files are inside it. Only use read_file on specific files returned by list_files, never on folder paths.

## Book Scout
{_book_scout_status}
When Vernie asks for a book search, or confirms she wants one, call the run_book_scout tool. It will spin up a specialist search agent that does all the research and returns a formatted digest — you do not need to search yourself. Present the digest to Vernie as-is when it arrives.

{_memory_section}
"""

    if not headless:
        print("Serviam! Nuncio is ready. Type 'exit' to quit.\n")

    while True:
        if headless:
            user_input = args.task
        else:
            # user_input = input("You: ")  # original
            user_input = input("You: ")

        if not headless and user_input.lower() == "exit":
            print("Ite in pace. Nuncio signing off.")
            break

        conversation_history.append({
            "role": "user",
            "content": [{"type": "text", "text": user_input}]
        })

        tool_call_counts = {}
        MAX_TOOL_RETRIES = 3

        while True:
            for attempt in range(3):
                try:
                    response = client.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=4096,
                        system=system_prompt,
                        tools=tools,
                        messages=conversation_history
                    )
                    break
                except anthropic.APIStatusError as e:
                    if e.status_code == 529 and attempt < 2:
                        print(f"[Nuncio] API overloaded, retrying in 15 seconds... (attempt {attempt + 1}/3)")
                        time.sleep(15)
                    else:
                        raise

            if response.stop_reason == "tool_use":
                tool_results = []
                SILENT_TOOLS = {"run_book_scout"}
                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input

                        if tool_name in CONFIRMATION_REQUIRED:
                            if headless:
                                confirmation_status = "granted"
                            else:
                                print(f"\n[Nuncio wants to use tool: {tool_name}]")
                                print(json.dumps(tool_input, indent=2))
                                if tool_name == "remember" and tool_input.get("source") == "external_url":
                                    print("⚠ This memory was derived from external content. Verify before confirming.")
                                answer = input("Confirm? (yes/no): ").strip().lower()
                                if answer != "yes":
                                    result = "Action cancelled by user."
                                    confirmation_status = "denied"
                                    append_action_log(tool_name, tool_input, result, confirmation_status)
                                else:
                                    confirmation_status = "granted"
                        else:
                            confirmation_status = "not_required"

                        if confirmation_status in ("granted", "not_required"):
                            tool_call_counts[tool_name] = tool_call_counts.get(tool_name, 0) + 1
                            if tool_call_counts[tool_name] > MAX_TOOL_RETRIES:
                                result = json.dumps({
                                    "status": "error",
                                    "reason": "retry_limit_reached",
                                    "retryable": False,
                                    "detail": f"{tool_name} has been called {tool_call_counts[tool_name]} times this turn. Stopping. Report this failure to Vernie and do not retry."
                                })
                                append_action_log(tool_name, tool_input, result, confirmation_status)
                            else:
                                print(f"[Nuncio is using tool: {tool_name}]")
                                result = execute_tool(tool_name, tool_input)
                                if tool_name not in SILENT_TOOLS:
                                    print(f"[Tool result for {tool_name}]: {result}")
                                append_action_log(tool_name, tool_input, result, confirmation_status)

                        MAX_TOOL_RESULT_LENGTH = 10000
                        if isinstance(result, str) and len(result) > MAX_TOOL_RESULT_LENGTH:
                            history_result = result[:MAX_TOOL_RESULT_LENGTH] + "\n[...truncated for history...]"
                        else:
                            history_result = result

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": history_result
                        })

                serialized = []
                for block in response.content:
                    if block.type == "text" and block.text:
                        serialized.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        serialized.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
                conversation_history.append({
                    "role": "assistant",
                    "content": serialized
                })
                conversation_history.append({
                    "role": "user",
                    "content": tool_results
                })

            else:
                reply = next((b.text for b in response.content if b.type == "text"), "")
                if reply:
                    conversation_history.append({
                        "role": "assistant",
                        "content": [{"type": "text", "text": reply}]
                    })
                    print(f"\nNuncio: {reply}\n")
                else:
                    print("[Nuncio completed action with no text response]\n")
                save_history(conversation_history)
                break  # exits inner tool loop

        if headless:
            break  # exits outer user-turn loop after single headless task
