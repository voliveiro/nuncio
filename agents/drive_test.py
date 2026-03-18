from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import os

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
CREDS_FILE = '/home/vernie/nuncio/keys/google_credentials.json'
TOKEN_FILE = '/home/vernie/nuncio/keys/drive_token.json'

def get_drive_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)

service = get_drive_service()

results = service.files().list(
    pageSize=10,
    orderBy='modifiedTime desc',
    fields="files(id, name, mimeType, modifiedTime)"
).execute()

files = results.get('files', [])

if not files:
    print('No files found.')
else:
    print('Your 10 most recently modified files:\n')
    for f in files:
        print(f"{f['modifiedTime']}: {f['name']} ({f['mimeType']})")