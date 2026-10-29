"""Future GitHub-hosted Linux bootstrap. Never execute this helper on the Mac.

Only PyICU may be source-built. No keys, service clients, system package manager,
cache, artifact uploader, model transport or fallback versions are supported.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import tomllib
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REPOSITORY = 'nab0k/bohun-the-field'
PYTHON = '3.13.17'
PINS = {'followthemoney': '4.11.2', 'nomenklatura': '4.17.3',
        'trafilatura': '2.3.1', 'langextract': '1.7.1', 'pyicu': '2.17'}
PYICU_HASH = '6138b95ac9592fa3983bf489e0c625e8fcfffd0c562a36a08269cecc4ffb48be'
SETUPTOOLS_HASH = '062d34222ad13e0cc312a4c02d73f059e86a4acbfbdea8f8f76b28c99f306922'
MAX_REPORT = 5 * 1024**2
DENIED_KEYS = ('SUPABASE_DB_PASSWORD', 'SUPABASE_SERVICE_ROLE_KEY', 'NOTION_TOKEN',
               'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'CLOUDFLARE_API_TOKEN')


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, data):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('OUTPUT_SYMLINK')
    tmp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
    os.replace(tmp, path)


def read_json(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > MAX_REPORT:
        raise ValueError('INVALID_JSON_FILE')
    return json.loads(path.read_text())


def context():
    """Admission before creating files, spawning processes or downloading anything."""
    if (sys.platform != 'linux' or platform.machine() not in {'x86_64', 'AMD64'}
            or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or os.environ.get('GITHUB_REPOSITORY') != REPOSITORY
            or os.environ.get('GITHUB_REF') != 'refs/heads/main'):
        raise ValueError('GITHUB_PUBLIC_LINUX_MANUAL_ONLY_NO_MAC')
    if platform.python_version() != PYTHON:
        raise ValueError('EXACT_CPYTHON_REQUIRED')
    if os.environ.get('NATIVE_CONFIRMATION') != 'SYNTHETIC_NATIVE_ONLY':
        raise ValueError('NATIVE_CONFIRMATION_REQUIRED')
    expected = os.environ.get('EXPECTED_SHA', '')
    if not re.fullmatch(r'[0-9a-f]{40}', expected):
        raise ValueError('FULL_EXPECTED_SHA_REQUIRED')
    if os.environ.get('GITHUB_WORKFLOW_SHA') != expected:
        raise ValueError('WORKFLOW_SOURCE_SHA_MISMATCH')
    event = read_json(Path(os.environ['GITHUB_EVENT_PATH']))
    if event.get('repository', {}).get('private') is not False:
        raise ValueError('PUBLIC_REPOSITORY_REQUIRED')
    if any(os.environ.get(k) for k in DENIED_KEYS):
        raise ValueError('SERVICE_SECRETS_NOT_ALLOWED')
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    if ROOT.resolve() != workspace or HERE.resolve() != workspace / 'experiments/exp016_enrichment':
        raise ValueError('EXACT_CHECKOUT_REQUIRED')
    actual = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                            check=True, capture_output=True, text=True).stdout.strip()
    if actual != expected:
        raise ValueError('CHECKED_OUT_SHA_MISMATCH')
    run_id, attempt = os.environ.get('GITHUB_RUN_ID', ''), os.environ.get('GITHUB_RUN_ATTEMPT', '')
    if not run_id.isdecimal() or not attempt.isdecimal():
        raise ValueError('RUN_ID_REQUIRED')
    base = Path(os.environ['RUNNER_TEMP']).resolve()
    return {'sha': actual, 'run_id': run_id, 'attempt': attempt,
            'work': base / ('exp016-native-' + run_id + '-' + attempt)}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('PACKAGE_REDIRECT_NOT_ALLOWED')


def public_url(url):
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname not in {'pypi.org', 'files.pythonhosted.org'}
            or p.username or p.password or p.port not in (None, 443) or p.fragment):
        raise ValueError('PUBLIC_PYPI_HTTPS_REQUIRED')
    return p


def download(url, limit, expected_hash=None):
    public_url(url)
    with build_opener(NoRedirect()).open(url, timeout=30) as response:
        if response.geturl() != url or response.status != 200:
            raise ValueError('PACKAGE_RESPONSE_NOT_EXACT')
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('PACKAGE_BYTES_EXCEEDED')
    if expected_hash is not None and sha(raw) != expected_hash:
        raise ValueError('PACKAGE_HASH_MISMATCH')
    return raw


def checked_file_metadata(project, version, filename, expected_hash, package_type):
    data = json.loads(download(f'https://pypi.org/pypi/{project}/{version}/json', 2 * 1024**2))
    matches = [f for f in data.get('urls', []) if f.get('filename') == filename
               and f.get('packagetype') == package_type and not f.get('yanked')]
    if len(matches) != 1 or matches[0].get('digests', {}).get('sha256') != expected_hash:
        raise ValueError('PINNED_RELEASE_FILE_MISMATCH')
    public_url(matches[0]['url'])
    return matches[0]['url']


def inspect_build_requirements(source):
    """Read a hash-verified archive without extracting or executing it."""
    with tarfile.open(source, 'r:gz') as archive:
        members = archive.getmembers()
        if len(members) > 1000 or sum(m.size for m in members) > 20 * 1024**2:
            raise ValueError('SOURCE_ARCHIVE_LIMIT')
        for m in members:
            parts = Path(m.name).parts
            if Path(m.name).is_absolute() or '..' in parts or m.issym() or m.islnk():
                raise ValueError('SOURCE_ARCHIVE_PATH')
        projects = [m for m in members if len(Path(m.name).parts) == 2
                    and Path(m.name).name == 'pyproject.toml' and m.isfile()]
        if not projects:
            return {'build-backend': 'setuptools.build_meta:__legacy__', 'requires': ['setuptools']}
        if len(projects) != 1 or projects[0].size > 128 * 1024:
            raise ValueError('BUILD_METADATA_LIMIT')
        data = tomllib.loads(archive.extractfile(projects[0]).read().decode())
    build = data.get('build-system', {})
    if build.get('build-backend') not in {'setuptools.build_meta', 'setuptools.build_meta:__legacy__'} or build.get('backend-path'):
        raise ValueError('UNAPPROVED_BUILD_BACKEND')
    # Intentionally narrow: unfamiliar requirements require review, not installation.
    allowed = {'setuptools', 'setuptools>=40.8.0', 'setuptools>=61',
               'setuptools>=61.0', 'setuptools>=61.0.0'}
    requires = build.get('requires', [])
    if not requires or any(r.replace(' ', '') not in allowed for r in requires):
        raise ValueError('UNAPPROVED_ADDITIONAL_BUILD_REQUIREMENTS')
    return build


def command(args, directory, label, timeout=300, *, offline_build=False):
    """Fixed argument vectors, one attempt; terminate the entire group on timeout."""
    if timeout > 600 or timeout <= 0 or not re.fullmatch(r'[a-z0-9-]+', label):
        raise ValueError('COMMAND_LIMIT')
    log = directory / (label + '.log')
    started = time.monotonic()
    env = dict(os.environ)
    if offline_build:
        env.update(PIP_NO_INDEX='1', PIP_NO_DEPS='1', PIP_NO_BUILD_ISOLATION='1',
                   PIP_DISABLE_PIP_VERSION_CHECK='1')
    with log.open('x') as out:
        proc = subprocess.Popen(args, cwd=HERE, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True, env=env)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise ValueError('COMMAND_TIMEOUT:' + label) from None
    if log.stat().st_size > 10 * 1024**2:
        raise ValueError('COMMAND_LOG_LIMIT:' + label)
    write_json(directory / (label + '.json'), {'argv': list(map(str, args)), 'returncode': code,
                                              'seconds': time.monotonic() - started})
    if code:
        raise ValueError('COMMAND_FAILED:' + label)


def validated_lock(report, wheel):
    installs = report.get('install', [])
    if not 5 <= len(installs) <= 150:
        raise ValueError('DEPENDENCY_COUNT_LIMIT')
    found, lines = {}, []
    wheel = Path(wheel).resolve()
    for entry in installs:
        meta, info = entry['metadata'], entry['download_info']
        name, ver = canonical(meta['name']), meta['version']
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name) or not re.fullmatch(r'[A-Za-z0-9.!+_-]+', ver):
            raise ValueError('INVALID_LOCK_TOKEN')
        if name in found or entry.get('requested_extras'):
            raise ValueError('DUPLICATE_OR_EXTRA_DEPENDENCY')
        url = info['url']
        p = urlsplit(url)
        h = info.get('archive_info', {}).get('hashes', {}).get('sha256', '')
        if not re.fullmatch(r'[0-9a-f]{64}', h) or not p.path.endswith('.whl'):
            raise ValueError('WHEEL_WITH_HASH_REQUIRED')
        if p.scheme == 'file':
            if p.netloc or Path(unquote(p.path)).resolve() != wheel or name != 'pyicu' or h != sha(wheel.read_bytes()):
                raise ValueError('UNAPPROVED_LOCAL_WHEEL')
        else:
            public_url(url)
            if name == 'pyicu':
                raise ValueError('BUILT_PYICU_WHEEL_REQUIRED')
        if name in PINS and ver != PINS[name]:
            raise ValueError('DIRECT_PIN_CHANGED')
        if name == 'setuptools' and ver != '80.9.0':
            raise ValueError('BUILD_PIN_CHANGED')
        found[name] = ver
        lines.append(f'{name}=={ver} --hash=sha256:{h}')
    if any(found.get(k) != v for k, v in PINS.items()):
        raise ValueError('DIRECT_PIN_MISSING')
    return '\n'.join(sorted(lines)) + '\n'


def prepare(ctx):
    work = ctx['work']
    if work.exists() or work.is_symlink():
        raise ValueError('NEW_WORK_DIRECTORY_REQUIRED')
    if shutil.disk_usage(work.parent).free < 2 * 1024**3:
        raise ValueError('FREE_DISK_REQUIRED')
    work.mkdir(mode=0o700)
    write_json(work / 'status.json', {'status': 'PREPARING', 'sha': ctx['sha']})
    for binary in ('g++', 'pkg-config'):
        if shutil.which(binary) is None:
            raise ValueError('IMAGE_PREREQUISITE_MISSING:' + binary)
    command(['pkg-config', '--modversion', 'icu-i18n'], work, 'icu-version')
    command(['pkg-config', '--cflags', '--libs', 'icu-i18n'], work, 'icu-flags')
    command(['g++', '--version'], work, 'compiler-version')
    write_json(work / 'image.json', {'python': platform.python_version(), 'os': platform.platform(),
                                    'image': os.environ.get('ImageVersion'), 'run_id': ctx['run_id']})
    command([sys.executable, '-m', 'venv', str(work / 'venv')], work, 'new-venv')
    py = str(work / 'venv/bin/python')
    pip = [py, '-m', 'pip', '--isolated']
    install_options = ['--no-cache-dir', '--index-url', 'https://pypi.org/simple',
                       '--only-binary=:all:', '--no-deps', '--require-hashes']
    backend_lock = work / 'backend.lock'
    backend_lock.write_text('setuptools==80.9.0 --hash=sha256:' + SETUPTOOLS_HASH + '\n')
    command(pip + ['install', *install_options, '-r', str(backend_lock)], work, 'install-backend')
    url = checked_file_metadata('pyicu', '2.17', 'pyicu-2.17.tar.gz', PYICU_HASH, 'sdist')
    source = work / 'pyicu-2.17.tar.gz'
    source.write_bytes(download(url, 1024**2, PYICU_HASH))
    write_json(work / 'build-requirements.json', inspect_build_requirements(source))
    wheelhouse = work / 'wheels'
    wheelhouse.mkdir(mode=0o700)
    command(pip + ['wheel', '--no-deps', '--no-build-isolation', '--no-index', '--no-cache-dir',
                   '--wheel-dir', str(wheelhouse), str(source)], work, 'build-pyicu', timeout=600, offline_build=True)
    wheels = list(wheelhouse.glob('*.whl'))
    if len(wheels) != 1 or not wheels[0].name.lower().startswith('pyicu-2.17-'):
        raise ValueError('EXACT_BUILT_WHEEL_REQUIRED')
    wheel = wheels[0]
    write_json(work / 'built-wheel.json', {'filename': wheel.name, 'sha256': sha(wheel.read_bytes()),
                                          'source_sha256': PYICU_HASH})
    constraints = work / 'constraints.txt'
    constraints.write_text('setuptools==80.9.0\n')
    resolver = work / 'resolve.json'
    command(pip + ['install', '--dry-run', '--ignore-installed', '--only-binary=:all:',
                   '--index-url', 'https://pypi.org/simple', '--no-cache-dir',
                   '--find-links', str(wheelhouse), '--constraint', str(constraints),
                   '--report', str(resolver), *(k + '==' + v for k, v in PINS.items())],
            work, 'resolve-linux')
    lock = work / 'resolved.lock'
    lock.write_text(validated_lock(read_json(resolver), wheel))
    if shutil.disk_usage(work).free < 1024**3:
        raise ValueError('REMAINING_DISK_REQUIRED')
    command(pip + ['install', *install_options, '--find-links', str(wheelhouse), '-r', str(lock)],
            work, 'install-resolved')
    command(pip + ['check'], work, 'pip-check')
    command(pip + ['freeze', '--all'], work, 'pip-freeze')
    write_json(work / 'status.json', {'status': 'INSTALLED_NATIVE_NOT_YET_TESTED', 'sha': ctx['sha'],
                                     'lock_sha256': sha(lock.read_bytes())})


def verify(ctx):
    work = ctx['work']
    if read_json(work / 'status.json').get('status') != 'INSTALLED_NATIVE_NOT_YET_TESTED':
        raise ValueError('PREPARED_ENVIRONMENT_REQUIRED')
    output = HERE / 'results/cloud-native' / ('run-' + ctx['run_id'] + '-' + ctx['attempt'])
    command([str(work / 'venv/bin/python'), str(HERE / 'runner.py'), 'verify-cloud',
             '--output', str(output)], work, 'strict-native-and-replay', timeout=600)
    proof = read_json(output / 'proof.json')
    if (proof.get('status') != 'PASS' or proof.get('native_tests', {}).get('passed') != 4
            or proof.get('native_tests', {}).get('skipped') != 0):
        raise ValueError('NATIVE_PROOF_REQUIRED')
    write_json(work / 'status.json', {'status': 'PASS', 'sha': ctx['sha'], 'proof': proof,
                                     'lock_sha256': sha((work / 'resolved.lock').read_bytes())})


def summary(ctx):
    work = ctx['work']
    data = read_json(work / 'status.json') if (work / 'status.json').exists() else {'status': 'NOT_RUN'}
    parts = ['## EXP-016 synthetic native smoke', '```json', json.dumps(data, indent=2), '```']
    for name in ('image.json', 'built-wheel.json', 'failure.json'):
        if (work / name).exists():
            parts += ['### ' + name, '```json', json.dumps(read_json(work / name), indent=2), '```']
    if (work / 'resolved.lock').exists():
        parts += ['### Linux dependency lock (temporary runner only)', '```text',
                  (work / 'resolved.lock').read_text(), '```']
    for name in ('icu-version.log', 'compiler-version.log', 'pip-freeze.log'):
        if (work / name).exists():
            value = (work / name).read_text()
            if len(value) > 64 * 1024:
                raise ValueError('SUMMARY_BYTES_EXCEEDED')
            parts += ['### ' + name, '```text', value, '```']
    if data.get('status') != 'PASS':
        output = HERE / 'results/cloud-native' / ('run-' + ctx['run_id'] + '-' + ctx['attempt'])
        logs = [work / name for name in ('build-pyicu.log', 'resolve-linux.log',
                'install-resolved.log', 'strict-native-and-replay.log')]
        logs += [output / name for name in ('core_replay_tests.log', 'native_tests.log')]
        for path in logs:
            if path.exists():
                with path.open('rb') as f:
                    f.seek(max(0, path.stat().st_size - 32768))
                    tail = f.read().decode('utf-8', errors='replace')
                parts += ['### Failure tail: ' + path.name, '```text', tail, '```']
    parts.append('Synthetic fixture proof only. Real company/model/business HTTP/DB/site activity: 0.')
    value = '\n'.join(parts) + '\n'
    print(value)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(value)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'verify', 'summary'))
    args = parser.parse_args()
    ctx = context()  # No file mutations/install/network before admission.
    try:
        {'prepare': prepare, 'verify': verify, 'summary': summary}[args.action](ctx)
    except Exception as exc:
        if ctx['work'].is_dir() and args.action != 'summary':
            write_json(ctx['work'] / 'failure.json', {'action': args.action,
                'error': type(exc).__name__ + ':' + str(exc), 'sha': ctx['sha'], 'status': 'FAILED'})
            write_json(ctx['work'] / 'status.json', {'status': 'FAILED', 'sha': ctx['sha']})
        raise


if __name__ == '__main__':
    main()
