"""L'axe sémantique prime sur la longueur observée des vecteurs."""

import pytest

from benchab import reglages


def test_modes_machine_ne_sont_jamais_des_filaments():
    """Reproduit la réduction 600→780 en mm/s ; attend un refus, aucune transformation silencieuse."""
    valeurs = reglages.Reglages(
        {
            "printer_settings_id": "Anycubic Kobra S1 0.4 nozzle",
            "machine_max_speed_x": ["600", "300", "780"],
            "filament_settings_id": ["PLA"],
        }
    )
    assert reglages.definition("machine_max_speed_x")["type"] == "liste_mode_machine"
    assert reglages.longueur_attendue("machine_max_speed_x", valeurs) == 3
    with pytest.raises(ValueError, match="Cardinalité"):
        valeurs["machine_max_speed_x"] = ["780"]
    assert valeurs["machine_max_speed_x"] == ["600", "300", "780"]


def test_inconnu_et_residu_ne_sont_pas_transformes():
    """Conserve liste inconnue et résidu non apparié ; NON VÉRIFIÉ, aucun broadcast ni réglage changé."""
    valeurs = reglages.Reglages({"cle_future_fixture": ["original"], "filament_density": ["1.26", "1.24", "1.37"]})
    assert all(r["statut"] == "NON VÉRIFIÉ" for r in reglages.verifier(valeurs))
    with pytest.raises(ValueError, match="inconnu"):
        valeurs["cle_future_fixture"] = ["change"]
    with pytest.raises(ValueError, match="réindexation"):
        valeurs["filament_density"] = ["1.37"]
    with pytest.raises(ValueError, match="réindexation"):
        valeurs["filament_density"] = ["1.37", "1.24", "1.26"]
    assert valeurs.copy() == valeurs


def test_mode_choisi_explicitement_sans_modifier_les_valeurs():
    """Lit 600/300/780 mm/s selon le mode ; renvoie les nombres, ne modifie jamais le profil."""
    donnees = {"printer_settings_id": "Anycubic Kobra S1 0.4 nozzle", "machine_max_speed_x": ["600", "300", "780"]}
    assert [reglages.valeur_mode(donnees, "machine_max_speed_x", m) for m in ["standard", "silencieux", "sport"]] == [
        600,
        300,
        780,
    ]
    assert reglages.valeur_mode(donnees, "machine_max_speed_x", "inconnu") is None


def test_mode_hors_vecteur_replie_sur_premiere_valeur_sans_certifier_binaire():
    """Lit le repli get_at pour un vecteur court ; l'archive garde son statut WARN et ses octets logiques."""
    donnees = reglages.Reglages(
        {"printer_settings_id": "Anycubic Kobra S1 0.4 nozzle", "machine_max_speed_x": ["300"]}
    )
    resultat = next(r for r in reglages.verifier(donnees) if r["cle"] == "machine_max_speed_x")
    assert resultat["statut"] == "WARN"
    assert "repli du slicer sur la première valeur" in resultat["detail"]
    assert [item["valeur"] for item in resultat["valeurs_effectives_par_indice"]] == ["300"] * 3
    assert [reglages.valeur_mode(donnees, "machine_max_speed_x", mode) for mode in
            ("standard", "silencieux", "sport")] == [300.0, 300.0, 300.0]
    assert reglages.valeur_indice(donnees, "machine_max_speed_x", 2, lecteur="index_exact") is None
    assert reglages.valeur_indice(donnees, "machine_max_speed_x", 2, lecteur="get_at") == 300.0
    assert reglages.valeur_indice(donnees, "machine_max_speed_x", 2, lecteur="inconnu") is None
    assert donnees["machine_max_speed_x"] == ["300"]
    deux = reglages.Reglages(
        {"printer_settings_id": "Anycubic Kobra S1 0.4 nozzle", "machine_max_speed_x": ["600", "300"]}
    )
    diag_deux = next(r for r in reglages.verifier(deux) if r["cle"] == "machine_max_speed_x")
    assert [item["valeur"] for item in diag_deux["valeurs_effectives_par_indice"]] == ["600", "300", "600"]


