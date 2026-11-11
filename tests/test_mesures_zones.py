"""Contrat des mesures linéaires signées et bornées sur G-code source."""

from benchab.controles.mesures_zones import recalculer_mesure


def test_recalcule_recouvrement_signe_sur_axe_local():
    gcode = (
        "G90\nM83\n;LAYER_CHANGE\n;WIDTH:0.4\n;TYPE:Bridge\n"
        "G1 X0 Y0 F1200\nG1 X10 Y0 E1\n;LAYER_CHANGE\n;WIDTH:0.4\n"
        ";TYPE:Overhang wall\nG1 X0 Y0.3 F1200\nG1 X10 Y0.3 E1\n"
    )
    resultat = recalculer_mesure(
        gcode,
        {"ligne_source": 7, "couche_source": 0, "ligne_cible": 12, "couche_cible": 1,
         "axe_local": [0, 1], "valeur_declaree_mm": -0.1, "tolerance_mm": 0.001},
    )
    assert resultat["statut"] == "PASS"
    assert resultat["recalculee_mm"] == -0.1


def test_recul_le_long_de_l_axe_est_mesure_sur_deux_lignes_locales():
    gcode = (
        "G90\nM83\n;LAYER_CHANGE\n;WIDTH:0.4\n;TYPE:Bridge\n"
        "G1 X0 Y0 F1200\nG1 X10 Y0 E1\n;LAYER_CHANGE\n;WIDTH:0.4\n"
        ";TYPE:Overhang wall\nG1 X0 Y3 F1200\nG1 X10 Y3 E1\n"
    )
    resultat = recalculer_mesure(
        gcode,
        {"ligne_source": 7, "couche_source": 0, "ligne_cible": 12, "couche_cible": 1,
         "axe_local": [0, 1], "valeur_declaree_mm": 2.6, "tolerance_mm": 0.001},
    )
    assert resultat["statut"] == "PASS"
    assert resultat["recalculee_mm"] == 2.6


def test_axe_invalide_largeur_absente_ou_couche_incoherente_restent_non_verifiees():
    gcode = "G90\nM83\n;LAYER_CHANGE\n;TYPE:Bridge\nG1 X0 Y0 F1200\nG1 X5 Y0 E1\n"
    declaration = {"ligne_source": 6, "couche_source": 0, "ligne_cible": 6, "couche_cible": 0,
                   "axe_local": [0, 0], "valeur_declaree_mm": 0, "tolerance_mm": 0}
    assert recalculer_mesure(gcode, declaration)["statut"] == "NON VÉRIFIÉ"


