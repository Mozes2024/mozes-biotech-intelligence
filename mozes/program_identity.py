"""Evidence-qualified program identifiers; aliases never establish ownership."""
import re


DDL = '''CREATE TABLE IF NOT EXISTS program_alias_evidence (
 program_id TEXT NOT NULL, alias TEXT NOT NULL, alias_norm TEXT NOT NULL,
 ticker TEXT NOT NULL, relationship TEXT NOT NULL, valid_from TEXT NOT NULL,
 valid_to TEXT, source_url TEXT NOT NULL, observed_at TEXT NOT NULL,
 PRIMARY KEY(program_id,alias_norm,ticker,relationship,valid_from,source_url));
 CREATE INDEX IF NOT EXISTS idx_program_alias_lookup
 ON program_alias_evidence(alias_norm,valid_from,valid_to);'''


def register(conn, *, program_id, alias, ticker, relationship, valid_from,
             source_url, observed_at, valid_to=None):
    from datetime import date, datetime
    from urllib.parse import urlsplit
    if not program_id or not alias.strip() or relationship not in {'developer','licensee','partner'}:
        raise ValueError('explicit program identity and relationship required')
    date.fromisoformat(valid_from)
    observed = datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
    if observed.tzinfo is None or not re.fullmatch(r'[A-Z][A-Z0-9.]{0,9}', ticker):
        raise ValueError('timezone-aware observation and canonical ticker required')
    if valid_to and date.fromisoformat(valid_to) < date.fromisoformat(valid_from):
        raise ValueError('invalid evidence validity interval')
    url = urlsplit(source_url)
    if url.scheme != 'https' or not url.hostname or url.username or url.password:
        raise ValueError('HTTPS provenance required')
    conn.executescript(DDL)
    with conn:
        conn.execute('INSERT OR IGNORE INTO program_alias_evidence VALUES(?,?,?,?,?,?,?,?,?)',
                     (program_id,alias,alias.casefold().strip(),ticker,relationship,valid_from,
                      valid_to,source_url,observed_at))


def resolve(conn, alias, *, as_of):
    """Return every supported relationship, preserving licensing ambiguity."""
    from datetime import date
    date.fromisoformat(as_of)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='program_alias_evidence'").fetchone():
        return []
    return [dict(r) for r in conn.execute(
        'SELECT * FROM program_alias_evidence WHERE alias_norm=? AND valid_from<=? '
        'AND (valid_to IS NULL OR valid_to>=?) AND substr(observed_at,1,10)<=? '
        'ORDER BY program_id,ticker,source_url', (alias.casefold().strip(),as_of,as_of,as_of))]


def mentions(conn, text, *, as_of):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='program_alias_evidence'").fetchone():
        return []
    result = []
    for row in conn.execute('SELECT DISTINCT alias FROM program_alias_evidence ORDER BY alias'):
        if re.search(r'(?<!\w)' + re.escape(row[0]) + r'(?!\w)', text, re.I):
            result.extend(resolve(conn,row[0],as_of=as_of))
    return result
