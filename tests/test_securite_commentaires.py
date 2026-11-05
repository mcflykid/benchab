import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from benchab.visualisation import serveur as S


class Securite(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name).resolve()
        S.RACINE = self.root
        self.page = "projets/test/preuves/visualisation/page.html"
        p = self.root / self.page
        p.parent.mkdir(parents=True)
        p.write_text("page")
        self.s = S.Serveur(("127.0.0.1", 0), S.Gestion)
        threading.Thread(target=self.s.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.s.server_port}"
        self.errors = []

    def tearDown(self):
        self.s.shutdown()
        self.s.server_close()
        self.t.cleanup()

    def post(self, d, origin=None):
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.s.server_port, timeout=10)
        try:
            conn.request(
                "POST",
                "/__commentaires",
                json.dumps(d, ensure_ascii=False).encode(),
                {"Content-Type": "application/json", "Origin": origin or self.url},
            )
            with conn.getresponse() as response:
                body = response.read().decode()
                if response.status >= 400:
                    self.errors.append(body)
                return response.status
        finally:
            conn.close()

    def test_b17_origin(self):
        self.assertEqual(self.post({"page": self.page, "commentaires": []}, "https://evil.invalid"), 403)

    def test_b17_path(self):
        (self.root / "AGENTS.html").write_text("x")
        self.assertEqual(self.post({"page": "AGENTS.html", "commentaires": []}), 400)

    def test_b17_symlink(self):
        f = self.root / self.page
        target = self.root / "target.json"
        target.write_text("{}")
        (f.parent / "commentaires.json").symlink_to(target)
        self.assertEqual(self.post({"page": self.page, "commentaires": []}), 400)

    def test_b24_preserve_concurrent(self):
        self.assertEqual(
            {x["id"] for x in S.fusionner([{"id": "a", "reponses": [{"texte": "réponse"}]}], [{"id": "b"}])}, {"a", "b"}
        )

    def comment(self, id):
        return {
            "id": id,
            "piece": "Cube",
            "texte": "test",
            "local": [0, 0, 0],
            "point_mm": [0, 0, 0],
            "normale": [0, 0, 1],
            "reponses": [],
        }

    def test_normal_post_and_invalid_data(self):
        self.assertEqual(self.post({"page": self.page, "commentaires": [self.comment("a")]}), 200)
        self.assertEqual(self.post({"page": self.page, "commentaires": [{"id": "a"}]}), 400)
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", self.s.server_port)
        conn.putrequest("POST", "/__commentaires")
        conn.putheader("Origin", self.url)
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "1000001")
        conn.endheaders()
        with conn.getresponse() as response:
            self.assertEqual(response.status, 413)
            response.read()
        conn.close()

    def test_concurrent_http_and_cli(self):
        import subprocess

        self.assertEqual(self.post({"page": self.page, "commentaires": [self.comment("a")]}), 200)
        f = self.root / self.page
        target = f.parent / "commentaires.json"
        import os

        env = {**os.environ, "BENCHAB_PRIVE": str(self.root)}
        script = Path(__file__).resolve().parents[1] / "benchab/visualisation/commentaires.py"
        commands = [
            subprocess.Popen(
                [sys.executable, str(script), "repondre", str(target), "a", str(i)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            for i in range(8)
        ]
        for proc in commands:
            stdout, stderr = proc.communicate()
            self.assertEqual(proc.returncode, 0, stderr.decode())
        replies = json.loads(target.read_text())["commentaires"][0]["reponses"]
        self.assertEqual(len(replies), 8)
        c = self.comment("b")
        self.assertEqual(self.post({"page": self.page, "commentaires": [c]}), 200)
        result = json.loads(target.read_text())
        self.assertEqual(len(result["commentaires"]), 2)
        self.assertEqual(len(result["commentaires"][1]["reponses"]), 8)
        self.assertEqual(self.post({"page": self.page, "commentaires": [], "supprimes": ["a"]}), 200)
        self.assertEqual(self.post({"page": self.page, "commentaires": [self.comment("a")]}), 200)
        self.assertEqual([c["id"] for c in json.loads(target.read_text())["commentaires"]], ["b"])

    def test_parallel_http_updates(self):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(
                pool.map(lambda i: self.post({"page": self.page, "commentaires": [self.comment(str(i))]}), range(12))
            )
        self.assertEqual(codes, [200] * 12, self.errors)
        result = json.loads((self.root / self.page).with_name("commentaires.json").read_text())
        self.assertEqual(len(result["commentaires"]), 12)

    def test_server_root_is_immutable(self):
        S.RACINE = self.root / "not-the-server"
        self.assertEqual(self.post({"page": self.page, "commentaires": [self.comment("a")]}), 200)
        self.assertTrue((self.root / self.page).with_name("commentaires.json").is_file())

    def test_static_symlink_outside(self):
        with tempfile.TemporaryDirectory() as d:
            outside = Path(d) / "secret"
            outside.write_text("secret")
            (self.root / "link").symlink_to(outside)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.url + "/link")
            self.assertEqual(caught.exception.code, 403)

    def test_limite_apres_fusion_et_suppression(self):
        from benchab.visualisation import stockage_commentaires as C

        target = (self.root / self.page).with_name("commentaires.json")
        C.atomique(target, {"page": self.page, "commentaires": [self.comment(str(i)) for i in range(1999)]})
        self.assertEqual(self.post({"page": self.page, "commentaires": [self.comment("a")]}), 200)
        avant = target.read_bytes()
        self.assertEqual(self.post({"page": self.page, "commentaires": [self.comment("b")]}), 400)
        self.assertEqual(target.read_bytes(), avant)
        self.assertEqual(self.post({"page": self.page, "commentaires": [self.comment("b")], "supprimes": ["0"]}), 200)

    def test_limite_fusion_concurrente(self):
        from concurrent.futures import ThreadPoolExecutor

        from benchab.visualisation import stockage_commentaires as C

        target = (self.root / self.page).with_name("commentaires.json")
        C.atomique(target, {"page": self.page, "commentaires": [self.comment(str(i)) for i in range(1999)]})
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(
                pool.map(lambda i: self.post({"page": self.page, "commentaires": [self.comment(i)]}), ("a", "b"))
            )
        self.assertEqual(sorted(codes), [200, 400])
        self.assertEqual(len(json.loads(target.read_text())["commentaires"]), 2000)

    def test_limite_octets_utf8_exacte_et_atomicite(self):
        from benchab.visualisation import stockage_commentaires as C

        target = (self.root / self.page).with_name("commentaires.json")
        d = {"page": self.page, "commentaires": [], "supprimes": ["é"]}
        n = C.MAX_OCTETS - len(C.serialiser(d))
        d["supprimes"][0] += "x" * n
        self.assertEqual(len(C.serialiser(d)), C.MAX_OCTETS)
        C.atomique(target, d)
        avant = target.read_bytes()
        d["supprimes"][0] += "x"
        with self.assertRaises(ValueError):
            C.atomique(target, d)
        self.assertEqual(target.read_bytes(), avant)
        self.assertFalse(list(target.parent.glob(".commentaires-*.tmp")))

    def test_reponses_fusionnees_trop_volumineuses(self):
        from benchab.visualisation import stockage_commentaires as C

        target = (self.root / self.page).with_name("commentaires.json")
        a = self.comment("a")
        a["reponses"] = [{"auteur": "test", "date": "date", "texte": "é" * 240000}]
        C.atomique(target, {"page": self.page, "commentaires": [a]})
        avant = target.read_bytes()
        b = self.comment("b")
        b["reponses"] = [{"auteur": "test", "date": "date", "texte": "é" * 270000}]
        self.assertEqual(self.post({"page": self.page, "commentaires": [b]}), 400)
        self.assertEqual(target.read_bytes(), avant)

    def test_lecture_fifo_refusee_sans_blocage(self):
        import os
        import subprocess

        target = (self.root / self.page).with_name("commentaires.json")
        os.mkfifo(target)
        code = "import sys;from benchab.visualisation.stockage_commentaires import lire;lire(sys.argv[1])"
        result = subprocess.run([sys.executable, "-c", code, str(target)], capture_output=True, text=True, timeout=2)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fichier régulier requis", result.stderr)

    def test_lecture_volumineuse_et_corrompue_refusee(self):
        from benchab.visualisation import stockage_commentaires as C

        target = (self.root / self.page).with_name("commentaires.json")
        for body in (b" " * (C.MAX_OCTETS + 1), b"{corrompu"):
            target.write_bytes(body)
            with self.assertRaises(ValueError):
                C.lire(target)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.url + "/__commentaires?page=" + self.page, timeout=2)
            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(target.read_bytes(), body)
        target.write_text(json.dumps({"page": self.page, "commentaires": []}))
        with urllib.request.urlopen(self.url + "/__commentaires?page=" + self.page, timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response)["commentaires"], [])

    def test_verrou_fifo_refuse_sans_modification(self):
        import os

        target = (self.root / self.page).with_name("commentaires.json")
        lock = target.with_name(target.name + ".lock")
        os.mkfifo(lock)
        self.assertEqual(self.post({"page": self.page, "commentaires": []}), 400)
        self.assertFalse(target.exists())
        self.assertTrue(lock.exists())

    def test_verrou_occupe_attente_bornee_et_reprise(self):
        import fcntl
        from unittest.mock import patch

        from benchab.visualisation import stockage_commentaires as C

        target = (self.root / self.page).with_name("commentaires.json")
        lock = target.with_name(target.name + ".lock")
        with lock.open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with patch.object(C, "DELAI_VERROU", 0.05):
                self.assertEqual(self.post({"page": self.page, "commentaires": []}), 400)
            self.assertIn("occupés", self.errors[-1])
            self.assertFalse(target.exists())
        self.assertEqual(self.post({"page": self.page, "commentaires": []}), 200)


if __name__ == "__main__":
    unittest.main()


def test_cli_commentaires_refuse_traversee_hors_base_sans_modification(tmp_path):
    """Un fichier dans prive/../ailleurs est refusé code2 ; aucun contenu extérieur à la base n'est modifié."""
    import pytest

    from benchab.visualisation.commentaires import main

    prive = tmp_path / "prive"
    prive.mkdir()
    autre = tmp_path / "ailleurs"
    autre.mkdir()
    cible = autre / "commentaires.json"
    contenu = b'{"commentaires":[]}'
    cible.write_bytes(contenu)
    avec_traversee = prive / ".." / "ailleurs/commentaires.json"
    with pytest.raises(SystemExit) as refus:
        main(["repondre", str(avec_traversee), "1", "refuse"], prive=prive)
    assert refus.value.code == 2
    assert cible.read_bytes() == contenu
