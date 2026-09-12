import hashlib
import hmac
import json
from pathlib import Path
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tools.tokenserver.interaction_relay_crypto import (
    MAX_STATUS_BYTES,
    REQUEST_FRAME_BYTES,
    STATUS_FRAME_BYTES,
    VERDICT_FRAME_BYTES,
    RelayRequest,
    RelayStatus,
    b64url_decode,
    b64url_encode,
    decode_device_key,
    decode_request,
    decode_status,
    decode_verdict,
    derive_keys,
    encode_request,
    encode_status,
    encode_verdict,
    request_aad,
    status_aad,
    verdict_aad,
    verdict_mac_bytes,
    verdict_mac_message,
    verify_verdict_mac,
)


ROOT = Path(__file__).resolve().parents[2]
VECTOR_PATH = ROOT / "test-vectors/interaction-relay-v1.json"
DEVICE_KEY_HEX = (
    "000102030405060708090a0b0c0d0e0f"
    "101112131415161718191a1b1c1d1e1f"
)
MAILBOX = "vp_A1b2C3d4E5f6G7h8"
REQUEST_ID = "ABEiM0RVZneImaq7zN3u_w"
CHALLENGE = bytes.fromhex(
    "202122232425262728292a2b2c2d2e2f"
    "303132333435363738393a3b3c3d3e3f"
)
REQUEST_NONCE = bytes.fromhex("404142434445464748494a4b")
VERDICT_NONCE = bytes.fromhex("505152535455565758595a5b")
EXPIRES_AT = 1_787_097_720
VIEW_BYTES = (
    b'{"provider":"codex","kind":"question",'
    b'"prompt":"How should Codex handle approvals?",'
    b'"title":"Use the trusted hook",'
    b'"subtitle":"Desktop + CLI, one setup",'
    b'"can_approve":true}'
)
STATUS_PUBLICATION_ID = 1_787_097_600_123
STATUS_EXPIRES_AT = 1_787_097_615
STATUS_NONCE = bytes.fromhex("606162636465666768696a6b")
STATUS_BYTES = (
    b'{"agents":{"claude":{"active_count":1,"jobs":[]},'
    b'"codex":{"active_count":0,"jobs":[]}},"seq":7,"v":2}'
)


def _outer(envelope: bytes) -> dict:
    return json.loads(envelope.decode("utf-8"))


def _replace_outer(envelope: bytes, **changes) -> bytes:
    value = _outer(envelope)
    value.update(changes)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _mutate_bytes(value: bytes, index: int) -> bytes:
    changed = bytearray(value)
    changed[index] ^= 1
    return bytes(changed)


