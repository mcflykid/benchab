"""Régressions des valeurs embarquées ; les indices filament et mode machine restent distincts."""

from zipfile import ZipFile

from benchab.fabrication.anycubic import reglages_embarques


def _verifier(tmp_path, entete, attendu):
    fichier = tmp_path / "fixture.3mf"
    with ZipFile(fichier, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", entete)
    avant = fichier.read_bytes()
    resultat = reglages_embarques(fichier, attendu)
    assert fichier.read_bytes() == avant
    return resultat


def test_chaque_filament_et_absence_cardinalite_sont_controles(tmp_path):
    """Compare 220/210 °C ; tout écart du second filament et toute valeur manquante bloquent sans mutation."""
    attendu = {"nozzle_temperature": ["220", "210"]}
    assert _verifier(tmp_path, "; nozzle_temperature = 220,210\n", attendu) == []
    erreurs = _verifier(tmp_path, "; nozzle_temperature = 220,220\n", attendu)
    assert erreurs[0]["filament"] == 2 and erreurs[0]["statut"] == "FAIL"
    assert _verifier(tmp_path, "; nozzle_temperature = 220\n", attendu)[0]["statut"] == "FAIL"
    assert _verifier(tmp_path, "", attendu)[0]["statut"] == "NON VÉRIFIÉ"


def test_scalaire_simple_csv_cite_et_attente_scalaire_de_liste(tmp_path):
    """Conserve 0,2 mm et les virgules citées ; refuse une attente scalaire pour une liste de filaments."""
    assert _verifier(tmp_path, "; layer_height = 0.2\n", {"layer_height": "0.2"}) == []
    assert (
        _verifier(
            tmp_path,
            '; filament_settings_id = "Profil, clair","Profil fonce"\n',
            {"filament_settings_id": ["Profil, clair", "Profil fonce"]},
        )
        == []
    )
    assert _verifier(tmp_path, "; nozzle_temperature = 220,210\n", {"nozzle_temperature": "220"})[0]["statut"] == "FAIL"


def test_vecteur_machine_et_cle_inconnue_ne_sont_pas_des_filaments(tmp_path):
    """Un troisième mode différent (780/600 mm/s) est FAIL ; une clé future reste NON VÉRIFIÉE."""
    erreurs = _verifier(
        tmp_path, "; machine_max_speed_x = 600,300,600\n", {"machine_max_speed_x": ["600", "300", "780"]}
    )
    assert erreurs[0]["mode_machine"] == 3 and "filament" not in erreurs[0]
    assert (
        _verifier(tmp_path, "; fixture_future = identique\n", {"fixture_future": "identique"})[0]["statut"]
        == "NON VÉRIFIÉ"
    )


def test_valeur_simple_n_est_pas_diffusee_sur_plusieurs_valeurs(tmp_path):
    """Une couche de 0,2 mm n'est pas une liste ; deux valeurs embarquées identiques restent FAIL."""
    erreurs = _verifier(tmp_path, "; layer_height = 0.2,0.2\n", {"layer_height": "0.2"})
    assert erreurs[0]["statut"] == "FAIL" and erreurs[0]["detail"] == "cardinalité différente"
