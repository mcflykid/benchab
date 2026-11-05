"""Contrats lus, preuves autorisées, refus de chemins et mutations inexistantes."""

import hashlib
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from test_serveur import S, lancer

from benchab.visualisation.atelier_local import lire_source, page
from benchab.visualisation.fixtures_atelier import creer


class Atelier(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="atelier été ")
        self.addCleanup(t.cleanup)
        self.root = creer(Path(t.name) / "sources")
        self.projet = self.root / "projets/inspection-ete"

    def empreintes(self):
        return {
            str(f.relative_to(self.root)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in self.root.rglob("*")
            if f.is_file()
        }

    def test_actif_historique_recherche_piece_sans_mutation(self):
        avant = self.empreintes()
        h = page(self.root, {"q": "BaseB"}).decode()
        self.assertIn("Inspection été", h)
        self.assertNotIn("Lune ancienne", h)
        h = page(self.root, {"projet": "ancienne-lune"}).decode()
        self.assertIn("HISTORIQUE", h)
        self.assertIn("aucune géométrie déclarée", h)
        self.assertIn("Déclaration", page(self.root, {"projet": "ancienne-lune", "preuve": "0"}).decode())
        self.assertEqual(avant, self.empreintes())

    def test_preparations_comparees_par_declarations_et_contrat_readonly(self):
        avant = self.empreintes()
        h = page(self.root, {"projet": "inspection-ete", "a": "P01", "b": "P02", "controle": "contrat"}).decode()
        for x in ("PASS — contrat", "P01 → P02", "parametres", "pas de géométrie", "candidate NON imprimée"):
            self.assertIn(x, h)
        self.assertEqual(avant, self.empreintes())
        with self.assertRaises(ValueError):
            page(self.root, {"projet": "inspection-ete", "a": "../qualite.json"})

    def test_preuve_lue_pas_evaluee_et_source_sha(self):
        f = self.projet / "preuves/controle.json"
        f.write_text('{"statut":"WARNING","texte":"</pre><script>alert(1)</script>"}')
        h = page(self.root, {"projet": "inspection-ete", "preuve": "0"}).decode()
        self.assertIn("&lt;script&gt;", h)
        self.assertNotIn("<script>alert", h)
        self.assertIn(hashlib.sha256(f.read_bytes()).hexdigest(), h)
        with self.assertRaises(ValueError):
            page(self.root, {"projet": "inspection-ete", "preuve": "-1"})
        with self.assertRaises(ValueError):
            page(self.root, {"projet": "inspection-ete", "preuve": "999"})

    def test_liens_vers_controleurs_existants_sans_nouveau_registre(self):
        h = page(self.root, {"projet": "inspection-ete"}).decode()
        for x in (
            "/inspecter?projet=inspection-ete",
            "/paquet-inspection?projet=inspection-ete",
            "/couches?projet=inspection-ete",
            "/projets/inspection-ete/preuves/visualisation/projet-3d.html",
        ):
            self.assertIn(x, h)
        self.assertIn("ne modifient pas le statut physique", h)

    def test_connaissances_sourcees_recherche_limites(self):
        h = page(self.root, {"vue": "connaissances", "q": "base"}).decode()
        for x in ("Base théorique", "CALCUL_NUMERIQUE", "Ne valide pas le contact réel", "K-SYNTH"):
            self.assertIn(x, h)
        self.assertIn("0 résultat", page(self.root, {"vue": "connaissances", "q": "inexistant"}).decode())

    def test_cloud_formulaire_session_locale_explicite_sans_lecture_get(self):
        from benchab.machine import cloud_lecture as C

        with patch.object(C, "token_slicer") as extraction, patch.object(C, "relever_avec_jeton") as lecture:
            body = page(self.root, {"vue": "cloud"}).decode()
            extraction.assert_not_called()
            lecture.assert_not_called()
        for attendu in (
            "Lire ma session locale",
            'id="cloud-session-button" type="button"',
            "demande.session_slicer=true",
            'local.addEventListener("click"',
            "machine:machine.value||null",
            "attendre quelques secondes",
        ):
            self.assertIn(attendu, body)
        self.assertNotIn("localStorage", body)
        self.assertNotIn("sessionStorage", body)

    def test_cloud_compte_prioritaire_sans_motdepasse_compte_dans_page(self):
        """Montre la commande BENCHAB actuelle sans lire le compte ni contacter le Cloud ; aucun statut physique."""
        from benchab.machine import cloud_lecture as C
        from benchab.machine import session_compte as S

        with (
            patch.object(C, "relever_avec_compte", side_effect=AssertionError()),
            patch.object(S, "_read", side_effect=AssertionError()),
        ):
            body = page(self.root, {"vue": "cloud"}).decode()
        for expected in (
            'id="cloud-account-button" type="button"',
            "demande.compte=true",
            "uv run benchab machine session-compte -- --configurer",
            'account.addEventListener("click"',
        ):
            self.assertIn(expected, body)
        self.assertLess(body.index("cloud-account-button"), body.index('id="cloud-form"'))
        for forbidden in ('name="password"', 'type="email"', "localStorage", "sessionStorage"):
            self.assertNotIn(forbidden, body)

    def test_projet_illisible_ne_masque_pas_les_autres(self):
        (self.root / "projets/ancienne-lune/qualite.json").write_text("{incomplet")
        h = page(self.root, {}).decode()
        self.assertIn("Inspection été", h)
        self.assertIn("Métadonnées illisibles", h)

    def test_taille_traversal_symlink_et_fichier_non_regulier_refuses(self):
        for rel in ("../a", "/etc/passwd", "projets\\x"):
            with self.assertRaises(ValueError):
                lire_source(self.root, rel)
        f = self.projet / "preuves/grand.json"
        f.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        with self.assertRaises(ValueError):
            lire_source(self.root, str(f.relative_to(self.root)))
        link = self.projet / "preuves/lien.json"
        link.symlink_to(f)
        with self.assertRaises(ValueError):
            lire_source(self.root, str(link.relative_to(self.root)))
        with self.assertRaises((ValueError, OSError)):
            lire_source(self.root, "projets")

    def test_source_modifiee_pendant_lecture_refusee(self):
        from benchab.visualisation import atelier_local as A

        original = A.os.fstat
        calls = []

        def stat_(fd):
            s = original(fd)
            calls.append(s)
            if len(calls) == 2:
                from types import SimpleNamespace

                return SimpleNamespace(st_size=s.st_size, st_mtime_ns=s.st_mtime_ns + 1, st_ctime_ns=s.st_ctime_ns)
            return s

        with patch.object(A.os, "fstat", side_effect=stat_):
            with self.assertRaisesRegex(ValueError, "modifié"):
                lire_source(self.root, "projets/inspection-ete/qualite.json")

    def test_serveur_bornes_head_erreurs_et_acces_interdits(self):
        avant = self.empreintes()
        old = S.RACINE
        S.RACINE = self.root
        srv = lancer(S.Gestion)
        try:
            base = f"http://127.0.0.1:{srv.server_port}"
            with urllib.request.urlopen(base + "/atelier") as r:
                self.assertIn("Inspection été", r.read().decode())
            with urllib.request.urlopen(urllib.request.Request(base + "/atelier", method="HEAD")) as r:
                self.assertEqual(r.read(), b"")
            for url in ("/atelier?projet=..", "/atelier?projet=inspection-ete&preuve=99", "/atelier?q=" + "a" * 201):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(base + url)
                self.assertEqual(ctx.exception.code, 400)
                self.assertIn(b"Revenir aux projets", ctx.exception.read())
                ctx.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(base + "/projets/inspection-ete/qualite.json")
            self.assertEqual(ctx.exception.code, 403)
            ctx.exception.close()
            self.assertEqual(avant, self.empreintes())
        finally:
            srv.shutdown()
            srv.server_close()
            S.RACINE = old


if __name__ == "__main__":
    unittest.main()
