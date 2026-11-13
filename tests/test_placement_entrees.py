"""Régressions de placement adaptées sur 3MF et G-code synthétiques."""

import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from benchab.controles import disposition_reelle as DR
from benchab.controles import plaques_interface as PI


class PlacementEntrees(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory()
        self.addCleanup(temporaire.cleanup)
        self.racine = Path(temporaire.name).resolve()

    def archive(self, coord="10", vide=False):
        source = self.racine / "source.3mf"
        modele = (
            '<model><resources><object id="1"><mesh><vertices><vertex x="'
            + coord
            + '" y="10" z="0"/><vertex x="20" y="20" z="10"/></vertices></mesh></object></resources>'
            '<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/></build></model>'
        )
        configuration = (
            "<config>"
            + ("" if vide else '<plate><model_instance><metadata key="object_id" value="1"/></model_instance></plate>')
            + "</config>"
        )
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("3D/3dmodel.model", modele)
            archive.writestr("Metadata/model_settings.config", configuration)
        return source

    def enrich(self, source):
        temporary = self.racine / "enrichi.3mf"
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(temporary, "w") as enriched:
            for info in original.infolist():
                if info.filename == "Metadata/model_settings.config":
                    continue
                enriched.writestr(info, original.read(info.filename))
            enriched.writestr(
                "Metadata/model_settings.config",
                '<config><object id="1"><metadata key="name" value="Piece"/>'
                '<metadata key="extruder" value="1"/></object><plate>'
                '<metadata key="plater_id" value="1"/><model_instance>'
                '<metadata key="object_id" value="1"/></model_instance></plate></config>',
            )
            enriched.writestr("Metadata/project_settings.config", json.dumps({"initial_layer_print_height": "0.2"}))
            enriched.writestr(
                "Metadata/slice_info.config", '<config><plate><metadata key="index" value="1"/></plate></config>'
            )
            enriched.writestr(
                "Metadata/plate_1.gcode",
                "G90\nM83\nEXCLUDE_OBJECT_DEFINE NAME=Piece\nEXCLUDE_OBJECT_START NAME=Piece\n"
                ";Z:0.2\n;HEIGHT:0.2\n;TYPE:Outer wall\n"
                "G1 X10 Y10 Z0.2 F1200\nG1 X20 Y10 E0.2\nG1 X20 Y20 E0.2\nEXCLUDE_OBJECT_END NAME=Piece\n",
            )
        source.unlink()
        temporary.rename(source)
        return source

    def test_plaque_vide_ne_passe_pas_et_coordonnees_non_finies_refusees(self):
        self.assertFalse(PI.analyser(self.archive(vide=True))["conforme"])
        for coord in ("nan", "inf", "-inf"):
            with self.subTest(coord=coord), self.assertRaises(ValueError):
                PI.analyser(self.archive(coord))

    def test_interface_valide_et_sortie_existante_preservee(self):
        source = self.archive()
        rapport = self.racine / "rapport.json"
        avant = source.read_bytes()
        self.assertEqual(PI.main([str(source), str(rapport)]), 0)
        resultat = rapport.read_bytes()
        self.assertTrue(json.loads(resultat)["conforme"])
        self.assertEqual(PI.main([str(source), str(rapport)]), 0)
        self.assertEqual(rapport.read_bytes(), resultat)
        self.assertNotEqual(PI.main([str(source), str(source)]), 0)
        self.assertEqual(source.read_bytes(), avant)

    def test_disposition_rejouable_deux_fois_sans_slicer(self):
        source = self.enrich(self.archive())
        placement = self.racine / "placement.json"
        placement.write_text(
            json.dumps(
                {
                    "plates": [{"name": "Courte"}],
                    "objects": [{"name": "Piece", "plate": 1, "xy": [10, 10], "size": [10, 10], "reglages": {}}],
                }
            )
        )
        avant = source.read_bytes()
        premier = DR.controler(source, placement, self.racine / "premier.json")
        second = DR.controler(source, placement, self.racine / "second.json")
        self.assertEqual(premier, second)
        self.assertTrue(premier["conforme"])
        self.assertEqual(source.read_bytes(), avant)

    def test_resume_disposition_localise_l_ecart_sans_perdre_le_rapport(self):
        """Le résumé annonce la première plaque dont le nombre d’objets diverge."""
        source = self.enrich(self.archive())
        placement = self.racine / "placement.json"
        placement.write_text(json.dumps({"plates": [{"name": "Courte"}], "objects": []}))
        rapport = self.racine / "rapport.json"
        capture = StringIO()
        with redirect_stdout(capture):
            self.assertEqual(DR.main([str(source), str(placement), str(rapport), "--resume"]), 1)
        court = json.loads(capture.getvalue())
        complet = json.loads(rapport.read_bytes())
        self.assertEqual(court["statut"], "FAIL")
        self.assertEqual(court["premiere_plaque_en_ecart"], complet["plaques"][0])
        self.assertFalse(complet["conforme"])

    def test_disposition_refuse_collision_avant_ecriture(self):
        source = self.enrich(self.archive())
        placement = self.racine / "placement.json"
        placement.write_text("{}")
        for destination in (source, placement):
            avant = destination.read_bytes()
            self.assertEqual(DR.main([str(source), str(placement), str(destination)]), 2)
            self.assertEqual(destination.read_bytes(), avant)


if __name__ == "__main__":
    unittest.main()
