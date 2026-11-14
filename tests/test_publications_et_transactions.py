"""Publication exclusive et nettoyage d'échecs sans toucher aux fichiers tiers."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchab.qualite.ecritures import PublicationNeuve, valider_chemins


class Publications(unittest.TestCase):
    """Publications privées synthétiques ; aucune cible tierce ni source réelle modifiée."""

    def setUp(self):
        """Crée des fichiers synthétiques temporaires ; sans unité ni effet global."""
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.d = Path(self.t.name).resolve()
        self.src = self.d / "src"
        self.src.write_bytes(b"original")
        self.a = self.d / "a.stl"
        self.b = self.d / "b.json"

    def test_collisions_and_existing_files(self):
        """Collision FAIL avant écriture ; fichiers préexistants inchangés."""
        for destinations in ([self.src], [self.a, self.d / "x/../a.stl"]):
            with self.assertRaises(ValueError):
                valider_chemins([self.src], destinations)
        self.a.write_bytes(b"user")
        with self.assertRaises(ValueError):
            valider_chemins([self.src], [self.a])
        self.assertEqual(self.a.read_bytes(), b"user")

    def test_symlinks_source_output_and_parent(self):
        """Liens et parents liés FAIL ; aucune cible traversée ni modifiée."""
        link = self.d / "link"
        link.symlink_to(self.src)
        dead = self.d / "dead"
        dead.symlink_to(self.d / "absent")
        folder = self.d / "folder"
        folder.symlink_to(self.d, target_is_directory=True)
        for entries, outputs in (
            ([link], [self.a]),
            ([self.src], [link]),
            ([self.src], [dead]),
            ([self.src], [folder / "new"]),
            ([self.src], [folder / ".." / "escape"]),
        ):
            with self.assertRaises(ValueError):
                valider_chemins(entries, outputs)
        self.assertEqual(self.src.read_bytes(), b"original")

    def test_success_complete_files(self):
        """Publication complète uniquement ; octets exacts, aucun réglage d’impression."""
        with PublicationNeuve([self.src], [self.a, self.b]) as p:
            p.temporaire(self.a).write_bytes(b"stl")
            p.temporaire(self.b).write_bytes(b"json")
            self.assertFalse(self.a.exists())
            p.publier()
        self.assertEqual(self.a.read_bytes(), b"stl")
        self.assertEqual(self.b.read_bytes(), b"json")
        self.assertFalse(list(self.d.glob(".publication-*")))

    def test_write_interruption_no_publication(self):
        """Interruption FAIL sans fichier partiel publié ; sources conservées."""
        with self.assertRaises(KeyboardInterrupt):
            with PublicationNeuve([self.src], [self.a]) as p:
                p.temporaire(self.a).write_bytes(b"partial")
                raise KeyboardInterrupt()
        self.assertFalse(self.a.exists())
        self.assertFalse(list(self.d.glob(".publication-*")))

    def test_second_publish_error_rolls_back_only_own_file(self):
        """Échec du second lien : seuls les fichiers créés par la transaction sont retirés."""
        link = os.link

        def fail_second(src, dst, **kw):
            if Path(dst) == self.b:
                raise OSError("simulated second publication failure")
            return link(src, dst, **kw)

        with PublicationNeuve([self.src], [self.a, self.b]) as p:
            p.temporaire(self.a).write_bytes(b"complete")
            p.temporaire(self.b).write_bytes(b"json")
            with patch("benchab.qualite.ecritures.os.link", side_effect=fail_second):
                with self.assertRaises(OSError):
                    p.publier()
        self.assertFalse(self.a.exists())
        self.assertFalse(self.b.exists())

    def test_concurrent_destination_not_overwritten_or_removed(self):
        """Une cible concurrente reste intacte après refus ; aucune donnée tierce supprimée."""
        link = os.link

        def race(src, dst, **kw):
            if Path(dst) == self.b:
                self.b.write_bytes(b"user")
            return link(src, dst, **kw)

        with PublicationNeuve([self.src], [self.a, self.b]) as p:
            p.temporaire(self.a).write_bytes(b"complete")
            p.temporaire(self.b).write_bytes(b"json")
            with patch("benchab.qualite.ecritures.os.link", side_effect=race):
                with self.assertRaises(FileExistsError):
                    p.publier()
        self.assertFalse(self.a.exists())
        self.assertEqual(self.b.read_bytes(), b"user")

    def test_replacement_of_first_destination_preserved(self):
        """Une première cible remplacée par un tiers reste intacte après échec."""
        link = os.link

        def race(src, dst, **kw):
            if Path(dst) == self.b:
                self.a.unlink()
                self.a.write_bytes(b"user replacement")
                raise OSError("failure")
            return link(src, dst, **kw)

        with PublicationNeuve([self.src], [self.a, self.b]) as p:
            p.temporaire(self.a).write_bytes(b"complete")
            p.temporaire(self.b).write_bytes(b"json")
            with patch("benchab.qualite.ecritures.os.link", side_effect=race):
                with self.assertRaises(OSError):
                    p.publier()
        self.assertEqual(self.a.read_bytes(), b"user replacement")


class TransactionsJSON(unittest.TestCase):
    """Transactions sur JSON jetables ; aucune base utilisateur ni réglage adopté."""

    def test_fiche_et_verrou_fifo_refuses_sans_attente(self):
        """FIFO FAIL immédiatement, sans attente ni modification de registre."""
        from benchab.qualite.ecritures import modifier_json

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "registre.json"
            p.write_text("{}")
            lock = p.with_name(p.name + ".lock")
            os.mkfifo(lock)
            with self.assertRaisesRegex(ValueError, "ordinaire"):
                modifier_json(p, lambda d: d.update(n=1))
            self.assertEqual(p.read_text(), "{}")
            lock.unlink()
            p.unlink()
            os.mkfifo(p)
            with self.assertRaisesRegex(ValueError, "ordinaire"):
                modifier_json(p, lambda d: d.update(n=1))

    def test_verrou_tenu_refuse_puis_acquis_apres_liberation(self):
        """Verrou tenu FAIL au délai en s ; acquisition possible après libération."""
        import subprocess
        import sys

        from benchab.qualite.ecritures import acquerir_verrou

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "verrou"
            p.touch()
            script = ("import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX);"
                      "print('pret',flush=True); sys.stdin.read(1)")
            child = subprocess.Popen(
                [sys.executable, "-c", script, str(p)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
            )
            try:
                self.assertEqual(child.stdout.readline().strip(), "pret")
                with p.open("a") as lock:
                    with self.assertRaisesRegex(ValueError, "délai"):
                        acquerir_verrou(lock, delai=0.05)
                    child.communicate("x", timeout=3)
                    acquerir_verrou(lock, delai=0.05)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()

    def test_mises_a_jour_concurrentes_sans_perte(self):
        """Douze incréments concurrents conservés ; aucune perte de mise à jour ni fichier global."""
        import json
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp).resolve() / "registre.json"
            p.write_text('{"n":0}')
            script = ("from benchab.qualite.ecritures import modifier_json; import sys,time;"
                      "modifier_json(sys.argv[1],lambda d:(time.sleep(.01),d.update(n=d['n']+1)))")
            workers = [
                subprocess.Popen([sys.executable, "-c", script, str(p)], cwd=Path(__file__).parent) for _ in range(12)
            ]
            self.assertEqual([w.wait(timeout=10) for w in workers], [0] * 12)
            self.assertEqual(json.loads(p.read_text())["n"], 12)

    def test_exception_callback_et_modification_externe_preservees(self):
        """Valeur non finie et modification concurrente FAIL, données externes conservées."""
        from benchab.qualite.ecritures import modifier_json

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp).resolve() / "registre.json"
            p.write_text('{"n":0}')

            def invalide(d):
                d.update(n=float("nan"))

            with self.assertRaises(ValueError):
                modifier_json(p, invalide)
            self.assertEqual(p.read_text(), '{"n":0}')

            def externe(d):
                p.write_text('{"n":7}')
                d.update(n=1)

            with self.assertRaises(ValueError):
                modifier_json(p, externe)
            self.assertEqual(p.read_text(), '{"n":7}')

    def test_creation_verrou_transitoire_et_erreur_permanente(self):
        """Création du verrou réessayée avec borne ; échec permanent sans altérer les octets."""
        import json

        from benchab.qualite import ecritures

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp).resolve() / "registre.json"
            p.write_text('{"n":0}')
            original = os.open
            appels = []

            def temporaire(path, *args, **kwargs):
                if str(path).endswith(".lock"):
                    appels.append(path)
                    if len(appels) == 1:
                        raise FileNotFoundError("fenêtre de création simulée")
                return original(path, *args, **kwargs)

            with patch.object(ecritures.os, "open", side_effect=temporaire):
                ecritures.modifier_json(p, lambda d: d.update(n=1))
            self.assertEqual(json.loads(p.read_text())["n"], 1)
            self.assertGreaterEqual(len(appels), 2)
            before = p.read_bytes()
            appels.clear()

            def permanent(path, *args, **kwargs):
                if str(path).endswith(".lock"):
                    appels.append(path)
                    raise FileNotFoundError("erreur persistante")
                return original(path, *args, **kwargs)

            with patch.object(ecritures.os, "open", side_effect=permanent):
                with self.assertRaises(FileNotFoundError):
                    ecritures.modifier_json(p, lambda d: d.update(n=2))
            self.assertEqual(p.read_bytes(), before)
            self.assertLessEqual(len(appels), 8)

    def test_lien_symbolique_refuse(self):
        """Registre lié FAIL sans modifier la cible réelle."""
        from benchab.qualite.ecritures import modifier_json

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp).resolve() / "registre.json"
            p.write_text('{"n":0}')
            link = p.with_name("lien.json")
            link.symlink_to(p)
            with self.assertRaises(ValueError):
                modifier_json(link, lambda d: d.update(n=1))
            self.assertEqual(p.read_text(), '{"n":0}')


if __name__ == "__main__":
    unittest.main()
