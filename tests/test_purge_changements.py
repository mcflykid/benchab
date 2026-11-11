"""Transitions dirigées et matrice conditionnelle ; aucun réglage machine exécuté."""

import json
from zipfile import ZipFile

import pytest

from benchab.controles.purge_changements import analyser, analyser_lignes
from benchab.reglages import lire_matrice_purge, valeur_brute


def _reglages(matrice=None):
    return {
        "filament_settings_id": ["Profil noir", "Profil blanc"],
        "filament_colour": ["#000000", "#ffffff"],
        "flush_volumes_matrix": matrice if matrice is not None else ["0", "12", "31", "0"],
    }


def test_paires_directionnelles_ne_confondent_pas_la_premiere_selection_ni_une_repetition():
    """Une matrice asymétrique prouve seulement la lecture source vers destination."""
    texte = "T0\nT1\nT1\nT0\nT1\n"
    rapport = analyser_lignes(texte.splitlines(), _reglages())
    assert rapport["changements_explicites"] == 3
    assert rapport["paires"] == [
        {"depart": 0, "arrivee": 1, "nombre": 2, "valeur_unitaire_matrice": 12.0},
        {"depart": 1, "arrivee": 0, "nombre": 1, "valeur_unitaire_matrice": 31.0},
    ]
    assert rapport["somme_conditionnelle_matrice"] == 55.0
    assert rapport["purge_physique_mm3"] is None


def test_matrice_trois_par_trois_ne_qualifie_pas_un_temoin_mono():
    """Ne pas choisir une sous-matrice depuis la seule longueur du tableau."""
    donnees = _reglages(["0"] * 9)
    donnees["filament_settings_id"] = ["Profil seul"]
    donnees["filament_colour"] = ["#000000"]
    rapport = analyser_lignes(["T0", "T0"], donnees)
    assert rapport["changements_explicites"] == 0
    assert rapport["matrice"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["somme_conditionnelle_matrice"] is None


def test_selection_hors_slots_interrompt_la_couverture():
    """Un indice inconnu ne doit pas fabriquer une transition entre deux slots valides."""
    rapport = analyser_lignes(["T0", "T255", "T1"], _reglages())
    assert rapport["changements_explicites"] == 2
    assert rapport["changements_hors_contexte"] == 2
    assert rapport["somme_conditionnelle_matrice"] is None


def test_lecture_conditionnelle_ne_change_pas_le_type_historique():
    """Le dictionnaire n'autorise toujours pas la lecture générique de la matrice historique."""
    donnees = _reglages()
    assert lire_matrice_purge(donnees)["statut"] == "PASS"
    with pytest.raises(ValueError, match="NON VÉRIFIÉ"):
        valeur_brute(donnees, "flush_volumes_matrix")
    assert lire_matrice_purge(_reglages(["0", "12", "31", "2"]))["statut"] == "NON VÉRIFIÉ"
    assert lire_matrice_purge(_reglages(["0", "NaN", "31", "0"]))["statut"] == "NON VÉRIFIÉ"


def test_archive_multiplaque_garde_les_sequences_separees(tmp_path):
    """La première sélection de la seconde plaque n’est pas un changement de la première."""
    source = tmp_path / "bicolore.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(_reglages()))
        archive.writestr("Metadata/plate_1.gcode", "T0\nT1\n")
        archive.writestr("Metadata/plate_2.gcode", "T0\n")
    avant = source.read_bytes()

    rapport = analyser(source)

    assert rapport["statut"] == "PASS"
    assert [p["changements_explicites"] for p in rapport["plaques"]] == [1, 0]
    assert [p["somme_conditionnelle_matrice"] for p in rapport["plaques"]] == [12.0, 0.0]
    assert rapport["volume_gcode_attribue_mm3"] is None
    assert source.read_bytes() == avant


def test_empreinte_purge_depuis_archive_deja_ouverte(tmp_path, monkeypatch):
    """Le SHA et les transitions doivent provenir de la même archive ouverte."""
    import hashlib

    from benchab.controles import purge_changements

    source = tmp_path / "purge.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(_reglages()))
        archive.writestr("Metadata/plate_1.gcode", "T0\nT1\n")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    observations = []
    sha_archive = purge_changements.Archive3MF.sha256

    def compter_sha(archive, **options):
        """Trace l’unique empreinte depuis le lecteur déjà ouvert."""
        resultat = sha_archive(archive, **options)
        observations.append(resultat)
        return resultat

    monkeypatch.setattr(purge_changements.Archive3MF, "sha256", compter_sha)
    rapport = analyser(source)
    assert observations == [sha] == [rapport["source_sha256"]]
    assert rapport["somme_conditionnelle_matrice"] == 12.0


def test_cli_resume_purge_conserve_le_rapport_et_son_statut(tmp_path, capsys):
    """Résumé, détail et code restent reliés au même 3MF, sans modifier la source."""
    from benchab.cli import main as cli_main

    prive = tmp_path / "atelier"
    assert cli_main(["--prive", str(prive), "init"]) == 0
    capsys.readouterr()
    source = prive / "temoin.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(_reglages()))
        archive.writestr("Metadata/plate_1.gcode", "T0\nT1\n")
    avant = source.read_bytes()
    rapport = prive / "purge.json"
    commande = ["--prive", str(prive), "controle", "purge-changements", str(source)]
    assert cli_main(commande) == 0
    complet = json.loads(capsys.readouterr().out)
    assert cli_main([*commande, "--resume", "--sortie", str(rapport)]) == 0
    resume = json.loads(capsys.readouterr().out)
    assert resume["statut"] == "PASS"
    assert resume["changements_explicites"] == 1
    assert resume["purge_physique_mm3"] is None
    assert resume["rapport"] == str(rapport)
    assert json.loads(rapport.read_text()) == complet
    assert source.read_bytes() == avant
    assert cli_main([*commande, "--resume", "--sortie", str(rapport)]) == 2
    assert "Sortie existante" in capsys.readouterr().err
    assert json.loads(rapport.read_text()) == complet
    assert cli_main([*commande, "--resume"]) == 2
    assert "--resume exige --sortie" in capsys.readouterr().err


def test_cli_resume_purge_ne_transforme_pas_une_matrice_indeterminee_en_pass(tmp_path, capsys):
    """Un résultat conditionnel absent conserve le statut et le code historiques."""
    from benchab.cli import main as cli_main

    prive = tmp_path / "atelier"
    assert cli_main(["--prive", str(prive), "init"]) == 0
    capsys.readouterr()
    source, rapport = prive / "temoin.3mf", prive / "purge.json"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "T0\nT1\n")
    assert cli_main([
        "--prive", str(prive), "controle", "purge-changements", str(source),
        "--resume", "--sortie", str(rapport),
    ]) == 2
    resume = json.loads(capsys.readouterr().out)
    assert resume["statut"] == "NON VÉRIFIÉ"
    assert resume["somme_conditionnelle_matrice"] is None
    assert json.loads(rapport.read_text())["plaques"][0]["changements_hors_contexte"] == 1


