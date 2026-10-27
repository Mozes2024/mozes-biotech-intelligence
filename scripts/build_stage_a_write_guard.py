"""Build the pinned production health-cache bundle with issuer-write guards only.

Keep scheduler, watchdog, recovery and all other routes unchanged. An unchanged
snapshot must not deactivate/rewrite every issuer. updated_at records the last
material issuer change (including activation/deactivation), not a sync heartbeat.
The removed-issuer update and conditional upserts stay in the original atomic batch.
"""
from pathlib import Path
import importlib.util
ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('stage_a_health_builder', ROOT / 'scripts/build_stage_a_health_cache.py')
_builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_builder)

OLD_DEACTIVATE = 'const statements = [db.prepare("UPDATE edge_issuers SET active=0")];'
NEW_DEACTIVATE = '''const snapshotCiks = JSON.stringify(body.issuers.map(row => row.cik));
  const statements = [db.prepare("UPDATE edge_issuers SET active=0,updated_at=? WHERE active=1 AND cik NOT IN (SELECT value FROM json_each(?))").bind(now, snapshotCiks)];'''
OLD_UPSERT = 'source=excluded.source,active=1,updated_at=excluded.updated_at");'
NEW_UPSERT = 'source=excluded.source,active=1,updated_at=excluded.updated_at WHERE edge_issuers.ticker IS NOT excluded.ticker OR edge_issuers.company IS NOT excluded.company OR edge_issuers.confidence IS NOT excluded.confidence OR edge_issuers.source IS NOT excluded.source OR edge_issuers.active IS NOT 1");'

def build(destination: Path) -> Path:
    destination = _builder.build(Path(destination))
    path = destination / 'cloudflare/src/index.js'
    text = path.read_text()
    for old, new in ((OLD_DEACTIVATE, NEW_DEACTIVATE), (OLD_UPSERT, NEW_UPSERT)):
        if text.count(old) != 1:
            raise RuntimeError('pinned issuer SQL anchor changed')
        text = text.replace(old, new)
    path.write_text(text)
    return destination

if __name__ == '__main__':
    import sys
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/stage-a-write-guard')))
