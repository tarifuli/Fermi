"""Database adapter for Fermi's existing parameterized SQL queries.

PostgreSQL requires DATABASE_URL and a separately applied schema migration.
SQLite remains available for local development and existing tests.
"""
import os
import re
import sqlite3


class HybridRow(dict):
    """Support both row['column'] and row[0], as sqlite3.Row does."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _row_factory(cursor):
    return HybridRow(zip((col.name for col in cursor.description), cursor.fetchone()))


class PostgresCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = None

    def fetchone(self):
        row = self.cursor.fetchone()
        return HybridRow(row) if row is not None else None

    def fetchall(self):
        return [HybridRow(row) for row in self.cursor.fetchall()]


class PostgresConnection:
    def __init__(self, url):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(url, row_factory=dict_row)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self.conn.rollback()
            else:
                self.conn.commit()
        finally:
            self.conn.close()

    def execute(self, sql, params=()):
        # SQL strings in this application use positional SQLite placeholders.
        # All current statements use bound parameters, not literal '?' operators.
        if sql.strip().upper() == 'SELECT LAST_INSERT_ROWID()':
            sql = 'SELECT lastval()'
        else:
            sql = sql.replace('?', '%s')
        cursor = self.conn.execute(sql, params)
        return PostgresCursor(cursor)

    def executemany(self, sql, params):
        with self.conn.cursor() as cursor:
            cursor.executemany(sql.replace('?', '%s'), params)


def get_db():
    url = os.environ.get('DATABASE_URL')
    if url:
        if not url.startswith(('postgresql://', 'postgres://')):
            raise ValueError('DATABASE_URL must be a PostgreSQL connection URL')
        return PostgresConnection(url)
    if os.environ.get('VERCEL'):
        raise RuntimeError('DATABASE_URL is required for Vercel deployment')
    conn = sqlite3.connect(os.environ.get('DATABASE_PATH', 'fermi.db'))
    conn.row_factory = sqlite3.Row
    return conn
