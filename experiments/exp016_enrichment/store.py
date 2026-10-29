"""Disposable private ledger + profile copies. No canonical registry or network DB."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
from contracts import digest, encoded


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError('LEDGER_SYMLINK')
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(self.path)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS changes (id TEXT PRIMARY KEY, body TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    def put(self, table, key, body):
        if table not in {'metadata', 'profiles', 'jobs', 'receipts', 'observations', 'changes'}:
            raise ValueError('TABLE_NOT_ALLOWED')
        column = 'key' if table == 'metadata' else 'id'
        return self.db.execute(f'INSERT OR IGNORE INTO {table} ({column}, body) VALUES (?, ?)',
                               (key, encoded(body).decode())).rowcount == 1

    def get(self, table, key):
        if table not in {'metadata', 'profiles', 'jobs', 'receipts', 'observations', 'changes'}:
            raise ValueError('TABLE_NOT_ALLOWED')
        column = 'key' if table == 'metadata' else 'id'
        row = self.db.execute(f'SELECT body FROM {table} WHERE {column}=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, table):
        if table not in {'profiles', 'jobs', 'receipts', 'observations', 'changes'}:
            raise ValueError('TABLE_NOT_ALLOWED')
        return [json.loads(r[0]) for r in self.db.execute(f'SELECT body FROM {table} ORDER BY id')]

    def metadata(self, key, value):
        self.db.execute('INSERT INTO metadata(key,body) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body',
                        (key, encoded(value).decode()))

    def seed(self, baseline, manifest):
        old = self.get('metadata', 'manifest')
        if old is not None and old != manifest:
            raise ValueError('IMMUTABLE_MANIFEST_CHANGED')
        with self.db:
            self.put('metadata', 'manifest', manifest)
            for key, row in baseline.rows.items():
                self.put('profiles', key, row)

    def finish_jobs(self, subject, section, field, status):
        for key, body in self.db.execute('SELECT id,body FROM jobs').fetchall():
            task = json.loads(body)
            if (task['subject'], task['section'], task['field']) == (subject, section, field):
                task['status'] = status
                self.db.execute('UPDATE jobs SET body=? WHERE id=?', (encoded(task).decode(), key))

    def apply(self, change, observation):
        """Caller transaction covers observation, proposal and copied field write."""
        row = self.get('profiles', change.subject)
        current = row.get('fields', {}).get(change.section, {}).get(change.field)
        if digest(current) != change.expected_old_hash:
            raise ValueError('EXPECTED_OLD_MISMATCH')
        if change.status != 'ACCEPTED_PRIVATE':
            return False
        if row.get('qa_status') in {'HOLD', 'REJECT'} or row.get('excluded'):
            raise ValueError('RECORD_GATE')
        if change.section + '.' + change.field in row.get('blocked_fields', []):
            raise ValueError('FIELD_GATE')
        if change.operation != 'fill' or current not in (None, '', [], {}) or change.incoming in (None, '', [], {}):
            raise ValueError('ONLY_EMPTY_FILL_ADMITTED')
        if observation.subject != change.subject or change.evidence_ids != (observation.key,):
            raise ValueError('OBSERVATION_CHANGE_MISMATCH')
        # Preserve source attribution/modality as part of each accepted value.
        row.setdefault('fields', {}).setdefault(change.section, {})[change.field] = change.incoming
        row.setdefault('evidence', {})[change.section + '.' + change.field] = {
            'observation': observation.key, 'source_type': observation.source_type,
            'modality': observation.modality, 'independent_confirmation': False,
            'units': observation.units, 'currency': observation.currency, 'period': observation.period,
        }
        # qa_status, public_release, flags and identity are never promoted.
        self.db.execute('UPDATE profiles SET body=? WHERE id=?', (encoded(row).decode(), change.subject))
        return True