def test_contradiction_gcode_sha_valide_bloque_malgre_justification_humaine(tmp_path):
    import hashlib
    import json
    from zipfile import ZipFile

    from benchab.controles.zones_locales import analyser_gcode, evaluer

    gcode = (
        "G90\nM83\n;LAYER_CHANGE\n;WIDTH:0.4\n;TYPE:Bridge\n"
        "G1 X0 Y0 F1200\nG1 X10 Y0 E1\n;LAYER_CHANGE\n;WIDTH:0.4\n"
        ";TYPE:Overhang wall\nG1 X0 Y0.3 F1200\nG1 X10 Y0.3 E1\n"
    ).encode()
    tranche = tmp_path / "tranche.3mf"
    with ZipFile(tranche, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", gcode)
    zones = analyser_gcode(gcode.decode())["zones"]
    zone_id = zones[0]["id"]
    preuve = tmp_path / "preuve.txt"
    preuve.write_text("Décision humaine conservée")
    revue = tmp_path / "revue.json"
    declaration = {
        "zone_id": zone_id, "plaque": 1, "gcode_sha256": hashlib.sha256(gcode).hexdigest(),
        "ligne_source": 7, "couche_source": 0, "ligne_cible": 12, "couche_cible": 1,
        "axe_local": [0, 1], "valeur_declaree_mm": 0.1, "tolerance_mm": 0.001,
    }
    revue.write_text(json.dumps({
        "tranche_sha256": hashlib.sha256(tranche.read_bytes()).hexdigest(), "geometrie_sha256": None,
        "zones": {zone_id: {"decision": "justifie", "justification": "historique",
                               "derniere_couche_verifiee": True,
                               "preuves": [{"fichier": preuve.name,
                                "sha256": hashlib.sha256(preuve.read_bytes()).hexdigest()}]}},
        "mesures_recul_recouvrement": [declaration, None],
    }))
    resultat = evaluer(zones, [], tranche, revue=revue)
    assert resultat["statut"] == "FAIL"
    assert resultat["mesures_recul_recouvrement"][0]["statut"] == "FAIL"
    assert resultat["mesures_recul_recouvrement"][1]["statut"] == "NON VÉRIFIÉ"
    assert json.loads(revue.read_text())["zones"][zone_id]["decision"] == "justifie"
    from benchab.controles.mesures_zones import verifier_mesures_declarees
    zone_sans_bbox = {key: value for key, value in zones[0].items() if key != "bbox_xy_mm"}
    assert verifier_mesures_declarees(tranche, [zone_sans_bbox], [declaration])[0]["statut"] == "FAIL"


def test_revue_refuse_mesure_dont_cible_est_hors_bbox_ou_objet(tmp_path):
    import hashlib
    from zipfile import ZipFile

    from benchab.controles.mesures_zones import verifier_mesures_declarees
    from benchab.controles.zones_locales import analyser_gcode

    source = (
        "G90\nM83\n;LAYER_CHANGE\nEXCLUDE_OBJECT_START NAME=piece-a\n"
        ";WIDTH:0.4\n;TYPE:Bridge\nG1 X0 Y0 F1200\nG1 X10 Y0 E1\n"
        ";LAYER_CHANGE\n;WIDTH:0.4\n;TYPE:Overhang wall\n"
        "G1 X0 Y0.3 F1200\nG1 X10 Y0.3 E1\nEXCLUDE_OBJECT_END NAME=piece-a\n"
    ).encode()
    tranche = tmp_path / "cible.3mf"
    with ZipFile(tranche, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", source)
    declaration = {
        "zone_id": "z", "plaque": 1, "gcode_sha256": hashlib.sha256(source).hexdigest(),
        "ligne_source": 8, "couche_source": 0, "ligne_cible": 13, "couche_cible": 1,
        "axe_local": [0, 1], "valeur_declaree_mm": -0.1, "tolerance_mm": 0.001,
    }
    zone = next(z for z in analyser_gcode(source.decode())["zones"] if z["type"] == "Overhang wall")
    declaration["zone_id"] = zone["id"]
    zone_hors_bbox = {**zone, "bbox_xy_mm": [0, 0.5, 10, 0.5]}
    resultat = verifier_mesures_declarees(tranche, [zone_hors_bbox], [declaration])
    assert resultat[0]["statut"] == "NON VÉRIFIÉ"
    assert verifier_mesures_declarees(tranche, [zone], [declaration])[0]["statut"] == "PASS"
    zone_autre_objet = {**zone, "objet": "piece-b"}
    assert verifier_mesures_declarees(tranche, [zone_autre_objet], [declaration])[0]["statut"] == "NON VÉRIFIÉ"


def test_revue_refuse_cordons_sans_recouvrement_orthogonal_local(tmp_path):
    import hashlib
    from zipfile import ZipFile

    from benchab.controles.mesures_zones import verifier_mesures_declarees

    source = (
        "G90\nM83\n;LAYER_CHANGE\n;WIDTH:0.4\n;TYPE:Bridge\n"
        "G1 X50 Y0 F1200\nG1 X60 Y0 E1\n;LAYER_CHANGE\n;WIDTH:0.4\n"
        ";TYPE:Overhang wall\nG1 X0 Y0.3 F1200\nG1 X10 Y0.3 E1\n"
    ).encode()
    tranche = tmp_path / "eloignes.3mf"
    with ZipFile(tranche, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", source)
    declaration = {
        "zone_id": "z", "plaque": 1, "gcode_sha256": hashlib.sha256(source).hexdigest(),
        "ligne_source": 7, "couche_source": 0, "ligne_cible": 12, "couche_cible": 1,
        "axe_local": [0, 1], "valeur_declaree_mm": -0.1, "tolerance_mm": 0.001,
    }
    zone = {"id": "z", "plaque": 1, "type": "Overhang wall", "bbox_xy_mm": [0, 0.3, 10, 0.3], "objet": None}
    assert verifier_mesures_declarees(tranche, [zone], [declaration])[0]["statut"] == "NON VÉRIFIÉ"


def test_conteneurs_malformes_ou_zone_inconnue_restent_visibles_et_bloquants(tmp_path):
    import json
    from zipfile import ZipFile

    from benchab.controles.zones_locales import evaluer

    tranche = tmp_path / "vide.3mf"
    with ZipFile(tranche, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", b"G90\nM83\n")
    zone = {"id": "z", "plaque": 1, "type": "Overhang wall", "bbox_xy_mm": [0, 0, 1, 1], "objet": None}
    preuve = tmp_path / "preuve.txt"
    preuve.write_text("preuve")
    import hashlib

    revue = tmp_path / "revue.json"
    declarations = (
        None, "", {}, [None], [{"zone_id": "absente", "plaque": 1}], [{"zone_id": "z", "plaque": True}]
    )
    for declaration in declarations:
        revue.write_text(json.dumps({
            "tranche_sha256": hashlib.sha256(tranche.read_bytes()).hexdigest(), "geometrie_sha256": None,
            "zones": {"z": {"decision": "justifie", "justification": "humain",
                              "derniere_couche_verifiee": True,
                              "preuves": [{"fichier": preuve.name,
                                           "sha256": hashlib.sha256(preuve.read_bytes()).hexdigest()}]}},
            "mesures_recul_recouvrement": declaration,
        }))
        resultat = evaluer([zone], [], tranche, revue=revue)
        assert resultat["statut"] == "FAIL"
        assert resultat["mesures_recul_recouvrement"]
        assert resultat["non_revues"] == ["z"]


def test_revue_refuse_source_d_un_autre_objet_et_metadonnees_zone_absentes(tmp_path):
    import hashlib
    from zipfile import ZipFile

    from benchab.controles.mesures_zones import verifier_mesures_declarees
    from benchab.controles.zones_locales import analyser_gcode

    source = (
        "G90\nM83\n;LAYER_CHANGE\nEXCLUDE_OBJECT_START NAME=source-a\n"
        ";WIDTH:0.4\n;TYPE:Bridge\nG1 X0 Y0 F1200\nG1 X10 Y0 E1\n"
        "EXCLUDE_OBJECT_END NAME=source-a\nEXCLUDE_OBJECT_START NAME=cible-b\n"
        ";LAYER_CHANGE\n;WIDTH:0.4\n;TYPE:Overhang wall\n"
        "G1 X0 Y0.3 F1200\nG1 X10 Y0.3 E1\nEXCLUDE_OBJECT_END NAME=cible-b\n"
    ).encode()
    tranche = tmp_path / "objets-differents.3mf"
    with ZipFile(tranche, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", source)
    zone = next(z for z in analyser_gcode(source.decode())["zones"] if z["type"] == "Overhang wall")
    declaration = {
        "zone_id": zone["id"], "plaque": 1, "gcode_sha256": hashlib.sha256(source).hexdigest(),
        "ligne_source": 8, "couche_source": 0, "ligne_cible": 15, "couche_cible": 1,
        "axe_local": [0, 1], "valeur_declaree_mm": -0.1, "tolerance_mm": 0.001,
    }
    resultat = verifier_mesures_declarees(tranche, [zone], [declaration])
    assert resultat[0]["statut"] == "NON VÉRIFIÉ"
    assert "objet source" in resultat[0]["erreur"]
    for champs_absents in ({"objet": "cible-b"}, {"bbox_xy_mm": zone["bbox_xy_mm"]}):
        zone_incomplete = {key: value for key, value in zone.items() if key not in {"objet", "bbox_xy_mm"}}
        zone_incomplete.update(champs_absents)
        assert verifier_mesures_declarees(tranche, [zone_incomplete], [declaration])[0]["statut"] == "NON VÉRIFIÉ"
