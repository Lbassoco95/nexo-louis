"""Pruebas de louis_push: vector oficial RFC 8291 §5, ida y vuelta y JWT VAPID verificable."""
import os, sys, json, base64, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
os.environ["PUSH_STATE_DIR"] = tempfile.mkdtemp()
import louis_push as P
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def decrypt(body, ua_priv, ua_pub, auth):
    salt, idlen = body[:16], body[20]
    as_pub, ct = body[21:21 + idlen], body[21 + idlen:]
    ecdh = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    prk = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + ua_pub + as_pub).derive(ecdh)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(prk)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(prk)
    pt = AESGCM(cek).decrypt(nonce, ct, None)
    assert pt[-1] == 2
    return pt[:-1]


class T(unittest.TestCase):
    def test_rfc8291_vector(self):
        body = P.encrypt(b"When I grow up, I want to be a watermelon",
                         "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                         "BTBZMqHH6r4Tts7J_aSIgg",
                         _as_priv=P._unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"),
                         _salt=P._unb64u("DGv6ra1nlYgDCS1FRnbzlw"))
        self.assertEqual(P._b64u(body),
            "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")

    def test_roundtrip(self):
        ua = ec.generate_private_key(ec.SECP256R1())
        pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        auth = os.urandom(16)
        msg = json.dumps({"title": "Áéí ñ ✅"}, ensure_ascii=False).encode()
        self.assertEqual(decrypt(P.encrypt(msg, P._b64u(pub), P._b64u(auth)), ua, pub, auth), msg)

    def test_vapid_jwt_verifica(self):
        h = P._vapid_headers("https://fcm.googleapis.com/fcm/send/abc")["Authorization"]
        jwt = h.split("t=")[1].split(",")[0]
        a, b, sig = jwt.split(".")
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), P._unb64u(P.public_key()))
        pub.verify(encode_dss_signature(int.from_bytes(P._unb64u(sig)[:32], "big"), int.from_bytes(P._unb64u(sig)[32:], "big")),
                   f"{a}.{b}".encode(), ec.ECDSA(hashes.SHA256()))
        self.assertEqual(json.loads(P._unb64u(b))["aud"], "https://fcm.googleapis.com")
        self.assertEqual(P.public_key(), P.public_key())  # persistente

    def test_endpoint_allowlist(self):
        self.assertTrue(P.endpoint_ok("https://fcm.googleapis.com/x"))
        self.assertTrue(P.endpoint_ok("https://web.push.apple.com/x"))
        for bad in ("http://fcm.googleapis.com/x", "https://evil.com/x", "https://127.0.0.1/x", "https://fcm.googleapis.com.evil.com/x"):
            self.assertFalse(P.endpoint_ok(bad), bad)

    def test_subs(self):
        ua = ec.generate_private_key(ec.SECP256R1())
        pub = P._b64u(ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/z", "keys": {"p256dh": pub, "auth": P._b64u(os.urandom(16))}}
        self.assertTrue(P.add_sub(sub, "iPhone")); self.assertTrue(P.add_sub(sub)); self.assertEqual(P.count(), 1)
        self.assertFalse(P.add_sub({"endpoint": "https://evil.com/x", "keys": sub["keys"]}))
        self.assertEqual(P.notify("x"), 0)  # red bloqueada en sandbox → no lanza
        P.remove_sub(sub["endpoint"]); self.assertEqual(P.count(), 0)

if __name__ == "__main__":
    unittest.main()
