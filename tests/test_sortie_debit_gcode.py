"""Publication du rapport U25 sans écrasement de source ni de preuve."""

import json
from zipfile import ZipFile

from benchab.controles import debit_gcode


def _archive(tmp_path, filament="1"):
    """Crée un 3MF tranché de test ; aucune impression ni profil réel."""
    source = tmp_path / "debit.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps({"filament_diameter": ["1.75"], "filament_max_volumetric_speed": ["12"]}),
        )
        archive.writestr(
            "Metadata/slice_info.config",
            f'<config><plate><metadata key="index" value="1"/><filament id="{filament}"/></plate></config>',
        )
        archive.writestr("Metadata/plate_1.gcode", "G90\nM83\nT0\nG1 X10 E0.4 F900\n")
    return source


def test_source_ne_peut_pas_servir_de_sortie(tmp_path, capsys):
    """Le code de refus ne remplace pas l’archive par son propre diagnostic."""
    source = _archive(tmp_path)
    avant = source.read_bytes()

    assert debit_gcode.main([str(source), "--sortie", str(source)]) == 2

    assert source.read_bytes() == avant
    assert "Erreur technique" in capsys.readouterr().err


def test_preuve_existante_ou_liee_ne_peut_pas_etre_ecrasee(tmp_path):
    """Une destination existante ou liée garde ses octets, même avec un verdict FAIL."""
    source = _archive(tmp_path)
    preuve = tmp_path / "precedent.json"
    preuve.write_text("preuve antérieure", encoding="utf-8")
    lien = tmp_path / "lien.json"
    lien.symlink_to(preuve)

    for cible in (preuve, lien):
        assert debit_gcode.main([str(source), "--sortie", str(cible)]) == 2
        assert preuve.read_text(encoding="utf-8") == "preuve antérieure"
    assert lien.is_symlink()


def test_sortie_neuve_garde_le_rapport_et_le_code_u25(tmp_path):
    """Le JSON sauvegardé reste égal au rapport du contrôle sans publication."""
    source = _archive(tmp_path)
    avant = source.read_bytes()
    attendu = debit_gcode.analyser(source)
    sortie = tmp_path / "rapport.json"

    assert debit_gcode.main([str(source), "--sortie", str(sortie)]) == attendu["code"]

    assert json.loads(sortie.read_bytes()) == attendu
    assert source.read_bytes() == avant


def test_sortie_publique_du_checkout_est_refusee(tmp_path, monkeypatch):
    """Même un checkout jetable ne reçoit pas le rapport dans sa partie publique."""
    source = _archive(tmp_path)
    depot = tmp_path / "depot"
    depot.mkdir()
    monkeypatch.delenv("BENCHAB_PRIVE", raising=False)
    monkeypatch.setattr("benchab.chemins.racine_depot", lambda: depot)
    sortie = depot / "rapport.json"

    assert debit_gcode.main([str(source), "--sortie", str(sortie)]) == 2
    assert not sortie.exists()


def test_resume_et_detail_preservent_le_verdict_et_la_preuve(tmp_path, capsys):
    """Les deux affichages gardent le même rapport, y compris ses erreurs."""
    source = _archive(tmp_path)
    attendu = debit_gcode.analyser(source)
    for mode in ("--resume", "--detail"):
        sortie = tmp_path / f"{mode}.json"
        assert debit_gcode.main([str(source), "--sortie", str(sortie), mode]) == attendu["code"]
        affiche = json.loads(capsys.readouterr().out)
        assert json.loads(sortie.read_bytes()) == attendu
        if mode == "--detail":
            assert affiche == attendu
        else:
            assert affiche["statut"] == attendu["statut"]
            assert affiche["rapport"] == str(sortie)
            assert affiche["plaques"][0]["plaque"] == 1
            assert affiche["plaques"][0]["erreurs"]
            assert len(json.dumps(affiche, ensure_ascii=False)) < len(json.dumps(attendu, ensure_ascii=False))


def test_modes_avec_rapport_requis_refuses_avant_lecture(tmp_path, capsys):
    """Un affichage avec rapport promis doit refuser avant l’analyse sans destination."""
    source = tmp_path / "absente.3mf"
    assert debit_gcode.main([str(source), "--resume"]) == 2
    erreur = capsys.readouterr().err
    assert "--sortie" in erreur and "absente.3mf" not in erreur


def test_u25_localise_le_pic_et_garde_les_deux_plafonds(tmp_path):
    """Un dépôt isolé dépasse le plafond embarqué, avec sa longueur et son rôle."""
    source = tmp_path / "pics.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps({
            "filament_diameter": ["1.75"], "filament_max_volumetric_speed": ["10.2"],
        }))
        archive.writestr("Metadata/slice_info.config", '<config><plate><metadata key="index" value="1"/>'
                         '<filament id="1"/><object name="témoin"/></plate></config>')
        archive.writestr("Metadata/plate_1.gcode",
                         "G90\nM83\nT0\n;LAYER_CHANGE\nG1 Z0.2\n;TYPE:Inner wall\n"
                         "EXCLUDE_OBJECT_START NAME=témoin\nG1 X1 E0.488 F600\nG1 X21 E1 F600\n")
    rapport = debit_gcode.analyser(source, overrides={"1": 12})
    outil = rapport["plaques"][0]["objets"][0]["outils"][0]
    assert rapport["statut"] == "PASS"
    assert outil["plafond_embarque_mm3_s"] == 10.2
    assert outil["depassements_plafond_embarque"] == 1
    assert outil["longueur_hors_plafond_embarque_mm"] == 1
    assert outil["pic"]["type_trajet"] == "Inner wall"
    assert outil["pic"]["longueur_mm"] == 1