def test_mutations_indirectes_inconnues_refusees():
    """Vérifie suppression et mutation imbriquée inconnues ; refus sans unité, aucun changement silencieux livré."""
    valeurs = reglages.Reglages({"cle_future_fixture": ["original"]})
    with pytest.raises(ValueError, match="inconnu"):
        valeurs.pop("cle_future_fixture")
    with pytest.raises(ValueError, match="inconnu"):
        valeurs.setdefault("autre_cle_future_fixture", "ajout")
    valeurs["cle_future_fixture"].append("mutation")
    with pytest.raises(ValueError, match="inconnu"):
        reglages.encoder(valeurs.copy())
    with pytest.raises(ValueError, match="inconnu"):
        reglages.encoder(reglages.Reglages(valeurs))


def test_diametre_buse_est_type_en_lecture_seule_sur_tous_les_chemins():
    """Lit nozzle_diameter typé mais conserve le verrou d’écriture des réglages inconnus."""
    valeurs = reglages.Reglages({"nozzle_diameter": ["0.4"]})
    assert reglages.definition("nozzle_diameter")["type"] == "diametre_buse"
    assert reglages.valeur_unique(valeurs, "nozzle_diameter") == "0.4"
    for operation in (
        lambda: valeurs.__setitem__("nozzle_diameter", ["0.6"]),
        lambda: valeurs.__delitem__("nozzle_diameter"),
        lambda: valeurs.pop("nozzle_diameter"),
        lambda: valeurs.update({"nozzle_diameter": ["0.6"]}),
        lambda: valeurs.__ior__({"nozzle_diameter": ["0.6"]}),
    ):
        with pytest.raises(ValueError, match="lecture seule"):
            operation()
        assert valeurs["nozzle_diameter"] == ["0.4"]
    with pytest.raises(ValueError, match="lecture seule"):
        reglages.Reglages().setdefault("nozzle_diameter", ["0.4"])
    valeurs["nozzle_diameter"][0] = "0.6"
    with pytest.raises(ValueError, match="lecture seule"):
        reglages.encoder(valeurs)


def test_limites_toml_identiques_au_profil_fabricant_par_mode():
    """Compare la transcription TOML au profil fabricant sourcé, mm/s et mm/s² ; aucun réglage modifié."""
    from benchab.machine.configuration import charger

    profil = reglages.profil_machine("Anycubic Kobra S1 0.4 nozzle")
    limites = charger()["mouvement"]
    for indice, mode in enumerate(("standard", "silencieux", "sport")):
        for prefixe, categorie in (
            ("machine_max_speed_", "vitesse_max_mm_s"),
            ("machine_max_acceleration_", "acceleration_max_mm_s2"),
            ("machine_max_jerk_", "jerk_max_mm_s"),
        ):
            for axe in "xyze":
                assert limites[mode][categorie][axe] == float(profil[prefixe + axe][indice])


def test_configuration_ambigue_ou_type_scalaire_invalide_refusee():
    """Refuse les clés doubles et NaN ; une chaîne remplacée par une liste donne FAIL, sans conversion."""
    with pytest.raises(ValueError, match="dupliquée"):
        reglages.decoder('{"top_shell_layers":"5","top_shell_layers":"8"}')
    with pytest.raises(ValueError, match="non finie"):
        reglages.decoder('{"top_shell_layers":NaN}')
    assert reglages.verifier({"top_shell_layers": ["5"]})[0]["statut"] == "FAIL"


def test_forme_observee_sur_autre_machine_ne_change_pas_le_type_certifie():
    """Garde les vitesses scalaires en mm/s ; une liste étrangère reste NON VÉRIFIÉE et intacte."""
    assert reglages.definition("outer_wall_speed", valeur="200")["type"] != "inconnu"
    valeurs = reglages.decoder('{"outer_wall_speed":["200","100"]}')
    assert reglages.verifier(valeurs)[0]["statut"] == "NON VÉRIFIÉ"
    assert reglages.decoder(reglages.encoder(valeurs)) == valeurs
    with pytest.raises(ValueError, match="inconnu"):
        valeurs["outer_wall_speed"] = "200"
    with pytest.raises(ValueError, match="inconnu"):
        valeurs["outer_wall_speed"] = ["100", "200"]
    valeurs["outer_wall_speed"][0] = "300"
    with pytest.raises(ValueError, match="inconnu"):
        reglages.encoder(valeurs)


