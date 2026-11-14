"""Processus synthétiques isolés ; aucun binaire de fabrication ou service réel."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from benchab import processus as P


class Processus(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.r = Path(t.name).resolve()

    def run_cmd(self, code, **kw):
        return P.executer([sys.executable, "-c", code], cwd=self.r, diagnostics=self.r / "diagnostics", **kw)

    def attendre(self, path, process=None):
        fin = time.monotonic() + 5
        while time.monotonic() < fin:
            if path.exists() and path.stat().st_size:
                return
            if process is not None and process.poll() is not None:
                break
            time.sleep(0.01)
        self.fail("signal de démarrage absent : " + str(path))

    def test_arguments_litteraux_cwd_et_fin_normale(self):
        cwd = self.r / "répertoire & espaces"
        cwd.mkdir()
        script = self.r / "script avec espaces.py"
        script.write_text("import json,sys,os;print(json.dumps([sys.argv[1],os.getcwd()]))")
        arg = "a ; $HOME & texte"
        r = P.executer([sys.executable, str(script), arg], cwd=cwd, diagnostics=self.r / "diag")
        self.assertTrue(r.ok)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout), [arg, str(cwd)])
        self.assertIsNone(r.diagnostic)
        self.assertEqual(r.execution, "PASS")
        self.assertEqual(list((self.r / "diag").iterdir()), [])

    def test_erreur_capture_complete_queue_bornee(self):
        r = self.run_cmd("import sys;sys.stderr.write('E'*100000);sys.exit(7)", limite_queue=128)
        self.assertEqual(r.returncode, 7)
        self.assertEqual(r.code_controle, 7)
        self.assertEqual(r.execution, "PASS")
        self.assertEqual(r.resultat, "FAIL")
        self.assertTrue(r.sortie_tronquee)
        self.assertLessEqual(len(r.stderr), 128)
        dossier = Path(r.diagnostic).parent
        self.assertEqual((dossier / "stderr.log").stat().st_size, 100000)
        self.assertEqual(Path(r.diagnostic).stat().st_mode & 0o777, 0o600)

    def test_timeout_tue_aussi_le_descendant(self):
        enfant = self.r / "enfant.pid"
        code = (
            "import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)']);"
            "open('enfant.pid','w').write(str(p.pid));time.sleep(20)"
        )
        r = self.run_cmd(code, delai=0.3, grace=0.1)
        self.assertEqual(r.code_controle, 124)
        self.assertEqual(r.raison, "DELAI")
        self.assertFalse(P._actif(r.pid))
        self.assertTrue(enfant.exists())
        self.assertIn("délai", r.stderr)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(json.loads(Path(r.diagnostic).read_text())["groupe_actif_apres"])

    def test_sans_reponse_sigterm_force_sigkill(self):
        r = self.run_cmd(
            "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(20)", delai=0.5, grace=0.1
        )
        self.assertEqual(r.code_controle, 124)
        self.assertIn("SIGKILL", json.loads(Path(r.diagnostic).read_text())["signaux"])
        self.assertFalse(P._actif(r.pid))

    def test_annulation_et_deux_groupes_independants(self):
        annuler = threading.Event()
        results = {}

        def lent():
            results["lent"] = self.run_cmd(
                "import time;open('pret','w').write('ok');time.sleep(20)", annulation=annuler, grace=0.1
            )

        t = threading.Thread(target=lent)
        t.start()
        self.addCleanup(lambda: t.join(3))
        self.attendre(self.r / "pret")
        rapide = self.run_cmd("import time;time.sleep(.1);print('independant')")
        annuler.set()
        t.join(3)
        self.assertFalse(t.is_alive())
        self.assertTrue(rapide.ok)
        self.assertIn("independant", rapide.stdout)
        self.assertEqual(results["lent"].code_controle, 130)
        self.assertFalse(P._actif(results["lent"].pid))

    def test_descendant_detache_du_parent_dans_meme_groupe_refuse(self):
        r = self.run_cmd(
            "import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])", grace=0.1
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.raison, "DESCENDANTS_ACTIFS")
        self.assertFalse(r.ok)
        self.assertFalse(P._actif(r.pid))

    def test_non_executable_et_bornes_invalides(self):
        r = P.executer(["/commande-inexistante-fixture"], cwd=self.r, diagnostics=self.r / "diag")
        self.assertEqual(r.code_controle, 127)
        self.assertIsNone(r.returncode)
        self.assertEqual(r.execution, "ERREUR_EXECUTION")
        for d in (0, -1, float("inf"), True):
            with self.assertRaises(ValueError):
                self.run_cmd("pass", delai=d)

    def test_arret_manuel_de_la_commande_englobante(self):
        fichier = self.r / "pid"
        code = (
            "import sys;from benchab.processus import executer;executer([sys.executable,'-c',"
            "\"import os,time;open('pid','w').write(str(os.getpid()));time.sleep(20)\"],cwd="
            + repr(str(self.r))
            + ",diagnostics="
            + repr(str(self.r / "diag"))
            + ")"
        )
        wrapper = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            self.attendre(fichier, wrapper)
            pid = int(fichier.read_text())
            os.kill(wrapper.pid, signal.SIGINT)
            out, err = wrapper.communicate(timeout=5)
            self.assertNotEqual(wrapper.returncode, 0)
            reports = list((self.r / "diag").glob("*/diagnostic.json"))
            self.assertEqual(len(reports), 1)
            self.assertEqual(json.loads(reports[0].read_text())["raison"], "ANNULATION")
            self.assertFalse(P._actif(pid))
        finally:
            if wrapper.poll() is None:
                os.killpg(wrapper.pid, signal.SIGKILL)
                wrapper.wait()
            if fichier.exists():
                pid = int(fichier.read_text())
                if P._actif(pid):
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()


def test_sha_depuis_enfant_et_sortie_brutale_preserves(tmp_path):
    """L'enfant hache abc puis sort brutalement à 73 ; SHA et code exacts conservés, aucun service touché."""
    import hashlib

    fichier = tmp_path / "fixture.txt"
    fichier.write_bytes(b"abc")
    racine = Path(__file__).resolve().parents[1]
    programme = ("import sys,os;sys.path.insert(0," + repr(str(racine)) + ");"
                 "from benchab.qualite.empreintes import sha256_fichier;"
                 "print(sha256_fichier(" + repr(str(fichier)) + "),flush=True);os._exit(73)")
    r = P.executer([sys.executable, "-B", "-c", programme], cwd=tmp_path,
                   diagnostics=tmp_path / "diagnostics", delai=5)
    assert r.returncode == r.code_controle == 73
    assert r.resultat == "FAIL"
    assert r.stdout.strip() == hashlib.sha256(b"abc").hexdigest()
    assert fichier.read_bytes() == b"abc"
