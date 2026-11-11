"""Tests géométriques synthétiques de l'optimisation interne."""

import io
import json
import tempfile
import types
import unittest
import zipfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import trimesh

from benchab.conception import optimisation_interne as O
from benchab.visualisation.visionneuse import Visionneuse

BLOC = trimesh.creation.box(extents=(80, 40, 24))
BLOC.apply_translation((40, 20, 12))
CAV = {"nom": "c", "forme": "toit", "min": [20, 3, 3], "max": [60, 37, 21], "faitage": "x", "angle": 45}
VIS = {
    "nom": "vis",
    "type": "fixation",
    "forme": "cylindre",
    "centre": [10, 20, 12],
    "rayon": 6,
    "hauteur": 24,
    "marge": 1.5,
}


class Cavites(unittest.TestCase):
    def test_variante_conforme(self):
        var, r = O.appliquer(BLOC, [CAV], {"paroi_min": 2.4, "protege": [VIS]})
        self.assertEqual(r["statut"], "WARN")  # WARN seul : cavité captive à vérifier dans le G-code
        self.assertTrue(all(c["statut"] in ("PASS", "WARN") for c in r["controles"]))
        self.assertAlmostEqual(r["volume_retire_mm3"], O.cavite(CAV).volume, delta=1)

    def test_aucune_cavite_reference_inchangee(self):
        var, r = O.appliquer(BLOC, [], {"protege": [VIS]})
        self.assertEqual(r["statut"], "PASS")
        self.assertEqual(r["volume_retire_mm3"], 0)

    def test_region_protegee_violee(self):
        r = O.appliquer(BLOC, [dict(CAV, min=[8, 3, 3])], {"protege": [VIS]})[1]
        self.assertEqual(r["statut"], "FAIL")
        self.assertIn("FAIL", [c["statut"] for c in r["controles"] if "vis" in c["controle"]])

    def test_paroi_trop_fine(self):
        r = O.appliquer(BLOC, [dict(CAV, min=[20, 3, 1])], {"paroi_min": 2.4})[1]
        self.assertEqual([c["statut"] for c in r["controles"] if c["controle"].startswith("paroi")], ["FAIL"])

    def test_cavite_debouchante_detectee(self):
        r = O.appliquer(BLOC, [dict(CAV, min=[20, 3, -1])], {})[1]
        self.assertEqual([c["statut"] for c in r["controles"] if c["controle"].startswith("enveloppe")], ["FAIL"])

    def test_cavites_invalides(self):
        for c in [
            dict(CAV, angle=30),
            dict(CAV, forme="sphere"),
            dict(CAV, max=[60, 37, 5]),
            dict(CAV, faitage="z"),
            dict(CAV, max=[10, 37, 21]),
        ]:
            with self.subTest(c=c), self.assertRaises(ValueError):
                O.cavite(c)
        with self.assertRaises(ValueError):
            O.appliquer(BLOC, [CAV], {"protege": [dict(VIS, type="magique")]})

    def test_visionneuse_affiche_cavites_et_regions(self):
        var, r = O.appliquer(BLOC, [CAV], {"protege": [VIS]})
        with tempfile.TemporaryDirectory() as t:
            stl = Path(t) / "B.stl"
            BLOC.export(stl)
            v = Visionneuse("t")
            v.optimisation_interne("B", stl, [CAV], {"protege": [VIS]}, r)
            res = v.ecrire(Path(t) / "p.html")
            h = (Path(t) / "p.html").read_text()
            self.assertEqual(res["pieces"], 3)
            self.assertIn("Optimisation interne — B", h)
            self.assertIn("paroi PASS", h)

    def test_toit_sans_plafond_plat(self):
        m = O.cavite(CAV)
        self.assertTrue(m.is_volume)
        self.assertLess(m.face_normals[:, 2].max(), 0.7072)


NERV = {"nom": "n", "forme": "nervure", "debut": [20, 20, 3], "fin": [60, 20], "hauteur": 18.5, "epaisseur": 1.2}
GOUS = {
    "nom": "g",
    "forme": "gousset",
    "coin": [20, 20, 3],
    "direction": "+x",
    "base": 8,
    "hauteur": 8,
    "epaisseur": 1.2,
}


