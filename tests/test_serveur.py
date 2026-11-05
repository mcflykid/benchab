"""Serveur des visionneuses (27/10/2026) : accueil listant les pages, et reconnaissance stricte de notre serveur."""

import http.server
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from benchab.visualisation import serveur as S


def lancer(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Serveur(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        r = Path(self.t.name)
        S.RACINE = r
        (r / "projets/lampe/preuves/visualisation").mkdir(parents=True)
        (r / "projets/lampe/preuves/visualisation/projet-3d.html").write_text("<title>x</title>")
        self.srv = lancer(S.Gestion)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.t.cleanup()

    def test_accueil_liste_les_pages(self):
        b = urllib.request.urlopen(f"http://127.0.0.1:{self.srv.server_port}/").read().decode()
        self.assertIn("Visionneuses 3D", b)
        self.assertIn('href="/projets/lampe/preuves/visualisation/projet-3d.html"', b)
        self.assertIn("lampe — projet-3d", b)

    def test_identite_get_head_sans_ouvrir_docs(self):
        r = Path(self.t.name)
        d = r / "docs/identite"
        d.mkdir(parents=True)
        image = d / "benchab3d-icone.png"
        image.write_bytes(b"PNG-test")
        (d / "secret.png").write_bytes(b"secret")
        base = f"http://127.0.0.1:{self.srv.server_port}"
        for methode in ("GET", "HEAD"):
            with urllib.request.urlopen(
                urllib.request.Request(base + "/docs/identite/benchab3d-icone.png", method=methode)
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Content-Type"], "image/png")
                self.assertEqual(response.read(), b"PNG-test" if methode == "GET" else b"")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(base + "/docs/identite/secret.png", method=methode))
            self.assertEqual(caught.exception.code, 403)
        image.unlink()
        image.symlink_to(d / "secret.png")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(base + "/docs/identite/benchab3d-icone.png")
        self.assertEqual(caught.exception.code, 403)

    def test_actif_reconnait_seulement_notre_serveur(self):
        self.assertTrue(S.actif(self.srv.server_port))
        autre = lancer(http.server.SimpleHTTPRequestHandler)
        try:
            self.assertFalse(S.actif(autre.server_port))
        finally:
            autre.shutdown()
            autre.server_close()

    def test_reemploi_serveur_exige_meme_racine_pas_seulement_meme_nom(self):
        """Une autre base, même de nom identique, est refusée avant navigateur/Popen ; aucun fichier projet ouvert."""
        from unittest.mock import patch

        racine = Path(self.t.name).resolve()
        autre = racine / "autre" / racine.name
        self.assertTrue(S.actif(self.srv.server_port, racine))
        self.assertFalse(S.actif(self.srv.server_port, autre))
        with patch.object(S, "ouvrir") as ouvrir, patch.object(S.subprocess, "Popen") as demarrer:
            with self.assertRaisesRegex(ValueError, "occupé par un autre service"):
                S.main(["--ouvrir", "/atelier", "--port", str(self.srv.server_port)], prive=autre)
            ouvrir.assert_not_called()
            demarrer.assert_not_called()

    def test_vendor_historique_alias_limite_aux_modules_verifies(self):
        """Les anciennes routes servent les mêmes JS vérifiés ; version inconnue ou autre outil reste403."""
        base = f"http://127.0.0.1:{self.srv.server_port}"
        suffixe = "vendor/three-0.160.0/build/three.module.js"
        with urllib.request.urlopen(base + "/outils/visualisation-3d/" + suffixe) as reponse:
            ancien = reponse.read()
        with urllib.request.urlopen(base + "/benchab/visualisation/" + suffixe) as reponse:
            self.assertEqual(ancien, reponse.read())
        for chemin in (
            "/outils/maintenance/coherence.py", "/outils/visualisation-3d/vendor/inconnue/build/three.module.js"
        ):
            with self.assertRaises(urllib.error.HTTPError) as refus:
                urllib.request.urlopen(base + chemin)
            self.assertEqual(refus.exception.code, 403)

    def test_ouverture_atelier_et_page_avec_espaces(self):
        root = Path(self.t.name).resolve()
        self.assertEqual(S.adresse_ouverture("/atelier", 8770, root), "http://127.0.0.1:8770/atelier")
        self.assertEqual(S.adresse_ouverture("/", 8770, root), "http://127.0.0.1:8770/")
        self.assertEqual(
            S.adresse_ouverture(str(root / "projets/été avec espaces.html"), 8770, root),
            "http://127.0.0.1:8770/projets/été avec espaces.html",
        )
        with self.assertRaises(ValueError):
            S.adresse_ouverture("/etc/passwd", 8770, root)

    def test_dependance_absente_reponse_503_et_recuperation(self):
        """Dépendance absente : HTTP503 et commande actuelle, sans détail privé ni tentative de réseau."""
        import builtins
        from unittest.mock import patch

        importer = builtins.__import__

        def sans_geometrie(name, *args, **kwargs):
            if name in (
                "benchab.visualisation.catalogue_visuel",
                "benchab.visualisation.atelier_local",
                "benchab.visualisation.paquet_inspection",
            ):
                raise ModuleNotFoundError("détail privé à ne pas exposer", name="numpy")
            return importer(name, *args, **kwargs)

        base = f"http://127.0.0.1:{self.srv.server_port}"
        with patch("builtins.__import__", side_effect=sans_geometrie):
            for route in (
                "/",
                "/atelier",
                "/inspecter?projet=lampe",
                "/couches?projet=lampe",
                "/paquet-inspection?projet=lampe",
            ):
                for methode in ("GET", "HEAD"):
                    with self.subTest(route=route, methode=methode):
                        with self.assertRaises(urllib.error.HTTPError) as caught:
                            urllib.request.urlopen(urllib.request.Request(base + route, method=methode))
                        self.assertEqual(caught.exception.code, 503)
                        body = caught.exception.read().decode()
                        if methode == "GET":
                            self.assertIn("numpy", body)
                            self.assertIn("uv run benchab visualisation serveur -- --ouvrir /atelier", body)
                            self.assertIn('href="/atelier"', body)
                            self.assertNotIn("détail privé", body)
                            self.assertNotIn("Traceback", body)
                        else:
                            self.assertEqual(body, "")
        with urllib.request.urlopen(base + "/__visionneuse") as response:
            self.assertEqual(response.status, 200)


class AuditHTTP(Serveur):
    def test_fichiers_prives_et_listings_interdits_get_head(self):
        import urllib.error

        root = Path(self.t.name)
        (root / ".git").mkdir()
        (root / ".git/config").write_text("secret")
        (root / "sauvegardes").mkdir()
        (root / "sauvegardes/prive.json").write_text("secret")
        (root / "AGENTS.md").write_text("private")
        for method in ("GET", "HEAD"):
            for path in ("/.git/config", "/AGENTS.md", "/sauvegardes/prive.json", "/projets/", "/%2e%2e/AGENTS.md"):
                with self.subTest(method=method, path=path):
                    req = urllib.request.Request(f"http://127.0.0.1:{self.srv.server_port}" + path, method=method)
                    with self.assertRaises(urllib.error.HTTPError) as e:
                        urllib.request.urlopen(req)
                    self.assertEqual(e.exception.code, 403)

    def test_head_lien_vers_exterieur_interdit(self):
        import urllib.error

        root = Path(self.t.name)
        (root / "leak").symlink_to("/etc/hosts")
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.server_port}/leak", method="HEAD")
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 403)

    def test_page_valide_get_head_et_host_refuse(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.srv.server_port}/projets/lampe/preuves/visualisation/projet-3d.html", method="HEAD"
        )
        with urllib.request.urlopen(req) as response:
            self.assertEqual(response.read(), b"")
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.server_port}/", headers={"Host": "evil.invalid"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 403)


