"""Fixtures d'authentification, stockage et reprise ; aucun compte réel contacté."""

import base64
import fcntl
import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from benchab.machine import session_compte as S
from benchab.machine.cloud_lecture import CloudErreur

NOW = 1800000000


def token(subject="fixture-account", expiry=NOW + 90 * 86400, **extra):
    c = {"iss": S.ISSUER, "sub": subject, "exp": expiry, "tokenType": "access-token", **extra}
    body = base64.urlsafe_b64encode(json.dumps(c).encode()).decode().rstrip("=")
    return "fixture." + body + ".not-a-real-signature"


# Identités purement synthétiques ; aucune imprimante ni compte réel.


class Response(io.BytesIO):
    status = 200
    headers = {}

    def __init__(self, body, url):
        super().__init__(json.dumps(body).encode())
        self.url = url

    def geturl(self):
        return self.url


class Transport:
    def __init__(self, *values):
        self.values = list(values)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return Response(value, request.full_url)


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.account = self.base / "account.json"
        self.session = self.base / "session.json"
        S.configurer("@".join(("fixture", "example.test")), "fictional-password", self.account)

    def tearDown(self):
        self.tmp.cleanup()

    def auth(self, transport, clock=lambda: NOW):
        return S.SessionCompte(self.account, self.session, opener=transport, clock=clock)

    def login(self, refresh="fixture-refresh"):
        tr = Transport(
            {"status": "ok", "data": "fixture-code"},
            {"access_token": token(), "refresh_token": refresh, "expires_in": 90 * 86400},
        )
        result = self.auth(tr).jeton()
        return (tr, result)

    def old(self, *, expired=False, refresh="fixture-refresh"):
        value = {
            "account_hash": hashlib.sha256("@".join(("fixture", "example.test")).encode()).hexdigest(),
            "access_token": token(expiry=NOW - 10) if expired else token(),
            "refresh_token": refresh,
            "expires_at": NOW - 10 if expired else NOW + 90 * 86400,
            "subject": "fixture-account",
            "obtained_at": NOW - 8 * 86400,
            "retry_after": 0,
        }
        S._write(self.session, value)
        return value

    def test_pkce_request_binds_verifier_and_stores_private_cache(self):
        tr, result = self.login()
        first, second = tr.requests
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(first.full_url).query)
        form = urllib.parse.parse_qs(second.data.decode())
        verifier = form["code_verifier"][0]
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(query["code_challenge"], [expected])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["type"], ["code"])
        self.assertNotIn("client_secret", form)
        self.assertEqual(json.loads(first.data)["application"], "ac_slicer_next")
        self.assertEqual(self.session.stat().st_mode & 511, 384)
        self.assertNotIn("password", self.session.read_text())
        self.assertEqual(self.auth(Transport()).jeton(), result)

    def test_no_existing_account_overwrite(self):
        before = self.account.read_bytes()
        with self.assertRaises(CloudErreur):
            S.configurer("@".join(("new", "example.test")), "different", self.account)
        self.assertEqual(self.account.read_bytes(), before)

    def test_secret_paths_inside_git_refused_even_with_dotdot(self):
        for path in (S.REPOSITORY / "fixture-secret.json", S.REPOSITORY / "docs/../fixture-secret.json"):
            with self.assertRaisesRegex(CloudErreur, "COMPTE_HORS_DEPOT_REQUIS"):
                S.configurer("@".join(("fixture", "example.test")), "fictional", path)
            self.assertFalse(path.exists())

    def test_refresh_due_uses_json_and_preserves_refresh_when_missing(self):
        self.old()
        tr = Transport({"access_token": token(expiry=NOW + 100 * 86400)})
        self.auth(tr).jeton()
        self.assertEqual(len(tr.requests), 1)
        self.assertTrue(tr.requests[0].full_url.endswith("/api/login/oauth/refresh_token"))
        self.assertEqual(json.loads(tr.requests[0].data)["grant_type"], "refresh_token")
        self.assertEqual(S._read(self.session)["refresh_token"], "fixture-refresh")

    def test_invalid_refresh_falls_back_once_to_new_pkce(self):
        self.old()
        tr = Transport({"status": "error"}, {"status": "ok", "data": "new-code"}, {"access_token": token()})
        self.assertEqual(self.auth(tr).jeton(), token())
        self.assertEqual(len(tr.requests), 3)
        self.assertIn("code_challenge=", tr.requests[1].full_url)

    def test_refuses_subject_change_and_preserves_session(self):
        self.old()
        before = self.session.read_bytes()
        tr = Transport({"access_token": token(subject="another-fixture")})
        with self.assertRaisesRegex(CloudErreur, "COMPTE_DIFFERENT_REFUSE"):
            self.auth(tr).jeton()
        self.assertEqual(self.session.read_bytes(), before)
        self.assertEqual(len(tr.requests), 1)

    def test_refuses_credentials_account_change_before_network(self):
        self.login()
        S._write(self.account, {"email": "@".join(("another", "example.test")), "password": "fictional"})
        tr = Transport()
        with self.assertRaisesRegex(CloudErreur, "COMPTE_DIFFERENT_REFUSE"):
            self.auth(tr).jeton()
        self.assertFalse(tr.requests)

    def test_expired_access_never_returned_on_outage_and_backoff_persisted(self):
        self.old(expired=True, refresh=None)
        tr = Transport(TimeoutError("fixture-sensitive-response"))
        for _ in range(2):
            with self.assertRaises(CloudErreur) as result:
                self.auth(tr).jeton()
            self.assertNotIn("fixture-sensitive-response", str(result.exception))
        self.assertEqual(len(tr.requests), 1)

    def test_valid_access_conserved_during_outage_with_backoff(self):
        old = self.old(refresh=None)
        tr = Transport(TimeoutError())
        self.assertEqual(self.auth(tr).jeton(), old["access_token"])
        self.assertEqual(self.auth(tr).jeton(), old["access_token"])
        self.assertEqual(len(tr.requests), 1)

    def test_rejected_access_not_reused_after_failed_forced_refresh(self):
        self.old(refresh=None)
        tr = Transport(TimeoutError())
        with self.assertRaises(CloudErreur):
            self.auth(tr).jeton(force=True)
        with self.assertRaises(CloudErreur):
            self.auth(tr).jeton()
        self.assertEqual(len(tr.requests), 1)

    def test_rejected_login_never_exchanges_code(self):
        tr = Transport({"status": "error", "data": "fictional-password"})
        with self.assertRaises(CloudErreur):
            self.auth(tr).jeton()
        self.assertEqual(len(tr.requests), 1)
        self.assertNotIn("fictional-password", self.session.read_text())

    def test_wrong_pkce_state_never_exchanges_code(self):
        tr = Transport({"status": "ok", "data": "fixture", "state": "wrong-state"})
        with self.assertRaises(CloudErreur):
            self.auth(tr).jeton()
        self.assertEqual(len(tr.requests), 1)

    def test_refuses_world_readable_credentials(self):
        self.account.chmod(420)
        with self.assertRaisesRegex(CloudErreur, "FICHIER_COMPTE_NON_PRIVE"):
            self.auth(Transport()).jeton()

    def test_refuses_symlink_file_and_ancestor(self):
        link = self.base / "link"
        link.symlink_to(self.account)
        with self.assertRaises(CloudErreur):
            S._read(link)
        directory = self.base / "directory"
        directory.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(CloudErreur):
            S._read(directory / "account.json")

    def test_refuses_fifo_and_hardlink(self):
        fifo = self.base / "fifo"
        os.mkfifo(fifo, 384)
        with self.assertRaises(CloudErreur):
            S._read(fifo)
        os.link(self.account, self.base / "hardlink")
        with self.assertRaises(CloudErreur):
            S._read(self.account)

    def test_session_symlink_target_preserved(self):
        self.session.symlink_to(self.account)
        before = self.account.read_bytes()
        with self.assertRaises(CloudErreur):
            self.auth(Transport()).jeton()
        self.assertEqual(before, self.account.read_bytes())

    def test_untrusted_session_directory_not_chmoded(self):
        self.base.chmod(493)
        with self.assertRaises(CloudErreur):
            self.auth(Transport()).jeton()
        self.assertEqual(self.base.stat().st_mode & 511, 493)

    def test_two_process_lock_rejects_without_authentication(self):
        path = self.session.with_name(self.session.name + ".lock")
        path.touch(mode=384)
        with path.open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            tr = Transport()
            with self.assertRaisesRegex(CloudErreur, "CONNEXION_COMPTE_EN_COURS"):
                self.auth(tr).jeton()
            self.assertFalse(tr.requests)

    def test_claims_rejects_wrong_issuer_type_and_bad_expiry(self):
        for value in ("fixture", token(iss="https://example.test"), token(tokenType="id-token"), token(exp=True)):
            self.assertEqual(S.claims(value), {})

    def test_representations_hide_credentials(self):
        self.assertNotIn("fictional-password", repr(self.auth(Transport())))
        with self.assertRaises(TypeError):
            import pickle

            pickle.dumps(self.auth(Transport()))

    def test_status_does_not_read_account_or_slicer(self):
        with (
            patch.dict(os.environ, {"BENCHAB_ANYCUBIC_ACCOUNT_FILE": str(self.account)}),
            patch.object(S, "_read", side_effect=AssertionError()),
        ):
            self.assertEqual(S.etat()["statut"], "COMPTE_CONFIGURE_NON_VALIDE")


if __name__ == "__main__":
    unittest.main()
