"""Témoins synthétiques du contrôle local des coutures."""

import hashlib
import zipfile

from benchab.controles.coutures import analyser


def _gcode(*, second_layer=True, hidden=False):
    """Construit deux boucles carrées identifiables, sans lancer de trancheur."""
    lignes = ["G90", "M83", "EXCLUDE_OBJECT_START NAME=PieceA"]
    for couche, z in enumerate((0.2, 0.4) if second_layer else (0.2,)):
        lignes.extend(
            [
                ";LAYER_CHANGE",
                f"G0 X0 Y{10 if hidden else 0} Z{z}",
                ";TYPE:Outer wall",
                "G1 X10 Y0 E1 F600" if not hidden else "G1 X10 Y10 E1 F600",
                "G1 X10 Y10 E1" if not hidden else "G1 X10 Y0 E1",
                "G1 X0 Y10 E1" if not hidden else "G1 X0 Y0 E1",
                "G1 X0 Y0 E1" if not hidden else "G1 X0 Y10 E1",
            ]
        )
    lignes.append("EXCLUDE_OBJECT_END NAME=PieceA")
    return "\n".join(lignes) + "\n"


def _archive(tmp_path, contenu=None):
    """Écrit un 3MF minimal temporaire avec une plaque et son G-code."""
    source = tmp_path / "coutures.3mf"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", contenu or _gcode())
    return source


def _declaration(source, *, regions=None):
    """Déclare une face visible et une face cachée en coordonnées spatiales mm."""
    return {
        "schema": 1,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "plaque": 1,
        "objet_id": "PieceA",
        "tolerances_mm": {"fermeture_boucle": 0.02, "classement_region": 0.05, "alignement": 0.1},
        "regions": regions
        or [
            {
                "id": "face-avant",
                "visibilite": "visible",
                "polygone_xy_mm": [[-1, -1], [11, -1], [11, 1], [-1, 1]],
                "z_min_mm": 0,
                "z_max_mm": 1,
            },
            {
                "id": "face-arriere",
                "visibilite": "cachee",
                "polygone_xy_mm": [[-1, 9], [11, 9], [11, 11], [-1, 11]],
                "z_min_mm": 0,
                "z_max_mm": 1,
            },
        ],
    }


def test_deux_couches_alignees_dans_face_visible_donnent_warn_local(tmp_path):
    source = _archive(tmp_path)
    avant = source.read_bytes()
    declaration = _declaration(source)
    rapport = analyser(source, declaration)
    assert source.read_bytes() == avant
    assert rapport["statut"] == "WARN"
    assert rapport["source_sha256"] == declaration["source_sha256"]
    assert rapport["regions"][0]["polygone_xy_mm"]
    assert rapport["boucles"][0]["fermee"] is True
    assert rapport["boucles"][0]["region_id"] == "face-avant"
    assert len(rapport["groupes_alignes"]) == 1
    assert rapport["groupes_alignes"][0]["statut"] == "WARN"
    assert len(rapport["groupes_alignes"][0]["couches"]) == 2


def test_boucle_visible_isolee_n_est_pas_dite_invisible(tmp_path):
    source = _archive(tmp_path, _gcode(second_layer=False))
    rapport = analyser(source, _declaration(source))
    assert rapport["statut"] == "PASS"
    assert rapport["boucles"][0]["visibilite"] == "visible"
    assert rapport["groupes_alignes"] == []


def test_boucles_sur_face_cachee_ne_donnent_pas_warn_couture(tmp_path):
    source = _archive(tmp_path, _gcode(hidden=True))
    rapport = analyser(source, _declaration(source))
    assert rapport["statut"] == "PASS"
    assert all(b["visibilite"] == "cachee" for b in rapport["boucles"])


def test_source_incorrecte_et_regions_ambigues_sont_refusees(tmp_path):
    source = _archive(tmp_path)
    declaration = _declaration(source)
    declaration["source_sha256"] = "0" * 64
    rapport = analyser(source, declaration)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["inconnues"]

    declaration = _declaration(source)
    declaration["regions"].append(dict(declaration["regions"][0], id="doublon"))
    rapport = analyser(source, declaration)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["inconnues"]


def test_boucle_ouverte_reste_inconnue(tmp_path):
    source = _archive(tmp_path, _gcode().replace("G1 X0 Y0 E1\n", "G1 X0 Y2 E1\n"))
    rapport = analyser(source, _declaration(source))
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert any(item["cause"] == "boucle_non_fermee" for item in rapport["inconnues"])