def test_depot_support_compte_hors_objet_et_sans_deplacement_a_vide(tmp_path):
    """Le G-code mesure un dépôt hors objet et ignore un trajet sous la même étiquette."""
    source = _archive_objets(
        tmp_path, ["objet"],
        "G90\nM83\nT0\n;TYPE:Support\nG1 X10 F900\nG1 X20 E0.4 F900\n"
        ";TYPE:Outer wall\nEXCLUDE_OBJECT_START NAME=objet\nG1 X30 E0.4 F900\n",
    )
    plaque = debit_gcode.analyser(source)["plaques"][0]
    assert plaque["segments_depot_support"] == 1
    assert plaque["trajets_supports_mm"] == 10


def _archive_objets(tmp_path, noms, gcode, *, vase=False):
    """Crée une plaque synthétique avec les noms et le réglage vase déclarés."""
    from xml.etree import ElementTree as ET

    racine = ET.Element("config")
    plaque = ET.SubElement(racine, "plate")
    ET.SubElement(plaque, "metadata", key="index", value="1")
    ET.SubElement(plaque, "filament", id="1")
    for nom in noms:
        ET.SubElement(plaque, "object", name=nom)
    source = tmp_path / "objets.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/slice_info.config", ET.tostring(racine))
        archive.writestr("Metadata/project_settings.config", json.dumps({
            "filament_diameter": ["1.75"], "filament_max_volumetric_speed": ["12"],
            "spiral_mode": "1" if vase else "0",
        }))
        archive.writestr("Metadata/plate_1.gcode", "G90\nM83\nT0\n" + gcode)
    return source


def test_u25_rapproche_nom_moteur_avec_espaces_et_parentheses(tmp_path):
    """La transformation native préserve le nom humain dans le rapport."""
    source = _archive_objets(tmp_path, ["Pièce filetée (A)"],
                            "EXCLUDE_OBJECT_START NAME=Pièce_filetée_A_id_0_copy_0\nG1 X10 E0.4 F900\n")
    resultat = debit_gcode.analyser(source)
    assert resultat["statut"] == "PASS"
    assert resultat["plaques"][0]["objets"][0]["objet"] == "Pièce filetée (A)"


def test_u25_ne_devine_pas_un_nom_moteur_ambigu(tmp_path):
    """Deux noms produisant la même étiquette restent non vérifiés."""
    source = _archive_objets(tmp_path, ["Pièce A", "Pièce_A"],
                            "EXCLUDE_OBJECT_START NAME=Pièce_A_id_0_copy_0\nG1 X10 E0.4 F900\n")
    assert debit_gcode.analyser(source)["statut"] == "NON VÉRIFIÉ"


def test_u25_vase_mesure_objet_unique_sans_attribuer_la_purge(tmp_path):
    """Le dépôt de la spirale est mesuré, la purge de démarrage est écartée."""
    source = _archive_objets(
        tmp_path, ["Coque vase"],
        ";TYPE:Custom\nG1 X10 E10 F900\n;TYPE:Outer wall\nG1 X20 Z0.2 E0.4 F900\nG1 X30 Z0.3 E0.4 F900\n",
        vase=True,
    )
    resultat = debit_gcode.analyser(source)
    assert resultat["statut"] == "PASS"
    assert 0 < resultat["plaques"][0]["objets"][0]["debit_max"] < 12


def test_u25_vase_plusieurs_objets_sans_etiquette_reste_non_verifie(tmp_path):
    """Le nombre de corps ne se déduit pas à partir de la spirale."""
    source = _archive_objets(tmp_path, ["Coque", "Second corps"], ";TYPE:Outer wall\nG1 X10 Z0.2 E0.4 F900\n",
                            vase=True)
    assert debit_gcode.analyser(source)["statut"] == "NON VÉRIFIÉ"


def test_u25_filament_unique_invalide_ecrit_un_rapport_structure(tmp_path, capsys):
    """Une déclaration de filament illisible rend un verdict lié à la plaque."""
    source = _archive(tmp_path, filament="x")
    rapport = tmp_path / "rapport.json"
    assert debit_gcode.main([str(source), "--sortie", str(rapport)]) == 2
    capsys.readouterr()
    resultat = json.loads(rapport.read_text())
    assert resultat["statut"] == "FAIL"
    plaque, = resultat["plaques"]
    assert plaque["plaque"] == 1
    assert "filaments déclarés absents ou invalides" in plaque["erreurs"]
