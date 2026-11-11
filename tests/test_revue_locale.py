"""Tests de revue_locale.py (chantier K, lot 2) : G-code synthétique, aucun fichier réel touché."""

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import pytest

from benchab.controles import revue_locale as RL
from benchab.controles import zones_locales as ZL


def couche(z, longueur, f=600, support=None):
    """Une couche : un aller-retour de `longueur` mm à F (mm/min), éventuellement un dépôt de support en (x, y)."""
    lignes = [f";Z:{z}", ";TYPE:Outer wall", f"G1 X0 Y0 F{f}", f"G1 X{longueur} Y0 E1", "G1 X0 Y0 E1"]
    if support:
        lignes += [";TYPE:Support", f"G1 X{support[0]} Y{support[1]} E0.5"]
    return lignes


class Revue(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = Path(self.t.name)
        # F600 = 10 mm/s : 100 mm aller-retour = 20 s ; 30 mm = 6 s (sous 8 s)
        g = (
            ["M83"]
            + couche(0.2, 100)
            + couche(0.4, 100, support=(50, 50))
            + couche(0.6, 100)
            + couche(20.0, 100)
            + couche(20.2, 30)
            + ["EXCLUDE_OBJECT_START NAME=Piece"]
        )
        self.tr = self.d / "tranche.3mf"
        with zipfile.ZipFile(self.tr, "w") as z:
            z.writestr("Metadata/plate_1.gcode", "\n".join(g))

        def zone(identifier, z_min, bbox):
            return {
                "id": identifier,
                "plaque": 1,
                "type": "Bridge",
                "z_min_mm": z_min,
                "z_max_mm": z_min,
                "bbox_xy_mm": bbox,
                "vitesse_max_mm_s": 10,
                "separation_support_max_mm": 0.21,
            }

        self.zones = [
            zone("soutenu", 0.6, [45, 45, 60, 60]),  # support à la couche 0.4 sous la zone
            zone("court", 20.0, [0, 0, 5, 5]),  # 7,1 mm, couche 20 s
            zone("long-haut", 20.0, [0, 0, 30, 30]),  # 42 mm à Z 20 : aucun critère
            zone("couche-courte", 20.2, [0, 0, 2, 2]),  # couche de 6 s : jamais justifiée
            zone("pont-bas", 0.6, [0, 0, 15, 10]),
        ]  # 18 mm, Z 0,6, couche 20 s
        self.env = self.d / "envoi.json"
        self.env.write_text(
            json.dumps(
                {"zones_locales": {"tranche_sha256": RL.sha(self.tr), "geometrie_sha256": None, "zones": self.zones}}
            )
        )

    def tearDown(self):
        self.t.cleanup()

    def test_criteres_de_p07(self):
        r = RL.revoir(self.tr, self.env, self.d / "absente.json", self.d / "faits.json")
        self.assertEqual(set(r["auto"]), {"soutenu", "court", "pont-bas"})
        self.assertEqual(sorted(r["reste"]), ["couche-courte", "long-haut"])
        self.assertEqual(r["plaques"][1]["min_s"], 6.0)

    def test_revue_ecrite_acceptee_par_controle_envoi_et_humain_conserve(self):
        revue = self.d / "REVUE-ZONES-LOCALES.json"
        revue.write_text(
            json.dumps(
                {
                    "tranche_sha256": RL.sha(self.tr),
                    "geometrie_sha256": None,
                    "zones": {
                        "long-haut": {
                            "decision": "corrige",
                            "justification": "humain",
                            "derniere_couche_verifiee": True,
                            "preuves": [],
                        }
                    },
                }
            )
        )
        res = RL.revoir(self.tr, self.env, revue, self.d / "preuves/faits.json")
        RL.ecrire(res, revue, self.d / "preuves/faits.json")
        doc = json.loads(revue.read_text())
        self.assertEqual(doc["zones"]["long-haut"]["justification"], "humain")  # jamais écrasée
        self.assertTrue(doc["zones"]["court"]["automatique"])
        ev = ZL.evaluer(self.zones, [], self.tr, None, revue)
        self.assertEqual(
            sorted(ev["non_revues"]), ["couche-courte", "long-haut"]
        )  # long-haut : preuve humaine vide, donc refusée

    def test_geometrie_justifiee_seulement_avec_supports_et_couches_suffisantes(self):
        z = {"id": "geometrie:Piece.stl:surplombs", "type": "surplombs", "fichier": "Piece.stl"}
        pl = {1: RL.mesurer_plaque(zipfile.ZipFile(self.tr).read("Metadata/plate_1.gcode").decode())}
        g = {1: zipfile.ZipFile(self.tr).read("Metadata/plate_1.gcode").decode()}
        self.assertIsNone(RL.juger_geometrie(z, pl, g))  # couche de 6 s sur la plaque
        pl[1]["min_s"] = 9.0
        self.assertIsNone(RL.juger_geometrie(z, pl, g))  # support global sans repère de la zone : insuffisant
        local = dict(z, bbox_xy_mm=[45, 45, 60, 60], z_min_mm=0.6, z_max_mm=0.6, separation_support_max_mm=0.21)
        self.assertIn("supports localisés", RL.juger_geometrie(local, pl, g))
        self.assertIsNone(RL.juger_geometrie(dict(z, fichier="Autre.stl"), pl, g))  # objet absent du G-code

    def test_plaque_ambigue_refusee_meme_si_le_sha_est_exact(self):
        """Un alias de plaque ne peut pas prendre la place du membre revu."""
        with zipfile.ZipFile(self.tr, "a") as archive:
            archive.writestr("Metadata/plate_01.gcode", "\n".join(["M83", *couche(0.2, 100)]))
        self.env.write_text(json.dumps(
            {"zones_locales": {"tranche_sha256": RL.sha(self.tr), "geometrie_sha256": None, "zones": self.zones}}
        ))
        with self.assertRaisesRegex(ValueError, "numéro de plaque dupliqué"):
            RL.revoir(self.tr, self.env, self.d / "r.json", self.d / "f.json")

        revue, faits = self.d / "r.json", self.d / "f.json"
        arguments = [
            str(self.tr), "--envoi", str(self.env), "--revue", str(revue), "--faits", str(faits), "--appliquer",
        ]
        self.assertEqual(RL.main(arguments), 2)
        self.assertFalse(revue.exists())
        self.assertFalse(faits.exists())

    def test_plaque_valide_conserve_l_ordre_du_zip(self):
        """La garde des alias ne modifie pas l’ordre historique d’un rapport valide."""
        contenu = "\n".join(["M83", *couche(0.2, 100)])
        with zipfile.ZipFile(self.tr, "w") as archive:
            for numero in (2, 1):
                archive.writestr(f"Metadata/plate_{numero}.gcode", contenu)
        self.env.write_text(json.dumps({"zones_locales": {"tranche_sha256": RL.sha(self.tr), "zones": []}}))
        resultat = RL.revoir(self.tr, self.env, self.d / "r.json", self.d / "f.json")
        self.assertEqual(list(resultat["plaques"]), [2, 1])

    def test_lecture_refuse_un_fichier_remplace_apres_le_sha(self):
        """Un fichier régulier remplacé au même chemin n’hérite pas du SHA vérifié."""
        from unittest.mock import patch

        lire = RL.lire_texte_gcode_3mf

        def remplacer(source, membre, **options):
            copie = self.d / "remplacement.3mf"
            copie.write_bytes(self.tr.read_bytes())
            copie.replace(self.tr)
            return lire(source, membre, **options)

        with patch.object(RL, "lire_texte_gcode_3mf", side_effect=remplacer):
            with self.assertRaisesRegex(ValueError, "modifiée"):
                RL.revoir(self.tr, self.env, self.d / "r.json", self.d / "f.json")

    def test_tranchage_different_refuse(self):
        self.env.write_text(json.dumps({"zones_locales": {"tranche_sha256": "autre", "zones": []}}))
        with self.assertRaises(SystemExit):
            RL.revoir(self.tr, self.env, self.d / "r.json", self.d / "f.json")


@pytest.mark.parametrize("sortie", ["revue", "faits"])
@pytest.mark.parametrize("entree", ["tranche", "envoi"])
def test_cli_refuse_sortie_reliee_a_une_entree_avant_revoir(tmp_path, monkeypatch, capsys, entree, sortie):
    """Le parcours --appliquer ne lit pas les entrées si une sortie partage leurs octets."""
    tranche = tmp_path / "tranche.3mf"
    envoi = tmp_path / "envoi.json"
    tranche.write_bytes(b"archive source intacte")
    envoi.write_bytes(b"rapport envoi intact")
    revue = tmp_path / "revue.json"
    faits = tmp_path / "faits.json"
    source = tranche if entree == "tranche" else envoi
    cible = revue if sortie == "revue" else faits
    try:
        os.link(source, cible)
    except OSError as erreur:
        pytest.skip(f"liens physiques indisponibles dans le temporaire : {erreur}")
    avant = {p: p.read_bytes() for p in (tranche, envoi, cible)}

    def interdit(*_args, **_kwargs):
        raise AssertionError("revoir ne doit pas lire les entrées en cas de collision")

    monkeypatch.setattr(RL, "revoir", interdit)

    assert RL.main([
        str(tranche), "--envoi", str(envoi), "--revue", str(revue),
        "--faits", str(faits), "--appliquer",
    ]) == 2
    assert "collision" in capsys.readouterr().err.lower()

    assert {p: p.read_bytes() for p in avant} == avant
    assert os.path.samefile(source, cible)


def test_cli_refuse_sorties_hardlinkees_avant_revoir(tmp_path, monkeypatch, capsys):
    """Les chemins revue/faits doivent désigner deux fichiers différents, même par hardlink."""
    tranche = tmp_path / "tranche.3mf"
    envoi = tmp_path / "envoi.json"
    tranche.write_bytes(b"archive source intacte")
    envoi.write_bytes(b"rapport envoi intact")
    revue = tmp_path / "revue.json"
    faits = tmp_path / "faits.json"
    revue.write_bytes(b"decision humaine a conserver")
    try:
        os.link(revue, faits)
    except OSError as erreur:
        pytest.skip(f"liens physiques indisponibles dans le temporaire : {erreur}")
    avant = revue.read_bytes()

    def interdit(*_args, **_kwargs):
        raise AssertionError("revoir ne doit pas commencer sur des sorties alias")

    monkeypatch.setattr(RL, "revoir", interdit)

    assert RL.main([
        str(tranche), "--envoi", str(envoi), "--revue", str(revue),
        "--faits", str(faits), "--appliquer",
    ]) == 2
    assert "collision" in capsys.readouterr().err.lower()

    assert revue.read_bytes() == avant
    assert faits.read_bytes() == avant
    assert os.path.samefile(revue, faits)


def test_cli_refuse_chemin_sortie_identique_avant_revoir(tmp_path, monkeypatch, capsys):
    """Deux sorties absentes ne peuvent pas partager le même nom canonique."""
    tranche = tmp_path / "tranche.3mf"
    envoi = tmp_path / "envoi.json"
    tranche.write_bytes(b"archive source intacte")
    envoi.write_bytes(b"rapport envoi intact")
    sortie = tmp_path / "sortie.json"

    def interdit(*_args, **_kwargs):
        raise AssertionError("revoir ne doit pas commencer avant le contrôle des sorties")

    monkeypatch.setattr(RL, "revoir", interdit)

    assert RL.main([
        str(tranche), "--envoi", str(envoi), "--revue", str(sortie),
        "--faits", str(sortie), "--appliquer",
    ]) == 2
    assert "collision" in capsys.readouterr().err.lower()

    assert not sortie.exists()


def test_cli_refuse_sorties_dans_checkout_public_avant_revoir(tmp_path, monkeypatch, capsys):
    """Les deux sorties d'application doivent rester privées ou dans un temporaire hors dépôt."""
    from benchab import chemins

    tranche = tmp_path / "tranche.3mf"
    envoi = tmp_path / "envoi.json"
    tranche.write_bytes(b"archive source intacte")
    envoi.write_bytes(b"rapport envoi intact")
    prive = tmp_path / "prive"
    prive.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))
    monkeypatch.setattr(chemins, "racine_depot", lambda: checkout)

    def interdit(*_args, **_kwargs):
        raise AssertionError("les sorties publiques doivent être refusées avant revoir")

    monkeypatch.setattr(RL, "revoir", interdit)

    assert RL.main([
        str(tranche), "--envoi", str(envoi), "--revue", str(checkout / "revue.json"),
        "--faits", str(prive / "faits.json"), "--appliquer",
    ]) == 2
    assert "privé ou temporaire" in capsys.readouterr().err.lower()

    assert not (checkout / "revue.json").exists()
    assert not (prive / "faits.json").exists()


if __name__ == "__main__":
    unittest.main()
