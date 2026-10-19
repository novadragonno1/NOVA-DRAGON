"""Private, durable task ledger. No network access and no code execution."""
import json
from pathlib import Path
import re
import sqlite3
import uuid

from nova_runtime import TaskError


def brief(text):
    text = re.sub(r'gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|'
                  r'AIza[A-Za-z0-9_-]+|\d{7,12}:[A-Za-z0-9_-]{30,}', '[REDACTED]', text)
    return text[:300]


class TaskStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
            summary TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chat_id, message_id))''')
        self.db.commit()
        path.chmod(0o600)

    def close(self):
        self.db.close()

    def create(self, chat_id, message_id, prompt):
        with self.db:
            self.db.execute('''INSERT OR IGNORE INTO tasks
                (id, chat_id, message_id, summary, status, phase)
                VALUES (?, ?, ?, ?, 'running', 'received')''',
                (uuid.uuid4().hex, chat_id, message_id, brief(prompt)))
        row = self.db.execute('SELECT id FROM tasks WHERE chat_id=? AND message_id=?',
                              (chat_id, message_id)).fetchone()
        return Task(self, row['id'])

    def find_message(self, chat_id, message_id):
        row = self.db.execute('SELECT id FROM tasks WHERE chat_id=? AND message_id=?',
                              (chat_id, message_id)).fetchone()
        return row['id'] if row else None

    def get(self, task_id):
        if not re.fullmatch('[a-f0-9]{32}', task_id):
            raise TaskError('رقم المهمة غير صالح. استخدم /tasks لعرض الأرقام.')
        row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise TaskError('المهمة غير موجودة في هذا الجهاز.')
        data = dict(row)
        data['details'] = json.loads(data['details'])
        return data

    def update(self, task_id, status=None, phase=None, error='', **details):
        old = self.get(task_id)
        old['details'].update(details)
        with self.db:
            self.db.execute('''UPDATE tasks SET status=?, phase=?, details=?, error=?,
                updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                (status or old['status'], phase or old['phase'],
                 json.dumps(old['details'], ensure_ascii=False), brief(error), task_id))

    def recover_startup(self):
        with self.db:
            self.db.execute("UPDATE tasks SET status='interrupted', updated_at=CURRENT_TIMESTAMP "
                            "WHERE status='running'")

    def pending(self):
        return [self.get(row['id']) for row in self.db.execute(
            "SELECT id FROM tasks WHERE status IN ('running','interrupted','needs_review','awaiting_sync') "
            'ORDER BY rowid')]

    def recent(self):
        return [self.get(row['id']) for row in self.db.execute('SELECT id FROM tasks ORDER BY rowid DESC LIMIT 10')]

    def summary(self, task_id):
        row = self.get(task_id)
        d = row['details']
        return (f"المهمة: {row['id']}\nالحالة: {row['status']} / {row['phase']}\n"
                f"الطلب: {row['summary']}\nالملفات: {', '.join(d.get('files', [])) or 'لا يوجد'}\n"
                f"Commit: {d.get('commit', 'لم يُسجّل')}\n"
                f"التحقق: {d.get('validation', 'لم يبدأ')}\n"
                f"الاختبارات التنفيذية: لم تُشغّل\n{row['error']}")


class Task:
    def __init__(self, store, task_id):
        self.store, self.id = store, task_id

    def mark(self, phase, status='running', **details):
        self.store.update(self.id, status=status, phase=phase, **details)

    def fail(self, message):
        row = self.store.get(self.id)
        if row['status'] in ('completed', 'cancelled'):
            return  # A reply failure must never undo a recorded success.
        status = ('awaiting_sync' if row['phase'] == 'committed' else
                  'needs_review' if row['phase'] in ('prepared', 'saved') else 'failed')
        self.store.update(self.id, status=status, error=message)
