import os
import threading
import queue
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from app.utils.date_utils import utcnow


class GoogleSheetsService:
    _instance = None

    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]

    HEADERS = [
        'Timestamp', 'User', 'User ID', 'Action Type', 'Action',
        'Page', 'Element', 'Details', 'IP Address', 'Session ID'
    ]

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._default_spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
        self._client = None
        self._worksheet_cache = {}
        self._write_queue = queue.Queue()
        self._start_flush_worker()

    def _start_flush_worker(self):
        threading.Thread(target=self._flush_worker, daemon=True).start()

    def _flush_worker(self):
        while True:
            batch = []
            try:
                item = self._write_queue.get(timeout=5)
                batch.append(item)
                while not self._write_queue.empty() and len(batch) < 20:
                    try:
                        batch.append(self._write_queue.get_nowait())
                    except queue.Empty:
                        break
            except queue.Empty:
                continue

            if batch:
                self._flush_batch(batch)

    def _flush_batch(self, batch):
        grouped = {}
        for row_data, spreadsheet_id in batch:
            if spreadsheet_id not in grouped:
                grouped[spreadsheet_id] = []
            grouped[spreadsheet_id].append(row_data)

        for spreadsheet_id, rows in grouped.items():
            try:
                ws = self._get_worksheet(spreadsheet_id)
                if ws and rows:
                    ws.append_rows(rows, value_input_option='USER_ENTERED')
            except Exception as e:
                print(f"Google Sheets write error: {e}")
                self._worksheet_cache = {}

    def _get_client(self):
        if self._client is None:
            creds = Credentials.from_service_account_file(
                'serviceAccountKey.json', scopes=self.SCOPES
            )
            self._client = gspread.authorize(creds)
        return self._client

    def _get_worksheet(self, spreadsheet_id=None):
        sid = spreadsheet_id or self._default_spreadsheet_id
        if not sid:
            return None

        cache_key = f"blogs:{sid}"
        if cache_key in self._worksheet_cache:
            return self._worksheet_cache[cache_key]

        try:
            spreadsheet = self._get_client().open_by_key(sid)
            try:
                ws = spreadsheet.worksheet('Blogs')
            except gspread.WorksheetNotFound:
                ws = spreadsheet.add_worksheet(title='Blogs', rows=1000, cols=10)

            if ws.row_values(1) != self.HEADERS:
                ws.update(range_name='A1:J1', values=[self.HEADERS])
                ws.format('A1:J1', {'textFormat': {'bold': True}})

            self._worksheet_cache[cache_key] = ws
            return ws
        except Exception as e:
            print(f"Google Sheets worksheet error: {e}")
            return None

    def _append(self, row, spreadsheet_id=None):
        self._write_queue.put((row, spreadsheet_id))

    def log_bulk_activities(self, events, spreadsheet_id=None):
        for event in events:
            row = [
                event.get('timestamp', utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')),
                event.get('user_name', ''),
                event.get('user_id', ''),
                event.get('action_type', 'click'),
                event.get('action', ''),
                event.get('page', ''),
                event.get('element', ''),
                event.get('details', ''),
                event.get('ip_address', ''),
                event.get('session_id', '')
            ]
            self._append(row, spreadsheet_id)
        return True

    def get_recent_activities(self, spreadsheet_id=None, limit=10):
        try:
            ws = self._get_worksheet(spreadsheet_id)
            if not ws:
                return []
            all_values = ws.get_all_values()
            if len(all_values) <= 1:
                return []
            rows = all_values[1:]
            recent = rows[-limit:] if len(rows) >= limit else rows
            recent.reverse()
            return [
                {
                    'timestamp': r[0] if len(r) > 0 else '',
                    'user': r[1] if len(r) > 1 else '',
                    'user_id': r[2] if len(r) > 2 else '',
                    'action_type': r[3] if len(r) > 3 else '',
                    'action': r[4] if len(r) > 4 else '',
                    'page': r[5] if len(r) > 5 else '',
                    'element': r[6] if len(r) > 6 else '',
                    'details': r[7] if len(r) > 7 else '',
                }
                for r in recent
            ]
        except Exception as e:
            print(f"Google Sheets read error: {e}")
            return []

    def log_activity(self, user_name, action_type, action_text, blog_title="", details="", spreadsheet_id=None):
        timestamp = utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        row = [timestamp, user_name, '', action_type, action_text, '', '', blog_title or details, '', '']
        self._append(row, spreadsheet_id)
        return True

    def sync_user(self, uid, name, email, role, created_by="", created_at=None, last_login=None, spreadsheet_id=None):
        timestamp = utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        row = [timestamp, name, uid, 'user_sync', f"{name} ({email}) - {role}", '', '', f"Created by: {created_by}", '', '']
        self._append(row, spreadsheet_id)
        return True

    def sync_blog(self, blog_id, title, status, category="", author_id="", created_at=None, updated_at=None, author_name="", spreadsheet_id=None):
        timestamp = utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        row = [timestamp, author_name, author_id, 'blog_sync', f"{title} [{status}]", '', '', f"Category: {category}, ID: {blog_id}", '', '']
        self._append(row, spreadsheet_id)
        return True

    @staticmethod
    def get_spreadsheet_id_for_user(user_id):
        try:
            from app.firebase.firestore_service import FirestoreService
            db = FirestoreService()
            settings = db.get_site_settings(user_id)
            val = settings.get('google_sheets_id', '').strip()
            if val:
                return val
        except Exception:
            pass
        return os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
