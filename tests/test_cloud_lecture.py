"""Contrats réels de routes et projection, transport injecté, aucun compte contacté."""

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from benchab.machine import cloud_lecture as C

FACTICE_1 = "PRIVATE_IDENTIFIER"
NOW = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)
INFO = {
    "id": 42,
    "device_status": 1,
    "version": {"firmware_version": "2.5.0"},
    "name": "PRIVATE_ACCOUNT_MACHINE",
    "device_id": FACTICE_1,
    "parameter": {"curr_nozzle_temp": "215", "curr_hotbed_temp": 55},
    "base": {"print_count": "7", "print_totaltime": "2hour10min", "material_used": "1.25kg"},
    "project": {
        "task_id": 22,
        "print_status": 1,
        "progress": "25",
        "remain_time": "12",
        "print_time": "4",
        "curr_layer": 10,
        "total_layers": 40,
        "name": "PRIVATE_FILENAME",
    },
    "multi_color_box": [
        {
            "id": 0,
            "model_id": 4,
            "temp": 30,
            "humidity": 22,
            "slots": [
                {
                    "index": 0,
                    "edit_status": 1,
                    "type": "PLA",
                    "consumables_percent": 99,
                    "sku": "STALE_SKU",
                    "rfid_uid": "PRIVATE_RFID",
                }
            ],
        }
    ],
}


class Response(io.BytesIO):
    def __init__(self, body, url, status=200, headers=None):
        super().__init__(body)
        self.url = url
        self.status = status
        self.headers = headers or {}

    def geturl(self):
        return self.url


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        body = self.responses.pop(0)
        if isinstance(body, Exception):
            raise body
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        return Response(body, request.full_url)


