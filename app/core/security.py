import os
import json
import base64
import hmac
import hashlib
import time
from typing import Dict, Any, Optional
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import jwt
from app.core.config import settings


def get_aes_key() -> bytes:
    key_str = settings.JUMP_TICKET_AES_KEY
    key_bytes = key_str.encode("utf-8")
    if len(key_bytes) < 32:
        key_bytes = key_bytes.ljust(32, b"0")
    return key_bytes[:32]


def encrypt_ticket_payload(payload: Dict[str, Any]) -> str:
    """Encrypt payload using AES-256-CBC, prepending 16-byte IV, returning urlsafe base64"""
    key = get_aes_key()
    iv = os.urandom(16)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()

    raw_data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    # PKCS7 padding
    pad_len = 16 - (len(raw_data) % 16)
    padded_data = raw_data + bytes([pad_len] * pad_len)

    ciphertext = encryptor.update(padded_data) + encryptor.finalize()
    combined = iv + ciphertext
    return base64.urlsafe_b64encode(combined).decode("utf-8").rstrip("=")


def decrypt_ticket_payload(ticket_token: str) -> Optional[Dict[str, Any]]:
    """Decrypt payload from urlsafe base64 string"""
    try:
        key = get_aes_key()
        # Fix padding for urlsafe base64
        padded_token = ticket_token + "=" * ((4 - len(ticket_token) % 4) % 4)
        raw_combined = base64.urlsafe_b64decode(padded_token.encode("utf-8"))
        if len(raw_combined) < 17:
            return None

        iv = raw_combined[:16]
        ciphertext = raw_combined[16:]

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        padded_data = decryptor.update(ciphertext) + decryptor.finalize()

        # Unpad PKCS7
        pad_len = padded_data[-1]
        if pad_len > 16:
            return None
        raw_data = padded_data[:-pad_len]
        return json.loads(raw_data.decode("utf-8"))
    except Exception:
        return None


def generate_gateway_signature(
    headers: Dict[str, str],
    secret: Optional[str] = None
) -> str:
    """
    Generate gateway HMAC-SHA256 signature according to detailed design section 2.3.2.
    Format: HMAC_SHA256(tenant_id + "\n" + user_id + "\n" + timestamp + "\n" + nonce, secret)
    """
    signing_secret = (secret or settings.GATEWAY_HMAC_SECRET).encode("utf-8")
    tenant_id = headers.get("X-Portal-Tenant-Id", "")
    user_id = headers.get("X-Portal-User-Id", "")
    timestamp = headers.get("X-Gateway-Timestamp", "")
    nonce = headers.get("X-Gateway-Nonce", "")

    string_to_sign = f"{tenant_id}\n{user_id}\n{timestamp}\n{nonce}".encode("utf-8")
    signature = hmac.new(signing_secret, string_to_sign, hashlib.sha256).hexdigest()
    return signature


def generate_gateway_headers(
    tenant_id: str,
    user_id: str,
    master_user_id: str,
    workspace_id: str,
    role_code: str,
    shadow_account: str = "",
    secret: Optional[str] = None,
) -> Dict[str, str]:
    """
    Generate complete 9 gateway trusted headers with HMAC-SHA256 signature.
    """
    timestamp = str(int(time.time()))
    nonce = base64.b64encode(os.urandom(12)).decode("utf-8")

    headers = {
        "X-Portal-Tenant-Id": tenant_id,
        "X-Portal-User-Id": user_id,
        "X-Portal-Master-Uid": master_user_id,
        "X-Portal-Workspace-Id": workspace_id,
        "X-Portal-Role-Code": role_code,
        "X-Shadow-Account": shadow_account,
        "X-Gateway-Timestamp": timestamp,
        "X-Gateway-Nonce": nonce,
    }
    headers["X-Gateway-Signature"] = generate_gateway_signature(headers, secret)
    return headers


def verify_gateway_signature(
    headers: Dict[str, str],
    secret: Optional[str] = None,
    max_clock_skew: int = 300,
) -> bool:
    """
    Verify gateway HMAC-SHA256 signature and prevent clock skew/replay.
    """
    try:
        timestamp_str = headers.get("X-Gateway-Timestamp", "")
        if not timestamp_str:
            return False
        timestamp = int(timestamp_str)
        now = int(time.time())
        if abs(now - timestamp) > max_clock_skew:
            return False

        expected_sig = headers.get("X-Gateway-Signature", "")
        if not expected_sig:
            return False

        calculated_sig = generate_gateway_signature(headers, secret)
        return hmac.compare_digest(calculated_sig, expected_sig)
    except Exception:
        return False


def hash_secret_key(secret_key: str, salt: str) -> str:
    """加盐哈希（优先SHA256/SM3）"""
    salted = f"{secret_key}:{salt}".encode("utf-8")
    return hashlib.sha256(salted).hexdigest()


def create_internal_jwt(payload: Dict[str, Any], expires_in: int = 86400) -> str:
    """Issue internal JWT token for iframe frontend authentication"""
    claims = payload.copy()
    now = int(time.time())
    claims["iat"] = now
    claims["exp"] = now + expires_in
    key = settings.INTERNAL_JWT_SECRET.encode("utf-8")
    if len(key) < 32:
        key = key.ljust(32, b"0")
    return jwt.encode(claims, key, algorithm="HS256")


def verify_internal_jwt(token: str) -> Optional[Dict[str, Any]]:
    """Verify internal JWT token"""
    try:
        key = settings.INTERNAL_JWT_SECRET.encode("utf-8")
        if len(key) < 32:
            key = key.ljust(32, b"0")
        payload = jwt.decode(token, key, algorithms=["HS256"])
        return payload
    except Exception:
        return None
