"""Web Push (RFC 8030/8291/8292) without third-party push libraries: payload encryption (aes128gcm) and the
VAPID signature use `cryptography`; delivery uses httpx. No account or API key is involved - the browser's push
service (Apple, Google, Mozilla) accepts any server that signs with the key pair the browser subscribed to.

The VAPID key pair is generated on first use and kept in the database, so nobody has to create or paste a
secret. The private key never leaves the server."""

from __future__ import annotations

import base64
import json
import os
import time
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from lq.db import models as m

KEY_ROW = "vapid"  # system_settings row that stores the key pair


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _pub_bytes(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> bytes:
    pub = key.public_key() if isinstance(key, ec.EllipticCurvePrivateKey) else key
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def vapid_key(session) -> ec.EllipticCurvePrivateKey:
    row = session.get(m.SystemSetting, KEY_ROW)
    if row is None or not (row.data or {}).get("private_pem"):
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        if row is None:
            row = m.SystemSetting(realm=KEY_ROW, data={})
            session.add(row)
        row.data = {"private_pem": pem.decode()}
        session.flush()
        return key
    return serialization.load_pem_private_key(row.data["private_pem"].encode(), password=None)


def public_key_b64(session) -> str:
    return b64u(_pub_bytes(vapid_key(session)))


def encrypt(payload: bytes, p256dh: str, auth: str) -> bytes:
    """RFC 8291 aes128gcm body: salt | record size | key id length | sender public key | ciphertext."""
    client_pub_bytes = unb64u(p256dh)
    client_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), client_pub_bytes)
    eph = ec.generate_private_key(ec.SECP256R1())
    eph_pub = _pub_bytes(eph)
    shared = eph.exchange(ec.ECDH(), client_pub)
    ikm = HKDF(hashes.SHA256(), 32, salt=unb64u(auth), info=b"WebPush: info\x00" + client_pub_bytes + eph_pub).derive(
        shared
    )
    salt = os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # \x02 = last (and only) record
    return salt + (4096).to_bytes(4, "big") + bytes([len(eph_pub)]) + eph_pub + ct


def vapid_header(key: ec.EllipticCurvePrivateKey, endpoint: str, subject: str, ttl_sec: int = 12 * 3600) -> str:
    u = urlsplit(endpoint)
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + ttl_sec, "sub": subject},
                             separators=(",", ":")).encode())  # fmt: skip
    signing_input = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"vapid t={header}.{claims}.{b64u(sig)}, k={b64u(_pub_bytes(key))}"


def subject() -> str:
    from lq.config import get_settings

    # an https URI of this app identifies the sender to the push service; no personal e-mail address is sent
    return get_settings().public_origin or "https://listening-quest.invalid"


def send(session, sub: m.PushSubscription, message: dict, *, ttl: int = 86400) -> str:
    """Deliver one message. Returns "ok", "gone" (subscription expired: removed) or "error"."""
    key = vapid_key(session)
    body = encrypt(json.dumps(message, ensure_ascii=False).encode(), sub.p256dh, sub.auth)
    try:
        r = httpx.post(
            sub.endpoint, content=body, timeout=15,
            headers={"Authorization": vapid_header(key, sub.endpoint, subject()), "Content-Encoding": "aes128gcm",
                     "Content-Type": "application/octet-stream", "TTL": str(ttl), "Urgency": "normal"},
        )  # fmt: skip
    except httpx.HTTPError:
        sub.fail_count = (sub.fail_count or 0) + 1
        return "error"
    if r.status_code in (404, 410):
        session.delete(sub)
        return "gone"
    if r.status_code >= 300:
        sub.fail_count = (sub.fail_count or 0) + 1
        return "error"
    sub.fail_count = 0
    return "ok"
