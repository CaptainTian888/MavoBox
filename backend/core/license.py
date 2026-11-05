"""Offline license codes (Ed25519).

The teacher signs a small JSON payload with a private key that never leaves their machine;
the app embeds only the public key and verifies codes offline. A code can be bound to one
machine (student sends their machine code) or be '*' (any machine), and may expire.

Honest limits: this keeps casual sharing out, it is not tamper-proof DRM — anyone able to
patch the executable can bypass it.
"""
import base64
import datetime as dt
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PREFIX = 'MAVO1'


class LicenseError(ValueError):
    pass


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def _raw_machine_id() -> str:
    try:
        if sys.platform == 'win32':
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                return str(winreg.QueryValueEx(key, 'MachineGuid')[0])
        if sys.platform == 'darwin':
            out = subprocess.run(['ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'],
                                 capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                if 'IOPlatformUUID' in line:
                    return line.split('"')[-2]
    except Exception:
        pass
    return str(uuid.getnode())


def machine_code() -> str:
    digest = hashlib.sha256(('mavobox|' + _raw_machine_id()).encode()).hexdigest().upper()[:20]
    return '-'.join(digest[i:i + 5] for i in range(0, 20, 5))


def sign(payload: dict, private_key: Ed25519PrivateKey) -> str:
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return f'{PREFIX}.{b64e(body)}.{b64e(private_key.sign(body))}'


def verify(token: str, public_key_b64: str, machine: str, today: dt.date | None = None) -> dict:
    parts = (token or '').strip().split('.')
    if len(parts) != 3 or parts[0] != PREFIX:
        raise LicenseError('授权码格式不正确')
    try:
        body, sig = b64d(parts[1]), b64d(parts[2])
        Ed25519PublicKey.from_public_bytes(b64d(public_key_b64)).verify(sig, body)
        payload = json.loads(body)
    except (InvalidSignature, ValueError, TypeError):
        raise LicenseError('授权码无效或已被篡改') from None
    bound = payload.get('machine', '*')
    if bound != '*' and bound != machine:
        raise LicenseError('该授权码绑定了其他电脑')
    expires = payload.get('exp')
    if expires:
        try:
            last_day = dt.date.fromisoformat(expires)
        except (TypeError, ValueError):
            raise LicenseError('授权码有效期字段无效') from None
        if (today or dt.date.today()) > last_day:
            raise LicenseError('授权码已过期，请联系老师续期')
    return payload


def generate_keypair():
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    public = b64e(private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    return pem, public


def load_private(pem: bytes) -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(pem, password=None)


class LicenseStore:
    def __init__(self, data_dir, public_key_b64, machine=None):
        self.path = Path(data_dir) / 'license.key'
        self.public_key = public_key_b64
        self.machine = machine or machine_code()

    def status(self, today=None) -> dict:
        base = dict(machine_code=self.machine, licensed=False, name=None, expires=None, message='尚未激活')
        try:
            token = self.path.read_text(encoding='utf-8').strip()
        except OSError:
            return base
        try:
            payload = verify(token, self.public_key, self.machine, today)
        except LicenseError as exc:
            return dict(base, message=str(exc))
        return dict(base, licensed=True, name=payload.get('name'), expires=payload.get('exp'), message='已激活')

    def activate(self, token, today=None) -> dict:
        verify(token, self.public_key, self.machine, today)  # raises LicenseError
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(token.strip(), encoding='utf-8')
        return self.status(today)
