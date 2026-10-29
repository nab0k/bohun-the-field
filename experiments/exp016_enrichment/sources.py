"""Saved/synthetic input and hash-guarded reuse; no business HTTP entry point."""
import ast
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
from contracts import SourceReceipt, encoded, iso

FIELD_ROOT = Path(__file__).resolve().parents[2]
FIELD_REF = 'defa9c291d71da81b3363309e1d2be547675b1ce'
REUSE = ('collectors.companies.sources.edf', 'collectors.companies.sources.http',
         'collectors.web_monitor.content', 'collectors.company_ner.linker',
         'collectors.companies.taxonomy')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def project_manifest(root=FIELD_ROOT, ref=FIELD_REF):
    """Resolve the entire project import closure statically, before any import."""
    root = Path(root).resolve()
    if root != FIELD_ROOT.resolve() or ref != FIELD_REF:
        raise ValueError('EXACT_FIELD_ROOT_REQUIRED')
    for name in REUSE:
        base = root.joinpath(*name.split('.'))
        if not base.with_suffix('.py').is_file() and not (base / '__init__.py').is_file():
            raise ValueError('REUSE_MODULE_MISSING:' + name)
    pending, checked = list(REUSE), {}
    while pending:
        mod = pending.pop()
        rel = Path(*mod.split('.'))
        path = root / rel.with_suffix('.py')
        if not path.exists():
            path = root / rel / '__init__.py'
        if not path.exists():  # namespace package
            continue
        relative = str(path.relative_to(root))
        if relative in checked:
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('REUSE_PATH_ESCAPE')
        raw = path.read_bytes()
        remote = subprocess.run(['git', '-C', str(root), 'show', f'{ref}:{relative}'],
                                check=True, capture_output=True).stdout
        if raw != remote:
            raise ValueError('REUSE_VERSION_MISMATCH:' + relative)
        checked[relative] = sha(raw)
        for n in ast.walk(ast.parse(raw)):
            names = []
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                name = n.module or ''
                if n.level:
                    package = mod if path.name == '__init__.py' else mod.rpartition('.')[0]
                    name = importlib.util.resolve_name('.' * n.level + name, package)
                names = [name] + [name + '.' + a.name for a in n.names]
            pending.extend(n for n in names if n.startswith('collectors.'))
        parts = mod.split('.')
        pending.extend('.'.join(parts[:i]) for i in range(1, len(parts)))
    return {'root': str(root), 'reference': ref, 'files': dict(sorted(checked.items()))}


def reuse_module(name, manifest):
    if name not in REUSE:
        raise ValueError('MODULE_NOT_ALLOWED')
    if manifest != project_manifest():
        raise ValueError('IMPORT_CLOSURE_CHANGED')
    root = str(FIELD_ROOT.resolve())
    # Reject an already-loaded module from another checkout as well.
    for key, module in tuple(sys.modules.items()):
        if key == 'collectors' or key.startswith('collectors.'):
            paths = list(getattr(module, '__path__', ()))
            if getattr(module, '__file__', None):
                paths.append(module.__file__)
            if any(not Path(p).resolve().is_relative_to(Path(root)) for p in paths):
                raise ValueError('FOREIGN_COLLECTORS_IMPORT')
    if root not in sys.path:
        sys.path.insert(0, root)
    module = importlib.import_module(name)
    if not Path(module.__file__).resolve().is_relative_to(Path(root)):
        raise ValueError('IMPORTED_PATH_MISMATCH')
    return module


def receipt(source, allowed):
    if source['source_id'] not in allowed:
        raise ValueError('SOURCE_NOT_ALLOWED')
    if source.get('synthetic') is not True:
        raise ValueError('LIVE_INPUT_NOT_ADMITTED')
    parts = urlsplit(source['url'])
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('SOURCE_URL')
    if not source.get('rights_reference'):
        raise ValueError('RIGHTS_REFERENCE_REQUIRED')
    iso(source['received_at'])
    iso(source['effective_at'])
    raw = encoded(source['raw']) if source['format'] == 'json' else source['raw'].encode()
    text = source.get('text', '')
    return SourceReceipt(source['source_id'], source['url'], source['publisher'],
                         source['dataset'], source['received_at'], source['effective_at'],
                         sha(raw), sha(text.encode()), source['format'], source['version'],
                         source['rights_reference']), len(raw), len(text.encode())


def json_field(document, path):
    if not path.startswith('$.') or any(p in path for p in ('[', ']', '*')):
        raise ValueError('EXPLICIT_JSON_FIELD_REQUIRED')
    value = document
    for part in path[2:].split('.'):
        if not part or not isinstance(value, dict) or part not in value:
            raise ValueError('SOURCE_FIELD_MISSING')
        value = value[part]
    return value


def saved_ner_candidates(data):
    """Already-computed candidates remain suggestions; no NER/model recomputation."""
    if not isinstance(data, list):
        raise ValueError('SAVED_CANDIDATES_LIST_REQUIRED')
    return [dict(r, identity_status='SUGGESTION') for r in data]