def test_observer_tour_mesure_uniquement_les_depots_nominalement_etiquetes(tmp_path):
    """Un dépôt dans la tour reste distinct de la matrice et de la purge réelle."""
    import math

    source = tmp_path / "tour.3mf"
    reglages = {**_reglages(), "filament_diameter": [1.75, 2.85]}
    lignes = (
        "M83\nT0\nG1 X1 F600\nT1\n;TYPE:Prime tower\nG1 E-1\n"
        "G1 X2 E0.5\nG1 X3 E1.5\nG1 E2\n;TYPE:Outer wall\nG1 X4 E1\n"
    )
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(reglages))
        archive.writestr("Metadata/plate_1.gcode", lignes)
    assert "observation_tour" not in analyser(source)["plaques"][0]
    rapport = analyser(source, observer_tour=True)
    mesure = rapport["plaques"][0]["observation_tour"]
    assert mesure["statut"] == "PASS"
    assert mesure["outils"] == [
        {"outil": 1, "segments": 1, "volume_nominal_mm3": pytest.approx(math.pi * (2.85 / 2)**2)},
    ]
    assert rapport["somme_conditionnelle_matrice"] == 12.0
    assert rapport["volume_gcode_attribue_mm3"] is None
    assert rapport["purge_physique_mm3"] is None


@pytest.mark.parametrize(("lignes", "diametres"), [
    ("M83\nT1\nG1 X1 E1 F600\n", [1.75, 2.85]),
    ("M83\n;TYPE:Prime tower\nG1 X1 E1 F600\n", [1.75, 2.85]),
    ("M83\nT1\n;TYPE:Prime tower\nG1 X1 E1 F600\n", [1.75]),
    ("M83\nT1\n;TYPE:Prime tower\nG1 X1 E1 F600\nG20\n", [1.75, 2.85]),
    ("M83\nG9111 extruderTemp=200\nT1\n;TYPE:Prime tower\nG1 X1 E1 F600\n", [1.75, 2.85]),
])
def test_observer_tour_refuse_de_qualifier_une_couverture_incomplete(tmp_path, lignes, diametres):
    """Sans rôle, outil, diamètre exact ou lecture qualifiée, le total reste indéterminé."""
    source = tmp_path / "tour.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps({
            **_reglages(), "filament_diameter": diametres,
        }))
        archive.writestr("Metadata/plate_1.gcode", lignes)
    mesure = analyser(source, observer_tour=True)["plaques"][0]["observation_tour"]
    assert mesure["statut"] == "NON VÉRIFIÉ"
    assert mesure["volume_nominal_mm3"] is None
