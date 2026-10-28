"""Isolated native CPU adapter. Requires an already acquired, pinned model.

Model downloads/installations are deliberately outside the inference process.
No credentials or database connection are passed to the child.
"""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from .contracts import SCHEMA, OPTIONS, encode, predictions

ROOT = Path(__file__).resolve().parents[2]
PINS = Path(__file__).with_name('native-pins.json')
SAFE_FAILURES = {'NER_RAM_PREFLIGHT', 'NER_RSS_CAP', 'NER_TIME_CAP', 'NER_NATIVE_FAILED'}


class NativeError(RuntimeError):
    def __init__(self, code):
        if code not in SAFE_FAILURES:
            raise ValueError('SAFE_NATIVE_CODE_REQUIRED')
        self.code = code
        super().__init__(code)


class Native:
    def __init__(self, python, model_dir):
        self.python = str(Path(python).absolute())
        self.model_dir = str(Path(model_dir).resolve(strict=True))

    def __call__(self, text):
        import psutil
        if psutil.virtual_memory().available < 3 * 1024**3:
            raise NativeError('NER_RAM_PREFLIGHT')
        with tempfile.TemporaryDirectory(prefix='bohun-native-') as temp:
            root = Path(temp)
            root.chmod(0o700)
            inp, out = root/'input.json', root/'output.json'
            inp.write_text(encode({'text': text})); inp.chmod(0o600)
            env = {'PATH': '/usr/bin:/bin', 'HOME': temp, 'HF_HOME': temp,
                   'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                   'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
                   'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
                   'TOKENIZERS_PARALLELISM': 'false', 'OMP_NUM_THREADS': '2',
                   'OPENBLAS_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2'}
            began = time.monotonic()
            child = subprocess.Popen([self.python, '-B', '-m', 'collectors.company_ner.native',
                '--child', self.model_dir, str(inp), str(out)], cwd=ROOT, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            process = psutil.Process(child.pid)
            try:
                while child.poll() is None:
                    try:
                        rss = sum(p.memory_info().rss for p in [process]+process.children(recursive=True))
                        if rss > 3 * 1024**3:
                            raise NativeError('NER_RSS_CAP')
                    except psutil.NoSuchProcess:
                        pass
                    if time.monotonic()-began > 180:
                        raise NativeError('NER_TIME_CAP')
                    time.sleep(0.10)
                if child.returncode or not out.is_file() or out.stat().st_size > 2 * 1024**2:
                    raise NativeError('NER_NATIVE_FAILED')
                return predictions(text, json.loads(out.read_bytes()))
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()


def child(model_dir, inp, out):
    import socket
    def no_network(*args, **kwargs):
        raise RuntimeError('NER_NETWORK_FORBIDDEN')
    socket.socket.connect = no_network
    socket.create_connection = no_network
    socket.getaddrinfo = no_network
    if not sys.platform.startswith('linux') or sys.version_info[:2] != (3, 13):
        raise RuntimeError('PINNED_LINUX_PYTHON_REQUIRED')
    pins = json.loads(PINS.read_bytes())
    for item in pins['packages']:
        if importlib.metadata.version(item['name']) != item['version']:
            raise RuntimeError('NER_DEPENDENCY_PIN')
    model_dir = Path(model_dir).resolve(strict=True)
    for item in pins['model']:
        path = (model_dir/item['path']).resolve(strict=True)
        if not path.is_relative_to(model_dir):
            raise RuntimeError('NER_MODEL_PATH')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                raise RuntimeError('NER_MODEL_PIN')
    raw = Path(inp).read_bytes()
    if len(raw) > 2*1024**2:
        raise RuntimeError('NER_INPUT_BOUND')
    text = json.loads(raw)['text']
    if not isinstance(text, str) or not 200 <= len(text.encode()) <= 131072:
        raise RuntimeError('NER_INPUT_TEXT')
    import torch
    from gliner2 import AutoExtractor
    torch.set_num_threads(2); torch.set_num_interop_threads(2)
    model = AutoExtractor.from_pretrained(str(model_dir), local_files_only=True).to('cpu').eval()
    forwards = []
    def count(module, args, kwargs):
        tensor = kwargs.get('input_ids', args[0] if args else None)
        if len(forwards) >= 128 or tensor is None or tensor.shape[-1] > model.config.max_len:
            raise RuntimeError('NER_FORWARD_BOUND')
        forwards.append(1)
    model.encoder.register_forward_pre_hook(count, with_kwargs=True)
    windows = []
    def observe(frame, event, arg):
        if event == 'return' and frame.f_code.co_name == 'split_text_into_chunks' and isinstance(arg, list):
            windows.append([(c.start_char, c.end_char) for c in arg])
    sys.setprofile(observe)
    try:
        with torch.inference_mode():
            answer = model.extract_entities_long(text, SCHEMA, include_spans=True,
                                                  include_confidence=True, **OPTIONS)
    finally:
        sys.setprofile(None)
    if len(windows) != 1:
        raise RuntimeError('NER_COVERAGE_UNKNOWN')
    covered = set()
    for a,b in windows[0]:
        if not 0 <= a < b <= len(text):
            raise RuntimeError('NER_COVERAGE_OFFSETS')
        covered.update(range(a,b))
    if any(i not in covered for i,c in enumerate(text) if not c.isspace()):
        raise RuntimeError('NER_COVERAGE_GAP')
    Path(out).write_text(encode(predictions(text, answer)))
    Path(out).chmod(0o600)


if __name__ == '__main__':
    if len(sys.argv) != 5 or sys.argv[1] != '--child':
        raise SystemExit('NATIVE_CHILD_ONLY')
    child(*sys.argv[2:])
