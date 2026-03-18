import anthropic
import datetime 
import os
import io
import json
import requests
import time
from bs4 import BeautifulSoup 
from duckduckgo_search import DDGS
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# --- Config ---
CREDS_FILE = '/home/vernie/nuncio/keys/google_credentials.json'
TOKEN_FILE = '/home/vernie/nuncio/keys/google_token.json'

SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/drive.readonly'
]

NUNCIO_FOLDER ='/home/vernie/nuncio/nuncio-inbox'

# --- History --- 

HISTORY_FILE = '/home/vernie/nuncio/logs/conversation_history.json'

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
    if os.path.isdir(filepath):
        return f"Error: '{filepath}' is a directory, not a file. Use list_files to see its contents."
    if not os.path.exists(filepath):
        return f"File not found: {filepath}"
    with open(filepath, 'r') as f:
        return f.read()
    
def read_pdf(filepath):
    import fitz
    if not os.path.exists(filepath):
        return f"File not found: {filepath}"
    doc = fitz.open(filepath)
    result = ""
    for page in doc:
        result += page.get_text()
    return result

def read_docx(filepath):
    from docx import Document
    if not os.path.exists(filepath):
        return f"File not found: {filepath}"
    doc = Document(filepath)
    return "\n".join([para.text for para in doc.paragraphs])

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
        return f"Error fetching URL: {str(e)}"

def web_search(query, max_results=5):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "No results found."
        output = ""
        for r in results:
            output += f"Title: {r['title']}\nURL: {r['href']}\nSummary: {r['body']}\n\n"
        return output
    except Exception as e:
        return f"Error performing search: {str(e)}"

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
        "name": "web_search",
        "description": "Search the web using DuckDuckGo. Use this to find recent news, research, or information not available in Vernie's local files or Google services.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "max_results": {"type": "integer", "description": "Number of results to return (default 5)"}
            },
            "required": ["query"]
        }
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
    elif tool_name == "fetch_url":
        return fetch_url(tool_input["url"])
    elif tool_name == "web_search":
        return web_search(tool_input["query"], tool_input.get("max_results", 5))
    return "Tool not found."

# --- Main Loop ---
client = anthropic.Anthropic()
conversation_history = load_history()

system_prompt = f"""You are Nuncio, an AI agent with delegated authority to act on behalf of your principal, Vernie.
Today's date is {datetime.datetime.now().strftime("%A, %d %B %Y")}.
You are precise, loyal, and operate within clearly defined boundaries.
You are informed by Jesuit Catholic values.
You have access to Vernie's calendar, Gmail, Google Drive, and local filesystem.
You can search the web and fetch URLs to find current information, news, and research when Vernie asks about external topics.
Use your tools whenever a question requires real data.
Always tell Vernie what you found, not just that you looked.
Always show Vernie the event details and ask for explicit confirmation before creating a calendar event.
Always show Vernie the draft email and ask for explicit confirmation before sending. When Vernie confirms, immediately call the send_email tool with no further questions.
You have access to a local inbox folder at /home/vernie/nuncio/nuncio-inbox. Use the list_files tool to see what files are inside it. Only use read_file on specific files returned by list_files, never on folder paths.
"""

print("Serviam! Nuncio is ready. Type 'exit' to quit.\n")

while True:
    user_input = input("You: ")

    if user_input.lower() == "exit":
        print("Ite in pace. Nuncio signing off.")
        break

    conversation_history.append({
        "role": "user",
        "content": user_input
    })

    while True:
        for attempt in range(3):
            try:
                response = client.messages.create(
                    model="claude-opus-4-6",
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
            for block in response.content:
                if block.type == "tool_use":
                    print(f"[Nuncio is using tool: {block.name}]")
                    result = execute_tool(block.name, block.input)
                    print(f"[Tool result for {block.name}]: {result}")

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

                    
            conversation_history.append({
                "role": "assistant",
                "content": response.content
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
            break
