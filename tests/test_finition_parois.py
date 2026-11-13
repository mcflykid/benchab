"""Fixtures synthétiques ; aucune extension tierce chargée et aucun tranchage."""

import hashlib
import json
import math
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from benchab.controles import finition_parois as F

M = {"volume_mm": [250, 250, 250], "sources": {}, "non_verifie": []}


def fixture(speeds=(120, 120, 20, 120, 120), objects=("Piece",), tools=(0,), zs=None):
    out = ["G90", "M83", "T0", "G9111 extruderTemp=220 bedTemp=55"]
    for i, v in enumerate(speeds):
        z = zs[i] if zs else (i + 1) * 0.2
        out += [";LAYER_CHANGE", f";Z:{z}", ";HEIGHT:0.2"]
        for name in objects:
            out += [
                "EXCLUDE_OBJECT_START NAME=" + name,
                ";TYPE:Outer wall",
                f"G1 X0 Y0 Z{z} F{v * 60}",
                f"G1 X10 E1 F{v * 60}",
                "EXCLUDE_OBJECT_END NAME=" + name,
            ]
    return "\n".join(out) + "\n"


def ecrire_maitre_synthetique(dossier):
    from test_profil_maitre import profil_synthetique

    master = dossier / "maitre.json"
    profile = profil_synthetique()
    values = {key: fact["value"] for key, fact in profile["configurations"]["atelier-test-v1"]["parametres"].items()}
    snapshot = json.dumps({"valeurs": values}).encode()
    (dossier / "snapshot.json").write_bytes(snapshot)
    profile["sources"]["configuration"].update(
        snapshot="snapshot.json", sha256_snapshot=hashlib.sha256(snapshot).hexdigest()
    )
    master.write_text(json.dumps(profile))
    return master