def test_palette_residuelle_conservee_sans_inventer_des_profils_actifs():
    """Trois couleurs ne prouvent pas trois profils actifs ; NON VÉRIFIÉ sans correspondance, réindexation interdite."""
    valeurs = reglages.Reglages(
        {"filament_colour": ["#000000", "#FFFFFF", "#112233"], "filament_settings_id": ["Profil actif unique"]}
    )
    couleur = next(r for r in reglages.verifier(valeurs) if r["cle"] == "filament_colour")
    assert couleur["type"] == "liste_filament" and couleur["statut"] == "NON VÉRIFIÉ"
    assert reglages.decoder(reglages.encoder(valeurs)) == valeurs
    with pytest.raises(ValueError, match="réindexation"):
        valeurs["filament_colour"] = ["#112233"]


def test_matrice_origine_connue_seulement_dans_sa_portee():
    """Une métadonnée de provenance n'autorise jamais une option projet inconnue homonyme."""
    assert reglages.definition("matrix")["type"] == "inconnu"
    assert reglages.definition("matrix", portee="origine_part")["type"] == "chaine"
    assert reglages.definition("top_shell_layers", portee="origine_part") == reglages.definition("top_shell_layers")
    assert reglages.definition("fixture_future", portee="origine_part")["type"] == "inconnu"


def test_identifiants_et_heritages_dimensionnes_par_palette_sans_inventer_profils():
    """Palette3/profil1 : IDs3 et héritages5 ; les trois couleurs ne prouvent toujours pas trois profils actifs."""
    donnees = reglages.Reglages(
        {
            "filament_colour": ["#000000", "#FFFFFF", "#123456"],
            "filament_settings_id": ["Profil unique"],
            "filament_ids": ["A", "B", "C"],
            "inherits_group": [""] * 5,
        },
        contexte={"consommateur_reglages": "preset_bundle"},
    )
    resultat = {r["cle"]: r for r in reglages.verifier(donnees)}
    assert resultat["filament_ids"]["longueur_attendue"] == 3
    assert resultat["inherits_group"]["longueur_attendue"] == 5
    assert resultat["filament_ids"]["statut"] == resultat["inherits_group"]["statut"] == "PASS"
    assert resultat["filament_colour"]["statut"] == "NON VÉRIFIÉ"
    incoherent = dict(donnees, inherits_group=[""] * 3)
    contexte = {"consommateur_reglages": "preset_bundle"}
    diagnostic = next(r for r in reglages.verifier(incoherent, contexte) if r["cle"] == "inherits_group")
    assert diagnostic["statut"] == "NON VÉRIFIÉ"
    with pytest.raises(ValueError, match="cardinalité invalide"):
        reglages.encoder(reglages.Reglages(incoherent, contexte=contexte))
    vide = reglages.Reglages(
        {"filament_colour": ["#000000", "#FFFFFF"], "filament_settings_id": ["PLA"], "filament_ids": []},
        contexte={"consommateur_reglages": "preset_bundle"},
    )
    assert next(r for r in reglages.verifier(vide) if r["cle"] == "filament_ids")["statut"] == "FAIL"


def test_lecture_vecteurs_machine_courts_longs_vides_et_ecriture_exacte():
    """Lecture courte/longue WARN, vide FAIL ; encoder refuse toute longueur différente du profil résolu."""
    for valeurs in (["300"], ["600", "600"], [], ["600"] * 4):
        donnees = reglages.Reglages(
            {"printer_settings_id": "Anycubic Kobra S1 0.4 nozzle", "machine_max_speed_x": valeurs}
        )
        attendu = "FAIL" if not valeurs else "WARN" if len(valeurs) != 3 else "PASS"
        resultat = next(r for r in reglages.verifier(donnees) if r["cle"] == "machine_max_speed_x")
        assert resultat["statut"] == attendu
        assert donnees["machine_max_speed_x"] == valeurs
        if attendu != "PASS":
            with pytest.raises(ValueError, match="Cardinalité machine exacte|cardinalité invalide"):
                reglages.encoder(donnees)
            with pytest.raises(ValueError, match="cardinalité invalide"):
                reglages.encoder(dict(donnees))
        if len(valeurs) > 3:
            assert resultat["elements_supplementaires"] == ["600"]
    complet = reglages.Reglages(
        {"printer_settings_id": "Anycubic Kobra S1 0.4 nozzle", "machine_max_speed_x": ["600", "300", "780"]}
    )
    with pytest.raises(ValueError, match="Cardinalité"):
        complet["machine_max_speed_x"] = ["780"]