class StatusRelayCryptoTests(unittest.TestCase):
    def setUp(self):
        self.keys = derive_keys(decode_device_key(DEVICE_KEY_HEX), MAILBOX)
        self.envelope = encode_status(
            self.keys, MAILBOX, STATUS_PUBLICATION_ID, STATUS_EXPIRES_AT,
            STATUS_BYTES, nonce=STATUS_NONCE,
            padding=lambda size: b"\xa5" * size,
        )

    def test_status_key_is_direction_separated(self):
        self.assertEqual(len(self.keys.status_aead), 32)
        self.assertEqual(len({
            self.keys.request_aead,
            self.keys.verdict_aead,
            self.keys.verdict_mac,
            self.keys.status_aead,
        }), 4)
        self.assertEqual(
            status_aad(MAILBOX),
            b"vibepulse-ir/v1|vp_A1b2C3d4E5f6G7h8|status",
        )

    def test_status_roundtrip_uses_an_exact_fixed_frame(self):
        outer = _outer(self.envelope)
        ciphertext = b64url_decode(outer["ciphertext"])
        self.assertEqual(STATUS_FRAME_BYTES, 2816)
        self.assertEqual(MAX_STATUS_BYTES, 2560)
        self.assertEqual(len(ciphertext), STATUS_FRAME_BYTES + 16)
        self.assertEqual(
            self.envelope,
            json.dumps(outer, sort_keys=True, separators=(",", ":")).encode(),
        )
        decoded = decode_status(self.keys, MAILBOX, self.envelope)
        self.assertIsInstance(decoded, RelayStatus)
        self.assertEqual(decoded.publication_id, STATUS_PUBLICATION_ID)
        self.assertEqual(decoded.expires_at, STATUS_EXPIRES_AT)
        self.assertEqual(decoded.status_bytes, STATUS_BYTES)
        self.assertEqual(decoded.status_sha256,
                         hashlib.sha256(STATUS_BYTES).digest())

    def test_status_plaintext_layout_and_padding_are_pinned(self):
        outer = _outer(self.envelope)
        frame = AESGCM(self.keys.status_aead).decrypt(
            STATUS_NONCE, b64url_decode(outer["ciphertext"]),
            status_aad(MAILBOX),
        )
        self.assertEqual(len(frame), STATUS_FRAME_BYTES)
        self.assertEqual(frame[:4], b"VPS1")
        self.assertEqual(int.from_bytes(frame[4:12], "big"),
                         STATUS_PUBLICATION_ID)
        self.assertEqual(int.from_bytes(frame[12:16], "big"),
                         STATUS_EXPIRES_AT)
        self.assertEqual(int.from_bytes(frame[16:18], "big"),
                         len(STATUS_BYTES))
        self.assertEqual(frame[18:50], hashlib.sha256(STATUS_BYTES).digest())
        self.assertEqual(frame[50:50 + len(STATUS_BYTES)], STATUS_BYTES)
        self.assertEqual(frame[50 + len(STATUS_BYTES):],
                         b"\xa5" * (STATUS_FRAME_BYTES - 50 -
                                      len(STATUS_BYTES)))

    def test_tamper_wrong_key_and_wrong_mailbox_are_rejected(self):
        outer = _outer(self.envelope)
        nonce = b64url_decode(outer["nonce"])
        ciphertext = b64url_decode(outer["ciphertext"])
        invalid = (
            _replace_outer(
                self.envelope, nonce=b64url_encode(_mutate_bytes(nonce, 0))),
            _replace_outer(
                self.envelope,
                ciphertext=b64url_encode(_mutate_bytes(ciphertext, 0))),
            _replace_outer(
                self.envelope,
                ciphertext=b64url_encode(_mutate_bytes(ciphertext, -1))),
        )
        for envelope in invalid:
            with self.subTest(envelope=envelope[:64]):
                with self.assertRaises(ValueError):
                    decode_status(self.keys, MAILBOX, envelope)
        with self.assertRaises(ValueError):
            decode_status(
                derive_keys(b"\xff" * 32, MAILBOX), MAILBOX, self.envelope)
        with self.assertRaises(ValueError):
            decode_status(
                self.keys, "vp_Z9y8X7w6V5u4T3s2", self.envelope)

    def _authenticated_frame(self, mutate) -> bytes:
        outer = _outer(self.envelope)
        nonce = b64url_decode(outer["nonce"])
        frame = AESGCM(self.keys.status_aead).decrypt(
            nonce, b64url_decode(outer["ciphertext"]),
            status_aad(MAILBOX),
        )
        changed = mutate(bytearray(frame))
        ciphertext = AESGCM(self.keys.status_aead).encrypt(
            nonce, bytes(changed), status_aad(MAILBOX))
        return _replace_outer(
            self.envelope, ciphertext=b64url_encode(ciphertext))

    def test_authenticated_malformed_status_frames_are_rejected(self):
        def replace(start, end, value):
            def mutate(frame):
                frame[start:end] = value
                return frame
            return mutate

        invalid = {
            "magic": replace(0, 4, b"BAD!"),
            "zero publication": replace(4, 12, b"\0" * 8),
            "zero expiry": replace(12, 16, b"\0" * 4),
            "zero length": replace(16, 18, b"\0\0"),
            "over-cap length": replace(
                16, 18, (MAX_STATUS_BYTES + 1).to_bytes(2, "big")),
            "digest": replace(18, 50, b"\0" * 32),
            "short frame": lambda frame: frame[:-1],
        }
        for name, mutate in invalid.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    decode_status(
                        self.keys, MAILBOX, self._authenticated_frame(mutate))

    def test_status_encode_rejects_invalid_fields_and_sources(self):
        cases = (
            {"publication_id": 0},
            {"publication_id": True},
            {"publication_id": 0x10000000000000000},
            {"expires_at": 0},
            {"expires_at": True},
            {"expires_at": 0x100000000},
            {"status_bytes": b""},
            {"status_bytes": b"x" * (MAX_STATUS_BYTES + 1)},
            {"status_bytes": "not bytes"},
            {"nonce": b"short"},
            {"padding": b"wrong"},
            {"padding": lambda size: "not bytes"},
        )
        base = {
            "publication_id": STATUS_PUBLICATION_ID,
            "expires_at": STATUS_EXPIRES_AT,
            "status_bytes": STATUS_BYTES,
            "nonce": STATUS_NONCE,
            "padding": lambda size: b"\0" * size,
        }
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    encode_status(self.keys, MAILBOX, **{**base, **changes})

    def test_status_outer_envelope_remains_strict(self):
        outer = _outer(self.envelope)
        invalid = (
            b"{}",
            b"[]",
            self.envelope + b" ",
            _replace_outer(self.envelope, extra=1),
            _replace_outer(self.envelope, v=True),
            _replace_outer(self.envelope, nonce="AA"),
            _replace_outer(self.envelope, ciphertext="AA"),
            (b'{"ciphertext":"' + outer["ciphertext"].encode() +
             b'","nonce":"' + outer["nonce"].encode() +
             b'","v":1,"v":1}'),
        )
        for envelope in invalid:
            with self.subTest(envelope=envelope[:64]):
                with self.assertRaises(ValueError):
                    decode_status(self.keys, MAILBOX, envelope)


class ProtocolVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vector = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))

    def test_fixed_vector_reconstructs_every_protocol_boundary(self):
        vector = self.vector
        self.assertEqual(vector["inputs"]["deviceKeyHex"], DEVICE_KEY_HEX)
        self.assertEqual(vector["inputs"]["mailbox"], MAILBOX)
        self.assertEqual(vector["inputs"]["requestId"], REQUEST_ID)
        self.assertEqual(vector["inputs"]["challengeHex"], CHALLENGE.hex())
        self.assertEqual(vector["inputs"]["requestNonceHex"],
                         REQUEST_NONCE.hex())
        self.assertEqual(vector["inputs"]["verdictNonceHex"],
                         VERDICT_NONCE.hex())
        self.assertEqual(vector["inputs"]["expiresAt"], EXPIRES_AT)
        self.assertEqual(vector["inputs"]["viewUtf8"], VIEW_BYTES.decode())

        keys = derive_keys(decode_device_key(DEVICE_KEY_HEX), MAILBOX)
        expected = vector["expected"]
        self.assertEqual(
            hashlib.sha256(b"VibePulse interaction relay v1").hexdigest(),
            expected["protocolSaltHex"],
        )
        self.assertEqual(keys.request_aead.hex(), expected["requestKeyHex"])
        self.assertEqual(keys.verdict_aead.hex(), expected["verdictKeyHex"])
        self.assertEqual(keys.verdict_mac.hex(),
                         expected["verdictMacKeyHex"])
        self.assertEqual(request_aad(MAILBOX, REQUEST_ID).decode(),
                         expected["requestAadUtf8"])
        self.assertEqual(verdict_aad(MAILBOX, REQUEST_ID).decode(),
                         expected["verdictAadUtf8"])
        self.assertEqual(hashlib.sha256(VIEW_BYTES).hexdigest(),
                         expected["viewSha256Hex"])
        self.assertEqual(
            b64url_encode(hashlib.sha256(VIEW_BYTES).digest()),
            expected["viewSha256Base64url"],
        )

        request_envelope = encode_request(
            keys, MAILBOX, REQUEST_ID, CHALLENGE, EXPIRES_AT, VIEW_BYTES,
            nonce=REQUEST_NONCE, padding=lambda size: b"\xa5" * size,
        )
        request_outer = _outer(request_envelope)
        request_ciphertext = b64url_decode(request_outer["ciphertext"])
        self.assertEqual(len(request_ciphertext), REQUEST_FRAME_BYTES + 16)
        self.assertEqual(expected["requestFrameBytes"], REQUEST_FRAME_BYTES)
        self.assertEqual(request_outer["ciphertext"],
                         expected["requestCiphertextBase64url"])
        self.assertEqual(request_ciphertext[-16:].hex(),
                         expected["requestTagHex"])
        request_plaintext = AESGCM(keys.request_aead).decrypt(
            REQUEST_NONCE, request_ciphertext,
            request_aad(MAILBOX, REQUEST_ID))
        self.assertEqual(hashlib.sha256(request_plaintext).hexdigest(),
                         expected["requestFrameSha256Hex"])
        request_json_size = int.from_bytes(request_plaintext[:2], "big")
        self.assertEqual(
            request_plaintext[2:2 + request_json_size].decode(),
            expected["requestInnerJsonUtf8"],
        )
        self.assertEqual(request_envelope.decode(),
                         expected["requestEnvelopeUtf8"])
        request = decode_request(keys, MAILBOX, REQUEST_ID, request_envelope)
        self.assertEqual(request.challenge, CHALLENGE)
        self.assertEqual(request.expires_at, EXPIRES_AT)
        self.assertEqual(request.view_bytes, VIEW_BYTES)
        self.assertEqual(request.view_sha256.hex(),
                         expected["viewSha256Hex"])

        mac_bytes = verdict_mac_bytes(keys, MAILBOX, request, "approve")
        self.assertEqual(mac_bytes.hex(), expected["verdictHmacHex"])
        self.assertEqual(b64url_encode(mac_bytes),
                         expected["verdictHmacBase64url"])
        self.assertEqual(
            verdict_mac_message(MAILBOX, request, "approve").hex(),
            expected["verdictMacMessageHex"],
        )
        self.assertEqual(
            hmac.new(keys.verdict_mac,
                     bytes.fromhex(expected["verdictMacMessageHex"]),
                     hashlib.sha256).digest(),
            mac_bytes,
        )
        verdict_envelope = encode_verdict(
            keys, MAILBOX, request, "approve", nonce=VERDICT_NONCE,
            padding=lambda size: b"\xa5" * size,
        )
        verdict_outer = _outer(verdict_envelope)
        verdict_ciphertext = b64url_decode(verdict_outer["ciphertext"])
        self.assertEqual(len(verdict_ciphertext), VERDICT_FRAME_BYTES + 16)
        self.assertEqual(expected["verdictFrameBytes"], VERDICT_FRAME_BYTES)
        self.assertEqual(verdict_outer["ciphertext"],
                         expected["verdictCiphertextBase64url"])
        self.assertEqual(verdict_ciphertext[-16:].hex(),
                         expected["verdictTagHex"])
        verdict_plaintext = AESGCM(keys.verdict_aead).decrypt(
            VERDICT_NONCE, verdict_ciphertext,
            verdict_aad(MAILBOX, REQUEST_ID))
        self.assertEqual(hashlib.sha256(verdict_plaintext).hexdigest(),
                         expected["verdictFrameSha256Hex"])
        verdict_json_size = int.from_bytes(verdict_plaintext[:2], "big")
        self.assertEqual(
            verdict_plaintext[2:2 + verdict_json_size].decode(),
            expected["verdictInnerJsonUtf8"],
        )
        self.assertEqual(verdict_envelope.decode(),
                         expected["verdictEnvelopeUtf8"])
        verdict = decode_verdict(
            keys, MAILBOX, REQUEST_ID, verdict_envelope)
        self.assertTrue(verify_verdict_mac(keys, MAILBOX, request, verdict))
        self.assertEqual(verdict.verdict, "approve")

    def test_base64url_is_canonical_and_unpadded(self):
        for raw in (b"", b"\x00", bytes(range(32)), bytes(range(255))):
            encoded = b64url_encode(raw)
            self.assertNotIn("=", encoded)
            self.assertEqual(b64url_decode(encoded), raw)
        for invalid in ("AA==", "AA=", "A", "+A", "/A", "AA\n", "å"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    b64url_decode(invalid)


class StrictDecodingTests(unittest.TestCase):
    def setUp(self):
        self.keys = derive_keys(decode_device_key(DEVICE_KEY_HEX), MAILBOX)
        self.envelope = encode_request(
            self.keys, MAILBOX, REQUEST_ID, CHALLENGE, EXPIRES_AT, VIEW_BYTES,
            nonce=REQUEST_NONCE, padding=lambda size: b"\xa5" * size,
        )
        self.request = decode_request(
            self.keys, MAILBOX, REQUEST_ID, self.envelope)
        self.verdict = encode_verdict(
            self.keys, MAILBOX, self.request, "approve",
            nonce=VERDICT_NONCE, padding=lambda size: b"\xa5" * size,
        )

    def test_device_key_shape_is_exact(self):
        self.assertEqual(len(decode_device_key(DEVICE_KEY_HEX)), 32)
        for invalid in (
                "", "0" * 63, "0" * 65, "g" * 64, "00" * 31 + " 0",
                None, b"0" * 64):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    decode_device_key(invalid)

    def test_nonce_ciphertext_tag_and_aad_tampering_are_rejected(self):
        outer = _outer(self.envelope)
        nonce = b64url_decode(outer["nonce"])
        ciphertext = b64url_decode(outer["ciphertext"])
        cases = (
            _replace_outer(self.envelope,
                           nonce=b64url_encode(_mutate_bytes(nonce, 0))),
            _replace_outer(self.envelope,
                           ciphertext=b64url_encode(
                               _mutate_bytes(ciphertext, 0))),
            _replace_outer(self.envelope,
                           ciphertext=b64url_encode(
                               _mutate_bytes(ciphertext, -1))),
        )
        for changed in cases:
            with self.subTest(changed=changed[:80]):
                with self.assertRaises(ValueError):
                    decode_request(self.keys, MAILBOX, REQUEST_ID, changed)
        for mailbox, request_id in (
                ("vp_Z9y8X7w6V5u4T3s2", REQUEST_ID),
                (MAILBOX, "_BEiM0RVZneImaq7zN3u_w")):
            with self.subTest(aad=(mailbox, request_id)):
                with self.assertRaises(ValueError):
                    decode_request(self.keys, mailbox, request_id,
                                   self.envelope)

    def test_outer_envelope_is_exact_bounded_json(self):
        outer = _outer(self.envelope)
        invalid = (
            b"{}",
            b"[]",
            b"not json",
            self.envelope + b" ",
            _replace_outer(self.envelope, extra=1),
            _replace_outer(self.envelope, v=True),
            _replace_outer(self.envelope, v=2),
            _replace_outer(self.envelope, nonce="AA"),
            _replace_outer(self.envelope, ciphertext="AA"),
            (b'{"v":1,"v":1,"nonce":"' + outer["nonce"].encode() +
             b'","ciphertext":"' + outer["ciphertext"].encode() + b'"}'),
            b"{" + b" " * 4096 + b"}",
        )
        for envelope in invalid:
            with self.subTest(envelope=envelope[:80]):
                with self.assertRaises(ValueError):
                    decode_request(self.keys, MAILBOX, REQUEST_ID, envelope)

    def _authenticated_request_with(self, mutate):
        outer = _outer(self.envelope)
        nonce = b64url_decode(outer["nonce"])
        frame = AESGCM(self.keys.request_aead).decrypt(
            nonce, b64url_decode(outer["ciphertext"]),
            request_aad(MAILBOX, REQUEST_ID))
        length = int.from_bytes(frame[:2], "big")
        value = json.loads(frame[2:2 + length])
        value = mutate(value)
        encoded = json.dumps(value, sort_keys=True,
                             separators=(",", ":")).encode()
        changed_frame = (len(encoded).to_bytes(2, "big") + encoded +
                         b"\xa5" * (REQUEST_FRAME_BYTES - 2 - len(encoded)))
        ciphertext = AESGCM(self.keys.request_aead).encrypt(
            nonce, changed_frame, request_aad(MAILBOX, REQUEST_ID))
        return _replace_outer(self.envelope,
                              ciphertext=b64url_encode(ciphertext))

    def _request_with_raw_frame(self, mutate):
        outer = _outer(self.envelope)
        nonce = b64url_decode(outer["nonce"])
        frame = AESGCM(self.keys.request_aead).decrypt(
            nonce, b64url_decode(outer["ciphertext"]),
            request_aad(MAILBOX, REQUEST_ID))
        changed = mutate(frame)
        ciphertext = AESGCM(self.keys.request_aead).encrypt(
            nonce, changed, request_aad(MAILBOX, REQUEST_ID))
        return _replace_outer(self.envelope,
                              ciphertext=b64url_encode(ciphertext))

    def test_authenticated_inner_request_tampering_is_rejected(self):
        def change(name, value):
            return lambda item: {**item, name: value}

        invalid = {
            "request id": change("requestId", "_BEiM0RVZneImaq7zN3u_w"),
            "view": change("view", b64url_encode(VIEW_BYTES + b"x")),
            "view digest": change("viewSha256", b64url_encode(b"\0" * 32)),
            "bool expiry": change("expiresAt", True),
            "negative expiry": change("expiresAt", -1),
            "unknown key": change("extra", 1),
        }
        for name, mutate in invalid.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    decode_request(
                        self.keys, MAILBOX, REQUEST_ID,
                        self._authenticated_request_with(mutate))

        for name, mutate in {
                "empty": lambda frame: b"\0\0" + frame[2:],
                "overflow": lambda frame: b"\xff\xff" + frame[2:],
                "short": lambda frame: frame[:-1],
        }.items():
            with self.subTest(frame=name):
                with self.assertRaises(ValueError):
                    decode_request(
                        self.keys, MAILBOX, REQUEST_ID,
                        self._request_with_raw_frame(mutate))

    def _authenticated_verdict_with(self, mutate):
        outer = _outer(self.verdict)
        nonce = b64url_decode(outer["nonce"])
        frame = AESGCM(self.keys.verdict_aead).decrypt(
            nonce, b64url_decode(outer["ciphertext"]),
            verdict_aad(MAILBOX, REQUEST_ID))
        length = int.from_bytes(frame[:2], "big")
        value = mutate(json.loads(frame[2:2 + length]))
        encoded = json.dumps(value, sort_keys=True,
                             separators=(",", ":")).encode()
        changed_frame = (len(encoded).to_bytes(2, "big") + encoded +
                         b"\xa5" * (VERDICT_FRAME_BYTES - 2 - len(encoded)))
        ciphertext = AESGCM(self.keys.verdict_aead).encrypt(
            nonce, changed_frame, verdict_aad(MAILBOX, REQUEST_ID))
        return _replace_outer(self.verdict,
                              ciphertext=b64url_encode(ciphertext))

    def test_verdict_and_mac_tampering_are_rejected(self):
        outer = _outer(self.verdict)
        nonce = b64url_decode(outer["nonce"])
        ciphertext = b64url_decode(outer["ciphertext"])
        tampered = {
            "nonce": _replace_outer(
                self.verdict,
                nonce=b64url_encode(_mutate_bytes(nonce, 0))),
            "ciphertext": _replace_outer(
                self.verdict,
                ciphertext=b64url_encode(_mutate_bytes(ciphertext, 0))),
            "tag": _replace_outer(
                self.verdict,
                ciphertext=b64url_encode(_mutate_bytes(ciphertext, -1))),
        }
        for name, changed in tampered.items():
            with self.subTest(field=name):
                with self.assertRaises(ValueError):
                    decode_verdict(self.keys, MAILBOX, REQUEST_ID, changed)

        for mailbox, request_id in (
                ("vp_Z9y8X7w6V5u4T3s2", REQUEST_ID),
                (MAILBOX, "_BEiM0RVZneImaq7zN3u_w")):
            with self.subTest(aad=(mailbox, request_id)):
                with self.assertRaises(ValueError):
                    decode_verdict(self.keys, mailbox, request_id,
                                   self.verdict)

        for verdict in ("approve", "deny", "terminal", "panic"):
            encoded = encode_verdict(
                self.keys, MAILBOX, self.request, verdict,
                nonce=VERDICT_NONCE, padding=lambda size: b"\xa5" * size)
            decoded = decode_verdict(
                self.keys, MAILBOX, REQUEST_ID, encoded)
            self.assertTrue(verify_verdict_mac(
                self.keys, MAILBOX, self.request, decoded))

        wrong_request = RelayRequest(
            request_id=self.request.request_id,
            challenge=_mutate_bytes(self.request.challenge, 0),
            expires_at=self.request.expires_at,
            view_bytes=self.request.view_bytes,
            view_sha256=self.request.view_sha256,
        )
        decoded = decode_verdict(
            self.keys, MAILBOX, REQUEST_ID, self.verdict)
        self.assertFalse(verify_verdict_mac(
            self.keys, MAILBOX, wrong_request, decoded))
        wrong_keys = derive_keys(b"\xff" * 32, MAILBOX)
        self.assertFalse(verify_verdict_mac(
            wrong_keys, MAILBOX, self.request, decoded))
        with self.assertRaises(ValueError):
            decode_request(wrong_keys, MAILBOX, REQUEST_ID, self.envelope)
        with self.assertRaises(ValueError):
            decode_verdict(wrong_keys, MAILBOX, REQUEST_ID, self.verdict)

        def change(name, value):
            return lambda item: {**item, name: value}

        for name, mutate, decodes in (
                ("request id",
                 change("requestId", "_BEiM0RVZneImaq7zN3u_w"), False),
                ("challenge",
                 change("challenge", b64url_encode(
                     _mutate_bytes(CHALLENGE, 0))), True),
                ("view digest",
                 change("viewSha256", b64url_encode(b"\0" * 32)), True),
                ("verdict", change("verdict", "deny"), True),
                ("hmac", change("hmac", b64url_encode(b"\0" * 32)), True),
                ("unknown key", change("extra", 1), False),
        ):
            with self.subTest(inner=name):
                changed = self._authenticated_verdict_with(mutate)
                if decodes:
                    untrusted = decode_verdict(
                        self.keys, MAILBOX, REQUEST_ID, changed)
                    self.assertFalse(verify_verdict_mac(
                        self.keys, MAILBOX, self.request, untrusted))
                else:
                    with self.assertRaises(ValueError):
                        decode_verdict(
                            self.keys, MAILBOX, REQUEST_ID, changed)

    def test_view_and_frame_caps_are_enforced(self):
        for size, accepted in ((640, True), (641, False)):
            view = b"x" * size
            with self.subTest(size=size):
                if accepted:
                    envelope = encode_request(
                        self.keys, MAILBOX, REQUEST_ID, CHALLENGE,
                        EXPIRES_AT, view, nonce=REQUEST_NONCE,
                        padding=lambda count: b"\0" * count)
                    self.assertEqual(
                        decode_request(self.keys, MAILBOX, REQUEST_ID,
                                       envelope).view_bytes,
                        view)
                else:
                    with self.assertRaises(ValueError):
                        encode_request(
                            self.keys, MAILBOX, REQUEST_ID, CHALLENGE,
                            EXPIRES_AT, view)

    def test_expiry_is_an_exact_cross_language_uint32(self):
        envelope = encode_request(
            self.keys, MAILBOX, REQUEST_ID, CHALLENGE, 0xFFFFFFFF,
            VIEW_BYTES, nonce=REQUEST_NONCE,
            padding=lambda size: b"\0" * size)
        self.assertEqual(
            decode_request(self.keys, MAILBOX, REQUEST_ID,
                           envelope).expires_at,
            0xFFFFFFFF)
        for invalid in (0, -1, True, 0x100000000):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    encode_request(
                        self.keys, MAILBOX, REQUEST_ID, CHALLENGE,
                        invalid, VIEW_BYTES)


if __name__ == "__main__":
    unittest.main()