class Lecture(unittest.TestCase):
    def test_schema_service_inconnu_ne_renouvelle_pas_compte(self):

        class Account:
            def jeton(self, *, force=False):
                if force:
                    raise AssertionError("refresh injustifie")
                return "ACCESS_FIXTURE_OLD"

        for response in (
            {"code": 9, "data": {}},
            {"code": 1, "data": None},
            {"code": 1, "data": []},
            {"code": 1, "data": {}},
            {"code": 1, "data": {"token": None}},
        ):
            tr = Transport([response])
            with self.assertRaisesRegex(C.CloudErreur, "SERVICE_REFUSE_OU_SCHEMA_INCONNU"):
                C.relever_avec_compte(
                    session=Account(),
                    opener=tr,
                    identite={"app_id": "FIXTURE_APP", "app_secret": "FIXTURE_PUBLIC_SIGNING", "version": "2.0"},
                    clock=lambda: NOW,
                )
            self.assertEqual(len(tr.requests), 1)

    def test_notre_machine_unique_qualifiee_un_seul_login(self):
        tr = Transport(
            [
                {"code": 1, "data": {"token": "INTERNAL_FIXTURE"}},
                {"code": 1, "data": [{"id": 42, "machine_type": 20025}]},
                {"code": 1, "data": INFO},
            ]
        )
        result = self.client(tr).relever_notre_machine()
        self.assertTrue(result["selection_automatique"])
        self.assertEqual([r.method for r in tr.requests], ["POST", "GET", "GET"])
        self.assertEqual(tr.requests[-1].full_url, C.API + "/v2/printer/info?id=42")
        self.assertFalse(result["commandes_machine"])

    def test_notre_machine_inconnue_multiple_ou_absente_ne_lit_aucune_cible(self):
        for rows in (
            [],
            [{"id": 42, "machine_type": 20019}],
            [{"id": 42, "machine_type": 20025}, {"id": 43, "machine_type": 20025}],
        ):
            tr = Transport([{"code": 1, "data": {"token": "INTERNAL_FIXTURE"}}, {"code": 1, "data": rows}])
            result = self.client(tr).relever_notre_machine()
            self.assertFalse(result["selection_automatique"])
            self.assertEqual(len(tr.requests), 2)

    def test_cli_notre_machine_delegue_sans_lire_compte_ou_slicer(self):
        with (
            patch.object(C, "relever_avec_compte", return_value={"statut": "FIXTURE"}) as read,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(C.main(["--cloud", "--compte", "--notre-machine"]), 0)
        read.assert_called_once_with(None, notre_machine=True)

    def test_jeton_compte_nouveau_refuse_pas_de_boucle_reconnexion(self):

        class Account:
            renouvele = True

            def jeton(self, *, force=False):
                if force:
                    raise AssertionError("seconde connexion interdite")
                return "ACCESS_FIXTURE_NEW"

        transport = Transport([{"code": 10001, "data": {}}])
        with self.assertRaises(C.CloudErreur):
            C.relever_avec_compte(
                session=Account(),
                opener=transport,
                identite={"app_id": "FIXTURE_APP", "app_secret": "FIXTURE_PUBLIC_SIGNING", "version": "2.0"},
                clock=lambda: NOW,
            )
        self.assertEqual(len(transport.requests), 1)

    def test_refus_http_auth_distingue_indisponibilite(self):
        req = urllib.request.Request(C.API + "/work/printer/getPrinters")
        for status in (401, 403):
            with self.assertRaisesRegex(C.CloudErreur, "AUTHENTIFICATION_REFUSEE"):
                C._octets(req, lambda *a, **k: Response(b"{}", req.full_url, status=status), auth_http=True)
        with self.assertRaisesRegex(C.CloudErreur, "REPONSE_HTTP_REFUSEE"):
            C._octets(req, lambda *a, **k: Response(b"{}", req.full_url, status=502), auth_http=True)

    def test_compte_autonome_liste_sans_session_slicer_et_reprise_bornee(self):

        class Account:
            def __init__(self):
                self.calls = []

            def jeton(self, *, force=False):
                self.calls.append(force)
                return "ACCESS_FIXTURE_NEW" if force else "ACCESS_FIXTURE_OLD"

        account = Account()
        transport = Transport(
            [
                {"code": 10001, "data": {}},
                {"code": 1, "data": {"token": "INTERNAL_FIXTURE"}},
                {"code": 1, "data": [{"id": 42, "machine_type": 20019, "device_status": 1}]},
            ]
        )
        with patch.object(C, "token_slicer", side_effect=AssertionError("slicer interdit")):
            result = C.relever_avec_compte(
                session=account,
                opener=transport,
                identite={"app_id": "FIXTURE_APP", "app_secret": "FIXTURE_PUBLIC_SIGNING", "version": "2.0"},
                clock=lambda: NOW,
            )
        self.assertEqual(account.calls, [False, True])
        self.assertEqual(result["authentification"], "COMPTE_AUTONOME_PKCE")
        self.assertEqual(result["machines"][0]["id"], 42)
        self.assertFalse(result["commandes_machine"])
        self.assertNotIn("ACCESS_FIXTURE", json.dumps(result))

    def test_quantite_declaree_distincte_de_masse_mesuree(self):
        result = C.projeter(INFO, 42)
        slot = result["ace"][0]["slots"][0]
        self.assertIsNone(slot["remaining_percent"])
        self.assertEqual(slot["disponibilite"]["pourcentage_rapporte"], 99)
        self.assertEqual(slot["disponibilite"]["nature"], "declaration_manuelle_cloud")
        self.assertIsNone(slot["disponibilite"]["masse_disponible_g"])
        self.assertFalse(slot["disponibilite"]["validation_physique"])

    def test_code_succes_un_observe_sur_compte_reel(self):
        tr = Transport(
            [
                {"code": 1, "data": {"token": "USER_SECRET_TOKEN"}},
                {"code": 1, "data": [{"id": 42, "machine_type": 20019, "device_status": 1}]},
            ]
        )
        result = self.client(tr).lister_machines()
        self.assertEqual(result["statut"], "CHOISIR_MACHINE")
        self.assertEqual(result["machines"][0]["id"], 42)
        self.assertFalse(result["commandes_machine"])
        for code in (True, 1.0, None, "1"):
            tr = Transport([{"code": code, "data": {"token": "USER_SECRET_TOKEN"}}])
            with self.assertRaises(C.CloudErreur):
                self.client(tr).lister_machines()

    def client(self, transport):
        return C.CloudLecture(
            "ACCESS_SECRET_TOKEN", "APP_IDENTIFIER", "APP_SECRET_VALUE", "2.0", opener=transport, clock=lambda: NOW
        )

    def test_routes_signature_et_donnees_sans_commandes(self):
        tr = Transport([{"code": 200, "data": {"token": "USER_SECRET_TOKEN"}}, {"code": 200, "data": INFO}])
        client = self.client(tr)
        d = client.relever("42")
        self.assertEqual([r.method for r in tr.requests], ["POST", "GET"])
        self.assertEqual(tr.requests[0].full_url, C.API + "/v3/public/loginWithAccessToken")
        self.assertEqual(tr.requests[1].full_url, C.API + "/v2/printer/info?id=42")
        first = tr.requests[0]
        headers = {k.lower(): v for k, v in first.header_items()}
        expected = hashlib.md5(
            (
                "APP_IDENTIFIER"
                + headers["xx-timestamp"]
                + "2.0"
                + "APP_SECRET_VALUE"
                + headers["xx-nonce"]
                + "APP_IDENTIFIER"
            ).encode()
        ).hexdigest()
        self.assertEqual(headers["xx-signature"], expected)
        self.assertNotIn("xx-token", headers)
        self.assertEqual({k.lower(): v for k, v in tr.requests[1].header_items()}["xx-token"], "USER_SECRET_TOKEN")
        self.assertEqual(d["donnees"]["reste_secondes"]["valeur"], 720)
        self.assertEqual(d["donnees"]["duree_totale_secondes"]["valeur"], 7800)
        self.assertEqual(d["donnees"]["buse_c"]["valeur"], 215)
        self.assertIsNone(d["donnees"]["buse_c"]["age_source_secondes"])
        self.assertIsNone(d["donnees"]["buse_c"]["frais"])
        self.assertIsNone(d["ace"][0]["temperature_c"])
        self.assertIsNone(d["ace"][0]["humidity_percent"])
        self.assertIsNone(d["ace"][0]["slots"][0]["remaining_percent"])
        self.assertIsNone(d["ace"][0]["slots"][0]["sku"])
        self.assertFalse(d["commandes_machine"])
        self.assertFalse(d["validation_reelle"])
        self.assertFalse(d["acquisition_reseau"])
        self.assertEqual(d["origine"], "TRANSPORT_INJECTE")
        self.assertIsNone(client._token)
        raw = json.dumps(d)
        for secret in (
            "PRIVATE_ACCOUNT_MACHINE",
            "PRIVATE_IDENTIFIER",
            "PRIVATE_FILENAME",
            "PRIVATE_RFID",
            "ACCESS_SECRET_TOKEN",
            "APP_SECRET_VALUE",
            "USER_SECRET_TOKEN",
        ):
            self.assertNotIn(secret, raw)

    def test_absences_zeros_et_nombres_invalides(self):
        d = C.projeter({"id": 42, "parameter": {"curr_nozzle_temp": 0, "curr_hotbed_temp": False}}, 42)
        self.assertEqual(d["donnees"]["buse_c"]["valeur"], 0)
        self.assertIsNone(d["donnees"]["plateau_c"]["valeur"])
        self.assertIsNone(d["donnees"]["progression_pourcent"]["valeur"])
        self.assertIsNone(d["donnees"]["connectee"]["valeur"])
        self.assertFalse(d["validation_reelle"])
        self.assertEqual(d["statut"], "INSTANTANE_FOURNI")

    def test_mauvaise_machine_refusee(self):
        for detail in ({"id": 43}, {}, {"id": True}, [], None):
            with self.assertRaises(C.CloudErreur):
                C.projeter(detail, 42)
        for wanted in (True, 0, -1, "../42", "42&start=1", "42\n", 2**64):
            tr = Transport([])
            with self.assertRaises(C.CloudErreur):
                self.client(tr).relever(wanted)
            self.assertEqual(tr.requests, [])

    def test_messages_et_reflexion_secret_masques(self):
        info = {**INFO, "version": {"firmware_version": "ACCESS_SECRET_TOKEN"}}
        tr = Transport([{"code": 1, "data": {"token": "USER_SECRET_TOKEN"}}, {"code": 1, "data": info}])
        d = self.client(tr).relever(42)
        self.assertNotIn("ACCESS_SECRET_TOKEN", json.dumps(d))
        tr = Transport([{"code": 401, "msg": "ACCESS_SECRET_TOKEN PRIVATE_IDENTIFIER", "data": {}}])
        with self.assertRaises(C.CloudErreur) as caught:
            self.client(tr).relever(42)
        self.assertNotIn("ACCESS_SECRET_TOKEN", str(caught.exception))

    def test_route_et_redirection_interdites(self):
        tr = Transport([])
        client = self.client(tr)
        for route in ("/start", "https://evil.test", "/v2/printer/info?start=1"):
            with self.assertRaises(C.CloudErreur):
                client._requete(route)
        self.assertEqual(tr.requests, [])
        req = urllib.request.Request(C.API + "/v2/printer/info")
        with self.assertRaises(C.CloudErreur):
            C.SansRedirection().redirect_request(req, None, 302, "", {}, "https://evil.test")
        with self.assertRaises(C.CloudErreur):
            C._octets(req, lambda *a, **kw: Response(b"{}", "https://evil.test"))

    def test_erreurs_bornees_pas_reessaie(self):
        req = urllib.request.Request(C.API + "/v2/printer/info")
        for response in (
            Response(b"x" * (C.LIMIT + 1), req.full_url),
            Response(b"{}", req.full_url, status=401),
            Response(b"{}", req.full_url, headers={"Content-Length": "9999999"}),
        ):
            with self.assertRaises(C.CloudErreur):
                C._octets(req, lambda *a, **kw: response)
        tr = Transport([OSError("PRIVATE TOKEN")])
        with self.assertRaises(C.CloudErreur) as caught:
            self.client(tr).relever(42)
        self.assertEqual(len(tr.requests), 1)
        self.assertEqual(str(caught.exception), "CLOUD_INDISPONIBLE")

    def test_json_schema_strict_et_secret_expire(self):
        for body in (b'{"data":{},"data":{}}', b'{"data":{"x":NaN}}', b"[]", b"{bad", b'{"code":200,"data":[]}'):
            tr = Transport([body])
            with self.assertRaises(C.CloudErreur):
                self.client(tr).relever(42)
            self.assertEqual(len(tr.requests), 1)
        tr = Transport([{"data": {}}])
        with self.assertRaises(C.CloudErreur):
            self.client(tr).relever(42)
        self.assertEqual(len(tr.requests), 1)

    def test_identite_publique_non_executee_et_empreinte(self):
        with self.assertRaises(C.CloudErreur):
            C.identite_application(Transport([b"import os; raise SystemExit()"]))
        source = (
            b'AC_KNOWN_AID="FAKE_PUBLIC_AID"\n'
            b'AC_KNOWN_SEC="FAKE_PUBLIC_SECRET"\n'
            b'AC_KNOWN_VID_SLICER_NEXT="2.0"\nraise SystemExit()\n'
        )
        with patch.object(C, "PUBLIC_SHA256", hashlib.sha256(source).hexdigest()):
            values = C.identite_application(Transport([source]))
        self.assertEqual(values["version"], "2.0")

    def test_config_slicer_non_lue_par_etat(self):
        from benchab.machine import session_compte as S

        with (
            patch.object(C, "_local_json", side_effect=AssertionError("lecture interdite")),
            patch.object(S, "etat", return_value={"statut": "CONFIGURATION_COMPTE_REQUISE"}),
        ):
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(C.etat()["statut"], "AUTHENTIFICATION_REQUISE")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td).resolve() / "slicer.conf"
            p.write_text(json.dumps({"anycubic_cloud": {"encoded": "SECRET_ACCOUNT_VALUE"}}))
            d = C.session_slicer(p)
            self.assertEqual(d["statut"], "SESSION_OPAQUE_EXTRACTION_EXPLICITE_REQUISE")
            self.assertFalse(d["dechiffrement_effectue"])
            self.assertNotIn("SECRET_ACCOUNT_VALUE", json.dumps(d))
            with self.assertRaises(C.CloudErreur):
                C.token_slicer(p)
            p.write_text(json.dumps({"anycubic_cloud": {"access_token": "SYNTHETIC_ACCESS_TOKEN"}}))
            self.assertTrue(C.session_slicer(p)["reutilisable"])
            self.assertEqual(C.token_slicer(p), "SYNTHETIC_ACCESS_TOKEN")
            q = Path(td).resolve() / "link"
            q.symlink_to(p)
            self.assertFalse(C.session_slicer(q)["reutilisable"])

    def test_cli_hors_ligne_et_absence_jeton_sans_reseau(self):
        with patch.object(C, "_opener", side_effect=AssertionError("reseau interdit")):
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(C.main([]), 0)
                self.assertEqual(C.main(["--cloud", "--machine", "42"]), 2)
            self.assertNotIn("SECRET", stream.getvalue())
        with tempfile.TemporaryDirectory() as td:
            p = Path(td).resolve() / "instantane.json"
            p.write_text(json.dumps({"data": INFO}))
            with contextlib.redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(C.main(["--instantane", str(p), "--machine", "42"]), 0)
            self.assertEqual(json.loads(stream.getvalue())["origine"], "INSTANTANE_LOCAL_NON_AUTHENTIFIE")

    def test_liste_machines_route_champs_et_selection(self):
        rows = [
            {
                "id": "42",
                "machine_type": 20025,
                "model": "PRIVATE_MODEL_NAME",
                "name": "PRIVATE_MACHINE_NAME",
                "key": "PRIVATE_DEVICE_KEY",
                "device_status": 1,
            },
            {"id": 43, "machine_type": True, "name": "PRIVATE_MACHINE_NAME", "device_status": False},
        ]
        tr = Transport([{"code": 1, "data": {"token": "USER_SECRET_TOKEN"}}, {"code": 1, "data": rows}])
        client = self.client(tr)
        d = client.lister_machines()
        self.assertEqual(tr.requests[1].full_url, C.API + "/work/printer/getPrinters")
        self.assertEqual(tr.requests[1].method, "GET")
        self.assertIsNone(tr.requests[1].data)
        self.assertEqual({k.lower(): v for k, v in tr.requests[1].header_items()}["xx-token"], "USER_SECRET_TOKEN")
        self.assertEqual(d["statut"], "CHOISIR_MACHINE")
        self.assertEqual(d["machines"][0]["id"], 42)
        self.assertEqual(d["machines"][0]["modele_id"], 20025)
        self.assertEqual(d["machines"][0]["modele"], "Anycubic Kobra S1")
        self.assertIsNone(d["machines"][1]["modele_id"])
        self.assertIsNone(d["machines"][1]["connectee"])
        self.assertFalse(d["validation_reelle"])
        self.assertFalse(d["commandes_machine"])
        self.assertIsNone(client._token)
        self.assertNotIn("PRIVATE", json.dumps(d))
        self.assertNotIn("USER_SECRET_TOKEN", json.dumps(d))

    def test_liste_machines_invalide_ou_vide(self):
        for rows in (
            {"id": 42},
            [None],
            [{"id": "42/start"}],
            [{"id": 42}, {"id": 42}],
            [{"id": True}],
            [{"id": i + 1} for i in range(101)],
        ):
            tr = Transport([{"code": 1, "data": {"token": "USER_SECRET_TOKEN"}}, {"code": 1, "data": rows}])
            client = self.client(tr)
            with self.assertRaises(C.CloudErreur):
                client.lister_machines()
            self.assertIsNone(client._token)
            self.assertEqual(len(tr.requests), 2)
        tr = Transport([{"code": 1, "data": {"token": "USER_SECRET_TOKEN"}}, {"code": 1, "data": []}])
        self.assertEqual(self.client(tr).lister_machines()["statut"], "AUCUNE_MACHINE_RAPPORTEE")

    def test_helper_jeton_ui_n_extrait_rien_et_ne_conserve_rien(self):
        identity = {"app_id": "APP_IDENTIFIER", "app_secret": "APP_SECRET_VALUE", "version": "2.0"}
        tr = Transport(
            [
                {"code": 1, "data": {"token": "USER_SECRET_TOKEN"}},
                {"code": 1, "data": [{"id": 42, "machine_type": 20025}]},
            ]
        )
        with patch.object(C, "identite_application", return_value=identity) as load:
            d = C.relever_avec_jeton("ACCESS_SECRET_TOKEN", opener=tr, clock=lambda: NOW)
            load.assert_called_once_with(tr)
        self.assertEqual(d["machines"][0]["id"], 42)
        tr = Transport([{"code": 1, "data": {"token": "USER_SECRET_TOKEN"}}, {"code": 1, "data": INFO}])
        d = C.relever_avec_jeton("ACCESS_SECRET_TOKEN", "42", opener=tr, identite=identity, clock=lambda: NOW)
        self.assertEqual(d["donnees"]["couche_courante"]["valeur"], 10)
        with patch.object(C, "identite_application", side_effect=AssertionError("reseau interdit")):
            for token, machine in ((None, None), ("", 42), ("ACCESS_SECRET_TOKEN", "42&start=1")):
                with self.assertRaises(C.CloudErreur):
                    C.relever_avec_jeton(token, machine)
        with self.assertRaises(C.CloudErreur):
            C.relever_avec_jeton("ACCESS_SECRET_TOKEN", identite={"version": "2.0"})

    def test_credentials_interdisent_entetes_injectes(self):
        for invalid in ("", None, "abc\r\nXX-Token: steal", "clé-unicode"):
            with self.assertRaises(C.CloudErreur):
                C.CloudLecture(invalid, "AID", "APP_SECRET", "2.0")
        import pickle

        with self.assertRaises(TypeError):
            pickle.dumps(self.client(Transport([])))


if __name__ == "__main__":
    unittest.main()