def statuts(rapport, prefixe):
    return [c["statut"] for c in rapport["controles"] if c["controle"].startswith(prefixe)]


class Renforts(unittest.TestCase):
    def test_nervure_dans_la_cavite_ajoute_de_la_matiere_sans_toucher_l_exterieur(self):
        var, r = O.appliquer(BLOC, [CAV, NERV], {"protege": [VIS]})
        self.assertEqual(r["statut"], "WARN")  # seul WARN : cavité captive
        self.assertGreater(r["volume_renforts_mm3"], 40 * 1.2 * 15)
        self.assertEqual(statuts(r, "enveloppe"), ["PASS"])
        self.assertEqual(statuts(r, "appui"), ["PASS"])
        self.assertEqual(statuts(r, "effet"), ["PASS"])
        self.assertAlmostEqual(r["volume_retire_mm3"], O.cavite(CAV).volume - r["volume_renforts_mm3"], delta=1)

    def test_nervure_flottante_refusee(self):
        r = O.appliquer(BLOC, [CAV, dict(NERV, debut=[20, 20, 10], hauteur=5)], {})[1]
        self.assertEqual(statuts(r, "appui"), ["FAIL"])
        self.assertEqual(r["statut"], "FAIL")

    def test_nervure_dans_la_matiere_sans_effet(self):
        r = O.appliquer(BLOC, [dict(NERV, debut=[65, 5, 3], fin=[75, 5])], {})[1]
        self.assertEqual(statuts(r, "effet"), ["WARN"])
        self.assertEqual(r["volume_renforts_mm3"], 0)

    def test_epaisseur_trop_fine_refusee(self):
        r = O.appliquer(BLOC, [CAV, dict(NERV, epaisseur=0.5)], {})[1]
        self.assertEqual(statuts(r, "épaisseur"), ["FAIL"])

    def test_nervure_qui_depasse_decoupee_a_l_enveloppe(self):
        var, r = O.appliquer(BLOC, [CAV, dict(NERV, hauteur=40)], {})
        self.assertEqual(statuts(r, "enveloppe"), ["PASS"])
        self.assertLessEqual(var.bounds[1][2], 24 + 1e-6)

    def test_gousset_dans_un_coin_de_la_cavite(self):
        var, r = O.appliquer(BLOC, [CAV, GOUS], {})
        self.assertEqual(statuts(r, "appui"), ["PASS"])
        self.assertEqual(statuts(r, "effet"), ["PASS"])
        self.assertAlmostEqual(r["volume_renforts_mm3"], 8 * 8 / 2 * 1.2, delta=1)
        self.assertGreaterEqual(
            O.renfort(GOUS).face_normals[:, 2].min(), -1 - 1e-9
        )  # seule face vers le bas : la base, posée

    def test_visionneuse_affiche_les_renforts(self):
        var, r = O.appliquer(BLOC, [CAV, NERV, GOUS], {})
        with tempfile.TemporaryDirectory() as t:
            stl = Path(t) / "D.stl"
            BLOC.export(stl)
            v = Visionneuse("t")
            v.optimisation_interne("D", stl, [CAV, NERV, GOUS], {}, r)
            res = v.ecrire(Path(t) / "p.html")
            h = (Path(t) / "p.html").read_text()
            self.assertEqual(res["pieces"], 4)
            self.assertIn("nervure n", h)
            self.assertIn("gousset g", h)
            self.assertIn("appui PASS", h)
            self.assertEqual(
                h.count('"cliquable":false'), 1
            )  # seule l'enveloppe laisse passer le clic vers les détails internes
            self.assertIn("pièce : statut des contrôles", h)
            # Les deux gardes d'origine restent dans leurs parcours ; la liste
            # ajoute un troisième filtrage des enveloppes non interactives.
            self.assertEqual(h.count("cliquable !== false"), 3)
            for debut, fin in (
                ("function viser(ev)", "renderer.domElement.addEventListener"),
                ("function viserPoint(ev)", "function point("),
            ):
                bloc = h[h.index(debut) : h.index(fin, h.index(debut))]
                self.assertIn("cliquable !== false", bloc)
            self.assertIn("pieces.filter(p => !p.base_cachee && p.cliquable !== false)", h)
            var.export(Path(t) / "D-opt.stl")
            v2 = Visionneuse("t")
            v2.optimisation_interne("D", stl, [CAV, NERV, GOUS], {}, r, variante=Path(t) / "D-opt.stl")
            self.assertEqual(v2.ecrire(Path(t) / "q.html")["pieces"], 5)
            self.assertIn("D (variante optimisée)", (Path(t) / "q.html").read_text())
            v3 = Visionneuse("t")
            v3.optimisation_interne("D", stl, [CAV], {}, r, variante=Path(t) / "absente.stl")
            self.assertEqual(v3.ecrire(Path(t) / "r.html")["pieces"], 2)

    def test_seuil_interpole_le_changement_de_signe(self):
        def ligne(variante, densite, ecart):
            return {"variante": variante, "remplissage": f"{densite}%", "ecart_pct_masse": ecart}

        self.assertEqual(O.seuil([ligne("B15", 15, 10), ligne("B20", 20, 5), ligne("B25", 25, -5)], "B"), 22.5)
        self.assertIsNone(O.seuil([ligne("B15", 15, 10), ligne("B40", 40, 2)], "B"))
        self.assertEqual(O.seuil([ligne("B15", 15, 0)], "B"), 15)

    def test_renforts_invalides(self):
        for r in [
            dict(NERV, forme="arc"),
            dict(NERV, epaisseur=0),
            dict(NERV, fin=[20, 20]),
            dict(GOUS, direction="z"),
            dict(GOUS, base=0),
        ]:
            with self.subTest(r=r), self.assertRaises(ValueError):
                O.renfort(r)


