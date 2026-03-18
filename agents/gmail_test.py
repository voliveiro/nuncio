from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import os

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
CREDS_FILE = '/home/vernie/nuncio/keys/google_credentials.json'
TOKEN_FILE = '/home/vernie/nuncio/keys/gmail_token.json'

def get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

service = get_gmail_service()

results = service.users().messages().list(userId='me', maxResults=5).execute()
messages = results.get('messages', [])

for msg in messages:
    txt = service.users().messages().get(userId='me', id=msg['id']).execute()
    headers = txt['payload']['headers']
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
    sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
    print(f"From: {sender}\nSubject: {subject}\n")