class AutomatisationHTTP(unittest.TestCase):
    setUp = Serveur.setUp
    tearDown = Serveur.tearDown

    def requete(self, path, data, origin=True):
        import http.client
        import json

        base = f"http://127.0.0.1:{self.srv.server_port}"
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        headers = {"Content-Type": "application/json", "Origin": base if origin else "https://example.invalid"}
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port, timeout=10)
        try:
            c.request("POST", path, raw, headers)
            with c.getresponse() as response:
                return (response.status, json.loads(response.read()))
        finally:
            c.close()

    def metadata(self):
        import json

        (Path(self.t.name) / "projets/lampe/qualite.json").write_text(
            json.dumps(
                {
                    "suivi": {
                        "schema": 1,
                        "type": "conception",
                        "statut": "EN_PREPARATION",
                        "physique": {"imprime": False, "resultat": "inconnu"},
                    }
                }
            )
        )

    def test_cloud_compte_liste_puis_selection_sans_secret_navigateur(self):
        from unittest.mock import patch

        from benchab.machine import cloud_lecture as C

        with (
            patch.object(
                C,
                "relever_avec_compte",
                return_value={"statut": "CHOISIR_MACHINE", "machines": [{"id": 42, "modele": "Anycubic Kobra S1"}]},
            ) as account,
            patch.object(C, "token_slicer", side_effect=AssertionError()),
            patch.object(C, "relever_avec_jeton", side_effect=AssertionError()),
        ):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.srv.server_port}/atelier?vue=cloud") as response:
                self.assertEqual(response.status, 200)
            account.assert_not_called()
            status, body = self.requete("/__cloud_lecture", {"compte": True, "machine": None})
            self.assertEqual(status, 200)
            account.assert_called_once_with(None)
            self.assertEqual(body["machines"][0]["id"], 42)
            self.assertEqual(self.requete("/__cloud_lecture", {"compte": True, "machine": "42"})[0], 200)
            account.assert_called_with(42)

    def test_cloud_compte_premiere_configuration_humaine_explicite(self):
        """Demande explicite sans compte : HTTP409 et commande BENCHAB, jamais de mot de passe transmis à la page."""
        from unittest.mock import patch

        from benchab.machine import cloud_lecture as C

        with patch.object(C, "relever_avec_compte", side_effect=C.CloudErreur("CONFIGURATION_COMPTE_REQUISE")):
            status, body = self.requete("/__cloud_lecture", {"compte": True})
        self.assertEqual(status, 409)
        self.assertEqual(body["statut"], "CONFIGURATION_COMPTE_REQUISE")
        self.assertEqual(body["configuration_command"], "uv run benchab machine session-compte -- --configurer")
        self.assertNotIn("password", str(body))

    def test_cloud_compte_refuse_identifiants_chemins_modes_et_json_ambigus(self):
        from unittest.mock import patch

        from benchab.machine import cloud_lecture as C

        demandes = [{"compte": v} for v in [1, "true", None]]
        demandes += [
            {"compte": True, "jeton": None},
            {"compte": True, "session_slicer": False},
            {"compte": True, "email": "@".join(("fixture", "example.test"))},
            {"compte": True, "password": "fictional"},
            {"compte": True, "chemin": "/tmp/account"},
            {"compte": True, "machine": 0},
            b'{"compte":true,"compte":false}',
        ]
        with (
            patch.object(C, "relever_avec_compte", side_effect=AssertionError()),
            patch.object(C, "token_slicer", side_effect=AssertionError()),
            patch.object(C, "relever_avec_jeton", side_effect=AssertionError()),
        ):
            for d in demandes:
                self.assertEqual(self.requete("/__cloud_lecture", d)[0], 400)
            self.assertEqual(self.requete("/__cloud_lecture", {"compte": True}, False)[0], 403)

    def test_action_fermee_origine_et_champs(self):
        self.metadata()
        for data in (
            {"action": "imprimer", "projet": "lampe"},
            {"action": "executer", "projet": "../lampe"},
            {"action": "executer", "projet": "lampe", "sortie": "/tmp/indesirable"},
        ):
            self.assertEqual(self.requete("/__automatisation", data)[0], 400)
        self.assertEqual(self.requete("/__automatisation", {"action": "executer", "projet": "lampe"}, False)[0], 403)

    def test_dispatch_borne_sans_commande_arbitraire(self):
        from unittest.mock import patch

        from benchab.organisation import automatisation as A

        self.metadata()
        with patch.object(
            A,
            "executer",
            return_value={
                "statut": "PASS",
                "etapes": {"resume": {"statut": "PASS", "entrees": {"prive": "non affiche"}}},
            },
        ) as call:
            status, body = self.requete(
                "/__automatisation", {"action": "executer", "projet": "lampe", "preparation": "P01"}
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["statut"], "PASS")
        self.assertNotIn("entrees", body["etapes"]["resume"])
        self.assertEqual(call.call_count, 1)

    def test_profondeur_json_et_taille_refusees(self):
        deep = b'{"action":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}"
        self.assertEqual(self.requete("/__automatisation", deep)[0], 400)
        self.assertEqual(self.requete("/__cloud_lecture", deep)[0], 400)
        self.assertEqual(self.requete("/__automatisation", b"x" * 4097)[0], 413)

    def test_cloud_get_sans_connexion_et_jeton_non_restitue(self):
        from unittest.mock import patch

        from benchab.machine import cloud_lecture as C

        with patch.object(C, "relever_avec_jeton", return_value={"statut": "CHOISIR_MACHINE", "machines": []}) as call:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.srv.server_port}/atelier?vue=cloud") as response:
                self.assertEqual(response.status, 200)
            call.assert_not_called()
            status, body = self.requete("/__cloud_lecture", {"jeton": "secret-test", "machine": None})
            self.assertEqual(status, 200)
            self.assertNotIn("secret-test", str(body))
            self.assertEqual(call.call_count, 1)
        self.assertEqual(self.requete("/__cloud_lecture", {"jeton": "x"}, False)[0], 403)

    def test_session_locale_uniquement_sur_demande_explicite(self):
        from unittest.mock import patch

        from benchab.machine import cloud_lecture as C

        with (
            patch.object(C, "token_slicer", return_value="secret-session-fixture") as extraction,
            patch.object(
                C,
                "relever_avec_jeton",
                return_value={"statut": "CHOISIR_MACHINE", "machines": [{"id": 1, "modele": "Anycubic Kobra S1"}]},
            ) as lecture,
        ):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.srv.server_port}/atelier?vue=cloud") as response:
                self.assertEqual(response.status, 200)
            extraction.assert_not_called()
            lecture.assert_not_called()
            status, body = self.requete("/__cloud_lecture", {"session_slicer": True, "machine": None})
            self.assertEqual(status, 200)
            self.assertNotIn("secret-session-fixture", str(body))
            extraction.assert_called_once_with(dechiffrer=True)
            lecture.assert_called_once_with("secret-session-fixture", None)
            status, _ = self.requete("/__cloud_lecture", {"session_slicer": True, "machine": "1"})
            self.assertEqual(status, 200)
            lecture.assert_called_with("secret-session-fixture", 1)

    def test_session_locale_refuse_melange_chemin_flag_et_machine_invalides(self):
        from unittest.mock import patch

        from benchab.machine import cloud_lecture as C

        demandes = (
            {"session_slicer": 1},
            {"session_slicer": "true"},
            {"session_slicer": None},
            {"session_slicer": True, "jeton": None},
            {"session_slicer": True, "jeton": "x"},
            {"session_slicer": True, "machine": 0},
            {"session_slicer": True, "path": "/tmp/session"},
        )
        with patch.object(C, "token_slicer") as extraction, patch.object(C, "relever_avec_jeton") as lecture:
            for demande in demandes:
                self.assertEqual(self.requete("/__cloud_lecture", demande)[0], 400)
            self.assertEqual(self.requete("/__cloud_lecture", {"session_slicer": True}, False)[0], 403)
            extraction.assert_not_called()
            lecture.assert_not_called()
        with (
            patch.object(C, "token_slicer") as extraction,
            patch.object(
                C, "relever_avec_jeton", return_value={"statut": "CHOISIR_MACHINE", "machines": []}
            ) as lecture,
        ):
            self.assertEqual(
                self.requete("/__cloud_lecture", {"session_slicer": False, "jeton": "secret-direct-fixture"})[0], 200
            )
            extraction.assert_not_called()
            lecture.assert_called_once_with("secret-direct-fixture", None)


if __name__ == "__main__":
    unittest.main()