class Preservation(unittest.TestCase):
    def test_eprouvette_refuse_sorties_existantes_avant_traitement(self):
        for where in ["sortie", "tranches"]:
            with self.subTest(where=where), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                out = root / "sortie"
                tr = root / "tranches"
                p = root / where / "original"
                p.parent.mkdir()
                p.write_bytes(b"original")
                with self.assertRaises(FileExistsError):
                    O.eprouvette(out, tr, root / "base-absente.3mf")
                self.assertEqual(p.read_bytes(), b"original")

    def test_resultats_tronques_et_identites_refuses(self):
        """Refuse les résultats incomplets et vérifie le nom canonique depuis le profil fixture."""
        cas = [
            ([], ["1", "2", "3"], False), ([1, 2], ["1", "2", "3"], False),
            ([1, 1, 2, 3], ["1", "2", "3"], False), ([1, 2, 3, 5], ["1", "2", "3", "5"], False),
            ([1, 2, 3, 4], ["01", "2", "3", "4"], True),
            ([1, 2, 3, 4], ["1", "01", "2", "3", "4"], False),
        ]
        for indices, membres, valide in cas:
            with self.subTest(indices=indices, membres=membres), tempfile.TemporaryDirectory(dir="/private/tmp") as td:
                root = Path(td)
                base = root / "base.3mf"
                tranche = root / "fake.3mf"
                with zipfile.ZipFile(base, "w") as z:
                    z.writestr(
                        "Metadata/project_settings.config",
                        json.dumps({"filament_settings_id": ["PLA fixe"], "filament_colour": ["#FFFFFF"]}),
                    )
                with zipfile.ZipFile(tranche, "w") as z:
                    z.writestr("Metadata/slice_info.config", "<config>" + "".join(
                        f'<plate><metadata key="index" value="{i}"/>'
                        '<metadata key="prediction" value="60"/><metadata key="weight" value="1"/></plate>'
                        for i in indices
                    ) + "</config>")
                    for k in membres:
                        z.writestr(f"Metadata/plate_{k}.gcode", "M83\n;TYPE:Sparse infill\nG1 X10 E1 F600")

                def ranger(items, *a, **k):
                    for item in items:
                        item.update(x=0, y=0)

                appels_ecriture = []

                def ecrire(*args, **kwargs):
                    appels_ecriture.append((Path(args[2]).name, kwargs))
                    return base

                a = types.SimpleNamespace(
                    ranger=ranger,
                    ecrire_3mf=ecrire,
                    trancher=lambda *a, **k: (tranche, 0),
                )
                with (
                    patch.object(O.A, "ranger", a.ranger),
                    patch.object(O.A, "ecrire_3mf", a.ecrire_3mf),
                    patch.object(O.A, "trancher", a.trancher),
                    patch.object(O, "appliquer", return_value=(BLOC, {"statut": "WARN"})),
                    patch.object(O.RL, "volumes", return_value=([], [])),
                ):
                    if valide:
                        rapport = O.eprouvette(root / "sortie", root / "tranches", base, densites=(15,))
                        self.assertEqual(len(rapport["variantes"]), 4)
                        self.assertTrue(all(ligne["grammes"] == 1 for ligne in rapport["variantes"]))
                    elif "01" in membres:
                        stderr = io.StringIO()
                        with redirect_stderr(stderr):
                            code = O.main([
                                "eprouvette", "--base", str(base), "--sortie", str(root / "sortie"),
                                "--tranches", str(root / "tranches"), "--densites", "15",
                            ])
                        self.assertEqual(code, 2)
                        self.assertIn("numéro de plaque dupliqué", stderr.getvalue())
                        self.assertFalse((root / "sortie/eprouvette.json").exists())
                    else:
                        with self.assertRaises(ValueError):
                            O.eprouvette(root / "sortie", root / "tranches", base, densites=(15,))
                self.assertTrue(appels_ecriture[-1][1]["canonique"])
                self.assertEqual(appels_ecriture[-1][1]["nom_source"], "eprouvette-plaques.3mf")

    def test_metadonnees_ambigues_de_l_archive_refusees_par_la_commande(self):
        """Une clé index répétée dans l’archive réelle ne publie pas de bilan d’étude."""
        with tempfile.TemporaryDirectory(dir="/private/tmp") as td:
            root = Path(td)
            base = root / "base.3mf"
            tranche = root / "fake.3mf"
            with zipfile.ZipFile(base, "w") as z:
                z.writestr(
                    "Metadata/project_settings.config",
                    json.dumps({"filament_settings_id": ["PLA fixe"], "filament_colour": ["#FFFFFF"]}),
                )
            with zipfile.ZipFile(tranche, "w") as z:
                z.writestr(
                    "Metadata/slice_info.config",
                    '<config><plate><metadata key="index" value="1"/>'
                    '<metadata key="index" value="2"/><metadata key="prediction" value="60"/>'
                    '<metadata key="weight" value="1"/></plate></config>',
                )
                z.writestr("Metadata/plate_1.gcode", "M83\n;TYPE:Sparse infill\nG1 X10 E1 F600")

            def ranger(items, *a, **k):
                for item in items:
                    item.update(x=0, y=0)

            with (
                patch.object(O.A, "ranger", ranger),
                patch.object(O.A, "ecrire_3mf", return_value=base),
                patch.object(O.A, "trancher", return_value=(tranche, 0)),
                patch.object(O, "appliquer", return_value=(BLOC, {"statut": "WARN"})),
                patch.object(O.RL, "volumes", return_value=([], [])),
                redirect_stderr(stderr := io.StringIO()),
            ):
                code = O.main([
                    "eprouvette", "--base", str(base), "--sortie", str(root / "sortie"),
                    "--tranches", str(root / "tranches"), "--densites", "15",
                ])

            self.assertEqual(code, 2)
            self.assertIn("métadonnées de plaque absentes, répétées ou mal formées", stderr.getvalue())
            self.assertFalse((root / "sortie/eprouvette.json").exists())


if __name__ == "__main__":
    unittest.main()
