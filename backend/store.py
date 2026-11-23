from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions(
                    id TEXT PRIMARY KEY, camera_id TEXT, source TEXT,
                    started_at TEXT, ended_at TEXT, status TEXT);
                CREATE TABLE IF NOT EXISTS events(
                    id TEXT PRIMARY KEY, session_id TEXT, camera_id TEXT,
                    event_type TEXT, track_id INTEGER, timestamp TEXT,
                    message TEXT, meta TEXT, snapshot_path TEXT,
                    review_status TEXT NOT NULL DEFAULT 'pending', note TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS events_time ON events(timestamp);
                CREATE TABLE IF NOT EXISTS reports(
                    id TEXT PRIMARY KEY, created_at TEXT, title TEXT, content TEXT, model TEXT);
            ''')
            # A new backend process cannot resume threads owned by a previous process.
            db.execute("UPDATE sessions SET status='interrupted', ended_at=? WHERE ended_at IS NULL", (self.now(),))

    @staticmethod
    def now():
        return datetime.now().astimezone().isoformat(timespec='seconds')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def start_session(self, session_id, camera_id, source):
        with self.connect() as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',
                       (session_id, camera_id, source, self.now(), None, 'running'))

    def end_session(self, session_id, status):
        with self.connect() as db:
            db.execute('UPDATE sessions SET ended_at=?,status=? WHERE id=? AND ended_at IS NULL',
                       (self.now(), status, session_id))

    def add_event(self, event, session_id, camera_id):
        with self.connect() as db:
            db.execute('''INSERT OR IGNORE INTO events
                (id,session_id,camera_id,event_type,track_id,timestamp,message,meta,snapshot_path)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (event['id'], session_id, camera_id, event['event_type'], int(event['track_id']),
                 event['timestamp'], event['message'], json.dumps(event.get('meta', {}), ensure_ascii=False),
                 event.get('snapshot_path', '')))

    @staticmethod
    def event_dict(row):
        item = dict(row)
        item['meta'] = json.loads(item['meta'])
        item['snapshot_url'] = f"/api/events/{item['id']}/snapshot" if item['snapshot_path'] else None
        item.pop('snapshot_path')
        return item

    def events(self, event_type='', review_status='', session_id='', camera_id='', since='', until='', limit=50, offset=0):
        terms, values = [], []
        for column, value in [('event_type', event_type), ('review_status', review_status),
                              ('session_id', session_id), ('camera_id', camera_id)]:
            if value:
                terms.append(column + '=?')
                values.append(value)
        if since:
            terms.append('timestamp>=?')
            values.append(since)
        if until:
            terms.append('timestamp<=?')
            values.append(until + 'T23:59:59' if len(until) == 10 else until)
        clause = ' WHERE ' + ' AND '.join(terms) if terms else ''
        with self.connect() as db:
            count = db.execute('SELECT count(*) FROM events' + clause, values).fetchone()[0]
            rows = db.execute('SELECT * FROM events' + clause + ' ORDER BY timestamp DESC,rowid DESC LIMIT ? OFFSET ?',
                              [*values, limit, offset]).fetchall()
        return {'total': count, 'items': [self.event_dict(row) for row in rows]}

    def event(self, event_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
        return dict(row) if row else None

    def review(self, event_id, status, note):
        with self.connect() as db:
            return db.execute('UPDATE events SET review_status=?,note=? WHERE id=?', (status, note, event_id)).rowcount

    def stats(self):
        today = datetime.now().date().isoformat()
        with self.connect() as db:
            types = dict(db.execute('SELECT event_type,count(*) FROM events GROUP BY event_type').fetchall())
            pending = db.execute("SELECT count(*) FROM events WHERE review_status='pending' AND track_id>=0").fetchone()[0]
            today_count = db.execute('SELECT count(*) FROM events WHERE timestamp>=? AND track_id>=0', (today,)).fetchone()[0]
            hourly = db.execute("SELECT substr(timestamp,12,2),count(*) FROM events WHERE timestamp>=? AND track_id>=0 GROUP BY 1", (today,)).fetchall()
            sessions = db.execute('SELECT count(*) FROM sessions').fetchone()[0]
        return {'total': sum(v for k, v in types.items() if k in {'overspeed','illegal_parking','reverse_driving'}),
                'today': today_count, 'pending': pending, 'types': types, 'sessions': sessions,
                'hourly': {str(k): v for k, v in hourly}}

    def sessions(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM sessions ORDER BY started_at DESC LIMIT 30')]

    def add_report(self, report):
        with self.connect() as db:
            db.execute('INSERT INTO reports VALUES(?,?,?,?,?)',
                       (report['id'], report['created_at'], report['title'], report['content'], report['model']))

    def reports(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM reports ORDER BY created_at DESC LIMIT 30')]

    def report(self, report_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM reports WHERE id=?', (report_id,)).fetchone()
        return dict(row) if row else None
