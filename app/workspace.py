"""SQLite transactions own migrations, replay records and optimistic versions."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3

from .errors import APIError, invalid_body, not_found


def encode(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            .replace('&', r'\u0026').replace('<', r'\u003c').replace('>', r'\u003e')
            .replace('\u2028', r'\u2028').replace('\u2029', r'\u2029'))


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class Workspace:
    def __init__(self, path: Path):
        self.path = path.resolve()

    def connect(self):
        connection = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True,
                                     timeout=5, isolation_level=None, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        return connection

    def initialize(self):
        with closing(self.connect()) as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            app_id = db.execute('PRAGMA application_id').fetchone()[0]
            if version not in (1, 2) or app_id != 1414676822:
                raise RuntimeError('unsupported catalog schema')
            if version == 1:
                try:
                    db.executescript('BEGIN IMMEDIATE;\n' + Path(__file__).with_name('migration.sql').read_text() + '\nCOMMIT;')
                except BaseException:
                    if db.in_transaction:
                        db.rollback()
                    raise

    @contextmanager
    def transaction(self):
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def apply(self, scope, key, payload, command):
        digest = hashlib.sha256(encode(payload).encode()).hexdigest()
        with self.transaction() as db:
            if key:
                row = db.execute('SELECT * FROM idempotency_keys WHERE scope=? AND key=?', (scope, key)).fetchone()
                if row:
                    if row['request_hash'] != digest:
                        raise APIError(409, 'idempotency_conflict', 'idempotency key was already used with a different request')
                    return row['status'], json.loads(row['data']), row['location'], True
            data, location = command(db)
            status = 201 if key else 200
            if key:
                db.execute('INSERT INTO idempotency_keys VALUES(?,?,?,?,?,?)',
                           (scope, key, digest, status, encode(data), location))
            return status, data, location, False

    @staticmethod
    def document_row(row):
        if row is None:
            not_found('document')
        value = dict(row)
        value['content'] = json.loads(value['content'])
        return value

    def documents(self, kind):
        with closing(self.connect()) as db:
            return [self.document_row(row) for row in db.execute(
                'SELECT * FROM documents WHERE kind=? ORDER BY created_at,id', (kind,))]

    def document(self, kind, id):
        with closing(self.connect()) as db:
            return self.document_row(db.execute('SELECT * FROM documents WHERE kind=? AND id=?', (kind, id)).fetchone())

    @staticmethod
    def products_exist(db, ids, field):
        for id in ids:
            if not db.execute('SELECT EXISTS(SELECT 1 FROM products WHERE id=?)', (id,)).fetchone()[0]:
                invalid_body(field, 'existing_products')

    def save_document(self, db, kind, id, content, ids, version):
        now = timestamp()
        if id:
            old = self.document_row(db.execute('SELECT * FROM documents WHERE kind=? AND id=?', (kind, id)).fetchone())
            if old['content'] == content:
                return old
            if old['version'] != version:
                raise APIError(409, 'version_conflict', 'resource changed; fetch its latest version before replacing it')
            created = old['created_at']
            version = old['version'] + 1
        else:
            id, created, version = kind + '-' + secrets.token_hex(13), now, 1
        self.products_exist(db, ids, 'items.product_id' if kind == 'trip' else 'product_ids')
        db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                   'content=excluded.content,version=excluded.version,updated_at=excluded.updated_at',
                   (id, kind, encode(content), version, created, now))
        db.execute('DELETE FROM document_products WHERE document_id=?', (id,))
        db.executemany('INSERT INTO document_products VALUES(?,?,?)', [(id, pid, i) for i, pid in enumerate(ids)])
        return {'id': id, 'kind': kind, 'content': content, 'version': version, 'created_at': created, 'updated_at': now}

    @staticmethod
    def review_row(row):
        if row is None:
            not_found('review')
        return dict(row)

    REVIEW_SELECT = ('SELECT r.id,r.product_id,r.author,r.rating,r.content,r.travel_date,r.created_at,m.version '
                     'FROM reviews r JOIN managed_reviews m ON m.review_id=r.id')

    def review(self, product_id, id):
        with closing(self.connect()) as db:
            return self.review_row(db.execute(self.REVIEW_SELECT + ' WHERE r.product_id=? AND r.id=?', (product_id, id)).fetchone())

    def save_review(self, db, product_id, id, content, version):
        if id:
            old = self.review_row(db.execute(self.REVIEW_SELECT + ' WHERE r.product_id=? AND r.id=?', (product_id, id)).fetchone())
            if all(old[key] == value for key, value in content.items()):
                return old
            if old['version'] != version:
                raise APIError(409, 'version_conflict', 'resource changed; fetch its latest version before replacing it')
            version, created = old['version'] + 1, old['created_at']
            db.execute('UPDATE reviews SET author=?,rating=?,content=?,travel_date=? WHERE id=?',
                       (content['author'], content['rating'], content['content'], content['travel_date'], id))
            db.execute('UPDATE managed_reviews SET version=? WHERE review_id=?', (version, id))
        else:
            if not db.execute('SELECT EXISTS(SELECT 1 FROM products WHERE id=?)', (product_id,)).fetchone()[0]:
                not_found('product')
            id, created, version = 'review-' + secrets.token_hex(13), datetime.now(timezone.utc).date().isoformat(), 1
            db.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,(SELECT coalesce(max(position),-1)+1 FROM reviews))',
                       (id, product_id, content['author'], content['rating'], content['content'], content['travel_date'], created))
            db.execute('INSERT INTO managed_reviews VALUES(?,?)', (id, version))
        return {'id': id, 'product_id': product_id, **content, 'created_at': created, 'version': version}
