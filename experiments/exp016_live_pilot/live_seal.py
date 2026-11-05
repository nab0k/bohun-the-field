"""CMS AES-256-GCM report for one certificate. The private key never leaves the owner's Mac."""
import hashlib
from pathlib import Path
import shutil
import subprocess

RESULT_CAP = 8 * 1024 * 1024


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def openssl(args, data=None, binary='openssl'):
    if shutil.which(binary) is None:
        raise RuntimeError('OPENSSL_MISSING')
    done = subprocess.run([binary, *args], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if done.returncode != 0:
        raise RuntimeError('OPENSSL_FAILED')
    return done.stdout


def seal(raw, certificate, expected_sha256, binary='openssl'):
    cert = Path(certificate).read_bytes()
    if sha(cert) != expected_sha256 or b'PRIVATE KEY' in cert or len(cert) > 8192:
        raise RuntimeError('CERTIFICATE_PIN')
    if not raw or len(raw) > RESULT_CAP:
        raise RuntimeError('RESULT_SIZE')
    return openssl(['cms', '-encrypt', '-binary', '-aes-256-gcm', '-outform', 'DER', str(certificate)], raw, binary)


def unseal(raw, key, certificate, binary='openssl'):
    if Path(key).stat().st_mode & 0o077:
        raise RuntimeError('PRIVATE_KEY_MODE')
    return openssl(['cms', '-decrypt', '-binary', '-inform', 'DER', '-inkey', str(key), '-recip', str(certificate)],
                   raw, binary)