class Finition(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.d = Path(self.t.name).resolve()

    def analyse(self, g=None, **kwargs):
        p = self.d / "source.gcode"
        p.write_text(g or fixture())
        before = p.read_bytes()
        r = F.analyser(p, maitre=M, **kwargs)
        self.assertEqual(before, p.read_bytes())
        json.dumps(r, allow_nan=False)
        return r

    def test_fifo_refuse_sans_blocage_enfant(self):
        import os
        import subprocess

        source = self.d / "fifo"
        os.mkfifo(source)
        code = (
            "import sys;from pathlib import Path;"
            "from benchab.controles import finition_parois as F;F._lire(Path(sys.argv[1]))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(source)],
            capture_output=True,
            text=True,
            timeout=2,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source non régulière", result.stderr)

    def test_budget_refuse_avant_lecture_et_ferme_descripteur(self):
        from unittest.mock import patch

        source = self.d / "grand"
        source.write_bytes(b"x" * 33)
        close = F.os.close
        with (
            patch.object(F, "MAX_BYTES", 32),
            patch.object(F.os, "read") as read,
            patch.object(F.os, "close", wraps=close) as closed,
        ):
            with self.assertRaisesRegex(ValueError, "source trop grande"):
                F._octets(source)
            read.assert_not_called()
            closed.assert_called_once()

    def test_archive_analysee_depuis_octets_hashes(self):
        from unittest.mock import patch

        source = self.d / "snapshot.3mf"
        with zipfile.ZipFile(source, "w") as z:
            z.writestr("Metadata/plate_1.gcode", "G1 X1")
        raw = source.read_bytes()
        with patch.object(F, "_octets", return_value=raw):
            source.write_bytes(b"archive remplacee apres lecture")
            actual, parts, archive = F._lire(source)
        self.assertEqual(actual, raw)
        self.assertTrue(archive)
        self.assertEqual(parts, [("Metadata/plate_1.gcode", "G1 X1")])

    def test_paroi_en_arc_non_verifiee_sans_planter(self):
        """Un arc sans représentation linéaire rend la couverture NON VÉRIFIÉE."""
        gcode = fixture().replace("G1 X10 E1 F7200", "G2 X10 Y0 I5 J0 E1 F7200", 1)
        r = self.analyse(gcode)
        self.assertEqual(r["couverture"]["statut"], "NON VÉRIFIÉ")
        self.assertFalse(r["transformable"])
        self.assertTrue(any("arc" in erreur for erreur in r["couverture"]["erreurs"]))

    def test_debit_arc_depasse_plafond_meme_si_geometrie_non_verifiee(self):
        """Le débit analytique reste opposable même si la paroi en arc est non mesurée."""
        gcode = fixture().replace("G1 X10 E1 F7200", "G2 X10 Y0 I5 J0 E3 F7200", 1)
        limites = {"1": {"diametre_mm": 1.75, "plafond_mm3_s": 30}}
        r = self.analyse(gcode, limites_filaments=limites)
        debit = r["plaques"][0]["debit_strict_brut_par_outil"]["1"]
        self.assertEqual(debit["depassements"], 1)
        self.assertEqual(debit["statut"], "FAIL")
        self.assertEqual(r["statut"], "FAIL")
        self.assertEqual(r["couverture"]["statut"], "NON VÉRIFIÉ")

    def test_series_z_distinct_height_et_cout(self):
        r = self.analyse()
        layers = [x for s in r["series"] for x in s["couches"]]
        self.assertEqual(len(layers), 5)
        self.assertAlmostEqual(layers[-1]["z_mm"], 1.0)
        self.assertEqual(layers[-1]["hauteur_mm"], 0.2)
        self.assertTrue(layers[0]["protegee"])
        self.assertIsNone(layers[0]["v_cible_mm_s"])
        self.assertGreater(r["proposition"]["delta_nominal_s"], 0)
        self.assertFalse(r["transformable"])
        self.assertEqual(r["validation_physique"], "NON VÉRIFIÉ")

    def test_mult_objets_pas_melanges(self):
        r = self.analyse(fixture(objects=("A", "B")))
        self.assertEqual({s["objet"] for s in r["series"]}, {"A", "B"})
        self.assertEqual(sum(len(s["couches"]) for s in r["series"]), 10)

    def test_mult_outils_frontiere(self):
        g = fixture() + fixture().replace("T0", "T1").replace("NAME=Piece", "NAME=Autre")
        r = self.analyse(g)
        self.assertEqual({s["outil"] for s in r["series"]}, {0, 1})

    def test_gauss_constant_et_descente(self):
        cfg = F._options(None)
        a, n, c, res = F._proposer([100] * 5, [0.2, 0.4, 0.6, 0.8, 1.0], cfg)
        self.assertEqual(a, [100] * 5)
        self.assertEqual(n, 0)
        speeds = [100, 100, 10, 100, 100]
        a, n, c, res = F._proposer(speeds, [0.2, 0.4, 0.6, 0.8, 1.0], cfg)
        self.assertTrue(c)
        self.assertLessEqual(res, 5)
        self.assertTrue(all(x <= y for x, y in zip(a, speeds)))

    def test_convergence_plafond_distinct(self):
        a, n, c, res = F._proposer(
            [1000, 1, 1000], [0.2, 0.4, 0.6], F._options({"max_iterations": 1, "seuil_delta_mm_s": 0.001})
        )
        self.assertFalse(c)
        self.assertEqual(n, 1)

    def test_gradient_hauteurs_variables(self):
        z = [0.2, 0.3, 0.6, 1.0]
        a, n, c, res = F._proposer(
            [100, 20, 100, 100], z, F._options({"mode": "gradient", "pente_max_mm_s_par_mm": 10})
        )
        self.assertTrue(c)
        self.assertTrue(all(abs(a[i] - a[i - 1]) <= 10 * (z[i] - z[i - 1]) + 1e-9 for i in range(1, 4)))

    def test_options_invalides(self):
        for opts in [
            {"sigma_couches": 0},
            {"sigma_couches": math.nan},
            {"fenetre": 4},
            {"skip_first_layers": -1},
            {"max_iterations": True},
            {"mode": "autre"},
            {"inconnu": 1},
            {"pente_max_mm_s_par_mm": math.inf},
        ]:
            with self.subTest(opts=opts), self.assertRaises(ValueError):
                F._options(opts)

    def test_inconnus_arcs_modes_identites_refus(self):
        variants = [
            fixture() + "C X1 E1",
            fixture() + "G2 X1 E1 I2",
            fixture().replace("M83", ""),
            fixture().replace("G90", ""),
            fixture().replace("EXCLUDE_OBJECT_START NAME=Piece", ""),
            fixture().replace(";LAYER_CHANGE", ";LAYER?"),
        ]
        for g in variants:
            r = self.analyse(g)
            self.assertNotEqual(r["couverture"]["statut"], "PASS")
            self.assertFalse(r["transformable"])

    def test_commandes_orca_validees_ne_creent_pas_de_mouvement(self):
        commands = (
            "SET_VELOCITY_LIMIT ACCEL=2500 ACCEL_TO_DECEL=1250 MINIMUM_CRUISE_RATIO=0.5\n"
            "SET_PRESSURE_ADVANCE ADVANCE=0.035\n"
        )
        base = self.analyse(fixture())
        r = self.analyse(commands + fixture())
        self.assertEqual(r["couverture"]["statut"], "PASS")
        # Les deux commandes décalent seulement les références aux lignes source.
        for series in (r["series"], base["series"]):
            for serie in series:
                for couche in serie["couches"]:
                    couche.pop("lignes", None)
        self.assertEqual(r["series"], base["series"])
        self.assertEqual(r["validation_physique"], "NON VÉRIFIÉ")
        self.assertFalse(r["transformable"])
        for command in (
            "SET_PRESSURE_ADVANCE ADVANCE=nan",
            "SET_PRESSURE_ADVANCE AUTRE=1",
            "SET_VELOCITY_LIMIT ACCEL=2500 ACCEL_TO_DECEL=1250 MINIMUM_CRUISE_RATIO=.6",
            "SET_UNSUPPORTED ADVANCE=.035",
        ):
            with self.subTest(command=command):
                self.assertNotEqual(self.analyse(command + "\n" + fixture())["couverture"]["statut"], "PASS")

    def test_z_absent_nan(self):
        for g in [fixture().replace(";Z:", ";NOZ:"), fixture().replace(";Z:0.2", ";Z:nan")]:
            self.assertNotEqual(self.analyse(g)["couverture"]["statut"], "PASS")

    def test_commentaires_ne_changent_pas_f(self):
        r = self.analyse(fixture((60, 60)).replace("G1 X10 E1", "G1 X10 E1 ; F99999 E999"))
        self.assertTrue(all(g["v_max_mm_s"] == 60 for s in r["series"] for g in s["couches"]))

    def test_m82_g92_retraction(self):
        g = fixture((60, 60)).replace("M83", "M82").replace("G1 X10 E1", "G92 E0\nG1 E-1\nG1 E0\nG1 X10 E1")
        r = self.analyse(g)
        self.assertTrue(r["series"])
        self.assertEqual(r["couverture"]["statut"], "PASS")

    def test_debit_strict_brut_inclut_purge(self):
        g = fixture((60, 60)) + ";TYPE:Wipe tower\nG1 X20 E10 F6000\n"
        r = self.analyse(g, limites_filaments={"1": {"diametre_mm": 1.75, "plafond_mm3_s": 1}})
        self.assertEqual(r["statut"], "FAIL")
        self.assertGreater(r["plaques"][0]["debit_strict_brut_par_outil"]["1"]["depassements"], 0)

    def test_absence_parois(self):
        r = self.analyse("G90\nM83\nT0\nG1 X1 E1 F600\n")
        self.assertNotEqual(r["statut"], "PASS")

    def test_html_autonome_escape_collision(self):
        r = self.analyse(fixture(objects=("<script>alert(1)</script>",)))
        out = self.d / "rapport.html"
        F.rendre_html(r, out)
        t = out.read_text()
        self.assertIn("&lt;script&gt;", t)
        self.assertNotIn("<script>", t)
        self.assertNotIn("https://", t)
        self.assertIn("<svg", t)
        with self.assertRaises(ValueError):
            F.rendre_html(r, out)

    def test_cli_ne_modifie_pas_source_et_collision(self):
        p = self.d / "source.gcode"
        p.write_text(fixture())
        master = ecrire_maitre_synthetique(self.d)
        before = p.read_bytes()
        j = self.d / "r.json"
        h = self.d / "r.html"
        self.assertEqual(F.main(["analyser", str(p), "--maitre", str(master), "--json", str(j), "--html", str(h)]), 0)
        self.assertEqual(before, p.read_bytes())
        self.assertEqual(F.main(["analyser", str(p), "--json", str(p)]), 2)
        self.assertEqual(F.main(["analyser", str(p), "--json", str(j)]), 2)
        new = self.d / "new"
        self.assertEqual(F.main(["analyser", str(p), "--json", str(new), "--html", str(new)]), 2)
        self.assertFalse(new.exists())

    def test_archive_non_modifiee(self):
        p = self.d / "a.3mf"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("Metadata/plate_1.gcode", fixture())
            z.writestr("Metadata/project_settings.config", "{}")
        before = p.read_bytes()
        r = F.analyser(p, maitre=M)
        self.assertEqual(before, p.read_bytes())
        self.assertTrue(r["series"])
        self.assertNotEqual(r["statut"], "PASS")

    def test_altitude_depot_differente_commentaire(self):
        r = self.analyse(fixture().replace("G1 X0 Y0 Z0.2", "G1 X0 Y0 Z0.8"))
        self.assertTrue(any("altitude déposée incohérente" in e for e in r["couverture"]["erreurs"]))

    def test_modes_superflus_et_parametres_dupliques(self):
        for g in [fixture().replace("M83", "M83 X1"), fixture().replace("G1 X10 E1", "G1 X10 X20 E1")]:
            self.assertEqual(self.analyse(g)["couverture"]["statut"], "NON VÉRIFIÉ")

    def test_temps_couches_et_vase(self):
        r = self.analyse()
        self.assertTrue(all(c["propose_s"] >= c["avant_s"] for c in r["plaques"][0]["temps_couches_nominaux"]))
        self.assertNotEqual(self.analyse(";spiral_mode = 1\n" + fixture())["couverture"]["statut"], "PASS")

    def test_options_filaments_malformees(self):
        with self.assertRaises(ValueError):
            self.analyse(limites_filaments=[])

    def test_maitre_chemin_valide_manquant(self):
        master = ecrire_maitre_synthetique(self.d)
        before = master.read_bytes()
        p = self.d / "chemin.gcode"
        p.write_text(fixture())
        for value in [str(master), master]:
            self.assertTrue(F.analyser(p, maitre=value)["limites_configurees"]["volume_mm"])
        self.assertEqual(before, master.read_bytes())
        with self.assertRaises(OSError):
            F.analyser(p, maitre=self.d / "absent.json")

    def test_ancetre_lie_et_lien_pendant_refuses(self):
        target = self.d / "original"
        target.mkdir(parents=True)
        (target / "normal").mkdir(parents=True)
        protected = target / "temoin"
        protected.write_text("préservé")
        link = self.d / "ancetre"
        link.symlink_to(target, target_is_directory=True)
        dst = link / "normal" / "rapport.html"
        r = self.analyse()
        with self.assertRaises(ValueError):
            F.rendre_html(r, dst)
        p = self.d / "source.gcode"
        p.write_text(fixture())
        self.assertEqual(F.main(["analyser", str(p), "--json", str(link / "normal" / "r.json")]), 2)
        dangling = self.d / "pendant"
        dangling.symlink_to(self.d / "absent")
        with self.assertRaises(ValueError):
            F.rendre_html(r, dangling)
        self.assertEqual(protected.read_text(), "préservé")
        self.assertFalse((target / "normal" / "rapport.html").exists())
        self.assertFalse((target / "normal" / "r.json").exists())

    def test_html_explique_couverture_et_contraintes_echappees(self):
        r = self.analyse()
        r["couverture"]["erreurs"] = ["<script>couverture unique</script>"]
        r["controle_capacites"] = {
            "statut": "FAIL",
            "contraintes_non_pass": [
                {"controle": "débit unique <fort>", "statut": "FAIL", "detail": "plafond dépassé"}
            ],
        }
        out = self.d / "diagnostic.html"
        F.rendre_html(r, out)
        t = out.read_text()
        self.assertIn("couverture unique", t)
        self.assertIn("débit unique &lt;fort&gt;", t)
        self.assertIn("plafond dépassé", t)
        self.assertNotIn("<script>", t)

    def test_sortie_lien_refuse(self):
        target = self.d / "target"
        target.write_text("original")
        link = self.d / "link"
        link.symlink_to(target)
        with self.assertRaises(ValueError):
            F.rendre_html(self.analyse(), link)
        self.assertEqual(target.read_text(), "original")


if __name__ == "__main__":
    unittest.main()


def test_diagnostic_vase_releve_une_spirale_continue(tmp_path):
    """Le fond et la largeur déclarés accompagnent le diagnostic du trajet nominal."""
    source = tmp_path / "vase.3mf"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", '{"spiral_mode":"1","bottom_shell_layers":"1"}')
        archive.writestr("Metadata/plate_1.gcode", (
            "G90\nM83\nT0\n;LAYER_CHANGE\n;Z:0.2\n;TYPE:Bottom surface\nG1 Z0.2 F600\nG1 X1 E0.1\n"
            ";LAYER_CHANGE\n;Z:0.4\n;TYPE:Outer wall\n;WIDTH:0.45\n"
            "G1 X2 Z0.3 E0.1\nG1 X3 Z0.4 E0.1\n"
        ))
    resultat = F.analyser(source, maitre=M)
    vase = resultat["plaques"][0]["vase"]
    assert vase["segments_deposes"] == 2
    assert vase["morceaux_contigus"] == 1
    assert vase["largeur_ligne_mm"] == {"min": 0.45, "max": 0.45}
    assert vase["couches_fond_declarees"] == vase["couches_fond_observees"] == 1
    assert vase["absence_couture"] == "NON VÉRIFIÉ"
    html = tmp_path / "vase.html"
    F.rendre_html(resultat, html)
    assert "Mode vase" in html.read_text()