def test_plafonds_de_regions_et_sommets(tmp_path):
    source = _archive(tmp_path)
    declaration = _declaration(source)
    declaration["regions"] *= 65
    rapport = analyser(source, declaration)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert any(item["cause"] == "budget_declaration_depasse" for item in rapport["inconnues"])


def test_reglages_couture_embarques_restent_informatifs(tmp_path):
    source = tmp_path / "coutures.3mf"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", _gcode())
        archive.writestr(
            "Metadata/project_settings.config",
            '{"seam_position":"aligned","wipe_on_loops":"1","wipe_before_external_loop":"0"}',
        )
    rapport = analyser(source, _declaration(source))
    assert rapport["reglages_couture_embarques"] == {
        "statut": "INFORMATIF_BRUT",
        "source_membre": "Metadata/project_settings.config",
        "valeurs_embarquees": {
            "seam_position": "aligned",
            "wipe_on_loops": "1",
            "wipe_before_external_loop": "0",
        },
    }
    assert rapport["statut"] == "WARN"


def test_depart_sur_frontiere_reste_inconnu(tmp_path):
    source = _archive(tmp_path)
    declaration = _declaration(source)
    declaration["regions"][0]["polygone_xy_mm"] = [[0, -1], [11, -1], [11, 1], [0, 1]]
    rapport = analyser(source, declaration)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert any(item["cause"] == "frontiere_region" for item in rapport["inconnues"])


def test_polygone_auto_intersecte_est_refuse(tmp_path):
    source = _archive(tmp_path)
    declaration = _declaration(source)
    declaration["regions"][0]["polygone_xy_mm"] = [[-1, -1], [11, 1], [11, -1], [-1, 1]]
    rapport = analyser(source, declaration)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert any(item["cause"] == "source_ou_declaration_invalide" for item in rapport["inconnues"])


def test_couche_absente_ne_peut_pas_creer_un_alignement(tmp_path):
    source = _archive(tmp_path, _gcode().replace(";LAYER_CHANGE\n", ""))
    rapport = analyser(source, _declaration(source))
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["groupes_alignes"] == []
    assert all(b["couche"] == -1 and b["visibilite"] == "inconnue" for b in rapport["boucles"])
    assert any(item["cause"] == "couche_non_identifiee" for item in rapport["inconnues"])


def test_identite_objet_repetee_n_est_pas_agregee_en_warn(tmp_path):
    contenu = _gcode(second_layer=False) + _gcode(second_layer=False)
    source = _archive(tmp_path, contenu)
    rapport = analyser(source, _declaration(source))
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["groupes_alignes"] == []
    assert any(item["cause"] == "identite_objet_repetee_ou_ambigue" for item in rapport["inconnues"])


def test_g92_conserve_la_region_spatiale_du_depart(tmp_path):
    """G92 et un remboursement de rétraction n’ajoutent ni ne déplacent une couture."""
    source = _archive(
        tmp_path,
        "G90\nM83\nEXCLUDE_OBJECT_START NAME=PieceA\n;LAYER_CHANGE\n"
        "G0 X0 Y0 Z0.2\nG1 E-0.5 F600\nG92 X100 Y100\n;TYPE:Outer wall\n"
        "G1 E0.5 F600\nG1 X110 Y100 E1 F600\nG1 X110 Y110 E1\nG1 X100 Y110 E1\nG1 X100 Y100 E1\n"
        "EXCLUDE_OBJECT_END NAME=PieceA\n",
    )
    rapport = analyser(source, _declaration(source))
    assert rapport["statut"] == "PASS"
    boucle, = rapport["boucles"]
    assert boucle["point_depart_xyz_mm"] == [0.0, 0.0, 0.2]
    assert boucle["region_id"] == "face-avant"


def test_dette_de_retraction_ne_fabrique_pas_de_boucle_fermee(tmp_path):
    """Une reprise de dépôt éloignée du retour de boucle reste hors contrat."""
    contenu = _gcode(second_layer=False).replace(
        "G1 X10 Y0 E1 F600", "G1 E-0.5 F600\nG1 X10 Y0 E1 F600",
    )
    source = _archive(tmp_path, contenu)
    rapport = analyser(source, _declaration(source))
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert not rapport["groupes_alignes"]
    assert all(not b["fermee"] for b in rapport["boucles"])


def test_arc_de_paroi_reste_non_verifie(tmp_path):
    """Le lot linéaire ne substitue pas une corde au dépôt sur l’arc."""
    contenu = _gcode().replace("G1 X10 Y0 E1 F600", "G2 X10 Y0 I5 J0 E1 F600")
    source = _archive(tmp_path, contenu)
    rapport = analyser(source, _declaration(source))
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert not rapport["groupes_alignes"]
    assert rapport["inconnues"]
