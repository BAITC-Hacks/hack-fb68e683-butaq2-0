"""Catch native crypto crashes in a child process instead of killing pytest."""

import subprocess
import sys


def test_crypto_import_and_authenticated_encryption_in_runtime_environment():
    # Inherit the real container environment: do not hide a missing Docker fix
    # by setting OPENSSL_armcap inside the test.
    script = """
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

cipher = AESGCM(AESGCM.generate_key(bit_length=128))
nonce = bytes(12)
encrypted = cipher.encrypt(nonce, b"butaq runtime smoke", b"context")
assert cipher.decrypt(nonce, encrypted, b"context") == b"butaq runtime smoke"
try:
    cipher.decrypt(nonce, encrypted, b"wrong context")
except InvalidTag:
    pass
else:
    raise AssertionError("Authenticated encryption accepted invalid context")
"""
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Native crypto failed ({result.returncode}): {result.stderr}"
