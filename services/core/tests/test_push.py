"""Web Push: RFC 8291 encryption decrypts like a browser would, the VAPID signature verifies, and the
notification rules send each kind at most once per period. No network: delivery is replaced in tests."""

import json
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from helpers import ready_unit
from test_api import login

from lq import push
from lq.db import models as m
from lq.services import notify


def _browser_keys():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = b"0123456789abcdef"
    return priv, push.b64u(pub), push.b64u(auth)


def _browser_decrypt(body: bytes, priv, p256dh: str, auth: str) -> bytes:
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    sender = body[21 : 21 + idlen]
    ct = body[21 + idlen :]
    assert rs == 4096 and idlen == 65
    shared = priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender))
    ikm = HKDF(
        hashes.SHA256(), 32, salt=push.unb64u(auth), info=b"WebPush: info\x00" + push.unb64u(p256dh) + sender
    ).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, ct, None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def test_payload_decrypts_like_a_browser_and_vapid_signature_verifies(db):
    priv, p256dh, auth = _browser_keys()
    msg = {"title": "テスト", "body": "届きました"}
    body = push.encrypt(json.dumps(msg, ensure_ascii=False).encode(), p256dh, auth)
    assert json.loads(_browser_decrypt(body, priv, p256dh, auth)) == msg

    key = push.vapid_key(db)
    assert push.vapid_key(db).private_numbers() == key.private_numbers()  # stored once, reused
    hdr = push.vapid_header(key, "https://web.push.apple.com/abc", "https://app.example")
    token = hdr.split("t=")[1].split(",")[0]
    h, c, s = token.split(".")
    claims = json.loads(push.unb64u(c))
    assert (
        claims["aud"] == "https://web.push.apple.com"
        and claims["sub"] == "https://app.example"
        and "@" not in claims["sub"]
    )
    sig = push.unb64u(s)
    key.public_key().verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
                            f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))  # fmt: skip
    assert hdr.endswith("k=" + push.public_key_b64(db))


def test_subscribe_test_message_and_expired_devices(db, user, monkeypatch):
    sent = []

    class R:
        def __init__(self, code):
            self.status_code = code

    monkeypatch.setattr(push.httpx, "post", lambda url, **k: (sent.append((url, k)), R(201 if "ok" in url else 410))[1])
    _, p256dh, auth = _browser_keys()
    c = login()
    assert c.get("/api/push/key").json()["devices"] == 0
    for ep in ("https://push.example/ok", "https://push.example/expired"):
        assert c.post("/api/push/subscriptions", json={"endpoint": ep, "p256dh": p256dh, "auth": auth}).json()[
            "subscribed"
        ]
    assert (
        c.post("/api/push/subscriptions", json={"endpoint": "http://plain", "p256dh": p256dh, "auth": auth}).status_code
        == 422
    )
    r = c.post("/api/push/test").json()
    assert r == {"ok": 1, "gone": 1, "error": 0}
    assert sent[0][1]["headers"]["Content-Encoding"] == "aes128gcm" and sent[0][1]["headers"][
        "Authorization"
    ].startswith("vapid t=")
    assert c.get("/api/push/key").json()["devices"] == 1  # the expired one was removed


def test_rules_send_each_kind_once_per_period(db, user, monkeypatch):
    got = []
    monkeypatch.setattr(push, "send", lambda session, sub, msg, **k: (got.append(msg["title"]), "ok")[1])
    db.add(m.PushSubscription(user_id=user.id, endpoint="https://push.example/1", p256dh="x" * 20, auth="y" * 8))
    user.settings = {**(user.settings or {}), "notify_reminder_hour": 20, "timezone": "Asia/Tokyo"}
    at_20 = datetime(2026, 11, 14, 11, 5, tzinfo=UTC)  # 20:05 in Tokyo
    u = ready_unit(db, seed="n1")
    u.ready_at = at_20 - timedelta(minutes=30)  # fixed, not the wall clock: the test must not depend on the time of day
    db.commit()
    assert sorted(notify.run_for_user(db, user, at_20)) == ["material", "reminder"]
    db.commit()
    assert notify.run_for_user(db, user, at_20 + timedelta(minutes=10)) == []  # once per day each
    # problems: a failed job, then quiet for 6 hours
    db.add(
        m.GenerationJob(
            realm=user.realm, kind="generate", params={}, status="FAILED", updated_at=at_20 + timedelta(minutes=20)
        )
    )
    db.commit()
    assert notify.run_for_user(db, user, at_20 + timedelta(minutes=30)) == ["problem"]
    db.commit()
    db.add(
        m.GenerationJob(
            realm=user.realm, kind="generate", params={}, status="FAILED", updated_at=at_20 + timedelta(minutes=40)
        )
    )
    db.commit()
    assert notify.run_for_user(db, user, at_20 + timedelta(hours=1)) == []
    # switched off
    user.settings = {**user.settings, "notify_problems": False, "notify_reminder_hour": None}
    db.commit()
    assert notify.run_for_user(db, user, at_20 + timedelta(hours=8)) == []