def test_lecteurs_cli_et_preset_bundle_ne_sont_pas_confondus():
    """Palette3/profil1 : CLI N1, PresetBundle N3 ; sans lecteur, les métadonnées restent NON VÉRIFIÉES."""
    donnees = {
        "filament_colour": ["#000000", "#FFFFFF", "#123456"],
        "filament_settings_id": ["Profil unique"],
        "filament_ids": ["A"],
        "inherits_group": [""] * 3,
    }
    assert reglages.longueur_attendue("inherits_group", donnees) is None
    assert reglages.longueur_attendue("filament_ids", donnees) is None
    assert next(r for r in reglages.verifier(donnees) if r["cle"] == "inherits_group")["statut"] == "NON VÉRIFIÉ"
    assert reglages.longueur_attendue("inherits_group", {**donnees, "consommateur_reglages": "cli"}) == 3
    assert reglages.longueur_attendue("inherits_group", {**donnees, "consommateur_reglages": "preset_bundle"}) == 5
    assert reglages.nombre_filaments_metadata(donnees, consommateur="cli", extrudeurs=[2]) == 2


def test_mutation_imbriquee_machine_ne_contourne_pas_non_verifie():
    """Réduit 600/300/780 à780 mm/s via slice ; encoder refuse, même si la lecture d'un ancien vecteur court est NV."""
    valeurs = reglages.Reglages(
        {"printer_settings_id": "Anycubic Kobra S1 0.4 nozzle", "machine_max_speed_x": ["600", "300", "780"]}
    )
    valeurs["machine_max_speed_x"][:] = ["780"]
    with pytest.raises(ValueError, match="mutation imbriquée"):
        reglages.encoder(valeurs)
    ancien = reglages.Reglages({"printer_settings_id": "Anycubic Kobra S1 0.4 nozzle", "machine_max_speed_x": ["300"]})
    assert ancien["machine_max_speed_x"] == ["300"]
    with pytest.raises(ValueError, match="cardinalité invalide"):
        reglages.encoder(ancien)
    ancien["machine_max_speed_x"][0] = "780"
    with pytest.raises(ValueError, match="mutation imbriquée"):
        reglages.encoder(ancien)


def test_normaliser_heritages_transmet_contexte_cli_au_dictionnaire():
    """Trois couleurs résiduelles ne créent pas de profils CLI : groupe vide5→3 documenté, entrées intactes."""
    from benchab.fabrication.reglages import normaliser_heritages

    donnees = {
        "filament_colour": ["#000000", "#FFFFFF", "#123456"],
        "filament_settings_id": ["Profil unique"],
        "inherits_group": [""] * 5,
    }
    resultat, modifications = normaliser_heritages(donnees)
    assert resultat["inherits_group"] == [""] * 3 and donnees["inherits_group"] == [""] * 5
    assert resultat["filament_settings_id"] == ["Profil unique"]
    assert modifications[0]["avant"] == [""] * 5 and modifications[0]["apres"] == [""] * 3


def test_vecteur_machine_court_ne_peut_pas_etre_complete_ni_reinterprete():
    """Une origine300 mm/s non certifiée ne devient jamais un vecteur780/780/780 par affectation ou slice."""
    donnees = {"printer_settings_id": "Anycubic Kobra S1 0.4 nozzle", "machine_max_speed_x": ["300"]}
    direct = reglages.Reglages(donnees)
    with pytest.raises(ValueError, match="origine NON VÉRIFIÉ"):
        direct["machine_max_speed_x"] = ["780", "780", "780"]
    imbrique = reglages.Reglages(donnees)
    imbrique["machine_max_speed_x"][:] = ["780", "780", "780"]
    with pytest.raises(ValueError, match="origine NON VÉRIFIÉ"):
        reglages.encoder(imbrique)
