"""Inert private comparison report, not a website export."""
from dataclasses import asdict, is_dataclass
import html
import json
import os
from pathlib import Path
from evidence import private_projection


def private_write(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('OUTPUT_SYMLINK')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(value)


def report_data(result):
    data = asdict(result) if is_dataclass(result) else json.loads(json.dumps(result))
    for key in ('before', 'after'):
        data[key] = {k: private_projection(v) for k, v in data[key].items()}
    # Receipts are private, but a UA location must not leak via observation/value tables.
    ua = {k for k, r in result.after.items()
          if r.get('fields', {}).get('geography', {}).get('country') == 'UA'}
    for key in ('observations', 'changes'):
        for row in data[key]:
            if row.get('subject') in ua and row.get('section') == 'geography' and row.get('field') != 'country':
                for field in ('value', 'incoming', 'current', 'baseline', 'locator'):
                    if field in row:
                        row[field] = '[REDACTED_UA_LOCATION]'
    return data


def comparison_html(data):
    def e(value):
        return html.escape(json.dumps(value, ensure_ascii=False, indent=2))
    rows = []
    for key, after in data['after'].items():
        before = data['before'].get(key, {'state': 'not in synthetic baseline'})
        rows.append(f'<tr><th>{e(key)}</th><td><pre>{e(before)}</pre></td><td><pre>{e(after)}</pre></td></tr>')
    return ('<!doctype html><html lang="ru"><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>EXP-016 — синтетическая локальная проверка</title>'
            '<style>body{font:16px system-ui;margin:2rem;background:#f5f4f0;color:#17232d}'
            'table{border-collapse:collapse;width:100%;table-layout:fixed}td,th{border:1px solid #bbb;padding:1rem;vertical-align:top}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px monospace}</style>'
            '<h1>Синтетическая локальная проверка</h1><p>Это не реальные 30+10 компаний. '
            'Принятие в приватную копию не означает проверенный факт или разрешение публикации.</p>'
            f'<pre>{e({k:data[k] for k in ("status", "counts", "limitations", "native_calls")})}</pre>'
            '<h2>До и после</h2><table><tr><th>Запись</th><th>До</th><th>После</th></tr>'
            + ''.join(rows) + '</table><h2>Доказательства и решения</h2>'
            f'<pre>{e(data["observations"])}</pre><pre>{e(data["changes"])}</pre>'
            f'<h2>Неизвестное и удержанное</h2><pre>{e(data["unknowns"])}</pre><pre>{e(data["holds"])}</pre></html>')


def write_report(result, directory):
    directory = Path(directory)
    data = report_data(result)
    private_write(directory / 'report.json', json.dumps(data, ensure_ascii=False, indent=2))
    private_write(directory / 'report.html', comparison_html(data))
    return data
