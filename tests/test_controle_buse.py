"""Le contrôle optionnel de buse s’appuie sur les réglages typés et reste non physique."""

from benchab import reglages
from benchab.controles import controle_buse


def test_buse_compare_diametre_declare_et_hrc_par_filament():
    """Compare un diamètre déclaré et les HRC exacts utilisés par deux filaments actifs."""
    parametres = {
        "filament_settings_id": ["PLA-A", "PLA-B"],
        "nozzle_diameter": ["0.4"],
        "nozzle_hrc": "50",
        "required_nozzle_HRC": ["40", "60"],
    }

    resultats = controle_buse.controler(parametres, {"buse": {"diametre_mm": 0.4}}, [1, 2])

    par_nom = {r["controle"]: r for r in resultats}
    assert par_nom["diamètre de buse déclaré"]["statut"] == "PASS"
    assert par_nom["HRC buse du profil 3MF vs exigence (filament 1)"]["statut"] == "PASS"
    assert par_nom["HRC buse du profil 3MF vs exigence (filament 2)"]["statut"] == "WARN"
    assert "compatibilité physique" in par_nom["HRC buse du profil 3MF vs exigence (filament 2)"]["detail"]


def test_buse_donnees_absentes_ou_ambigues_non_verifiees():
    """Un HRC inconnu, un vecteur de buse ambigu et une déclaration absente ne donnent pas PASS."""
    parametres = {
        "filament_settings_id": ["PLA-A"],
        "nozzle_diameter": ["0.4", "0.6"],
        "nozzle_hrc": "0",
        "required_nozzle_HRC": ["40"],
    }

    resultats = controle_buse.controler(parametres, {"buse": {}}, [1])

    assert all(r["statut"] == "NON VÉRIFIÉ" for r in resultats)
    assert controle_buse.controler(parametres, {}, [1]) == []


def test_ecart_de_diametre_de_buse_declare_est_warn():
    """Un diamètre 3MF différent du diamètre de buse déclaré reste visible comme WARN."""
    resultats = controle_buse.controler(
        {"nozzle_diameter": ["0.4"]}, {"buse": {"diametre_mm": 0.6}}, []
    )
    assert resultats[0]["statut"] == "WARN"


def test_declaration_buse_non_numerique_reste_non_verifiee():
    """Une chaîne ou un booléen dans la déclaration ne devient pas un diamètre validé."""
    for valeur in ("0.4", True):
        resultat = controle_buse.controler(
            {"nozzle_diameter": ["0.4"]}, {"buse": {"diametre_mm": valeur}}, []
        )
        assert resultat[0]["statut"] == "NON VÉRIFIÉ"


def test_contrat_buse_nul_ou_malforme_ne_peut_pas_valider_hrc():
    """Un contrat explicitement malformé rend chaque contrôle de buse NON VÉRIFIÉ."""
    parametres = {
        "filament_settings_id": ["PLA-A"], "nozzle_diameter": ["0.4"],
        "nozzle_hrc": "50", "required_nozzle_HRC": ["40"],
    }
    for contrat in (None, 0, {}, {"diametre_mm": 0}, {"diametre_mm": 0.4, "autre": 1}):
        resultats = controle_buse.controler(parametres, {"buse": contrat}, [1])
        assert len(resultats) == 2
        assert all(r["statut"] == "NON VÉRIFIÉ" for r in resultats)


def test_dictionnaire_type_la_buse_et_ses_unites():
    """Le dictionnaire expose les formes et unités sans rendre un vecteur multi-buse univoque."""
    assert reglages.definition("nozzle_diameter")["type"] == "diametre_buse"
    assert reglages.definition("nozzle_diameter")["unite"] == "mm"
    assert reglages.definition("nozzle_hrc")["unite"] == "HRC"
    assert reglages.definition("required_nozzle_HRC")["unite"] == "HRC"
    assert reglages.verifier({"nozzle_diameter": ["0.4"]})[0]["statut"] == "PASS"
    assert reglages.verifier({"nozzle_diameter": ["0.4", "0.6"]})[0]["statut"] == "NON VÉRIFIÉ"


def test_envoi_buse_trempee_declaree_distincte_du_profil():
    """Distingue une buse déclarée de l’absence d’information sur la buse montée."""
    reglages_3mf = {"nozzle_diameter": ["0.4"], "nozzle_hrc": "0"}
    sans = controle_buse.controler(reglages_3mf, {"buse": {"diametre_mm": 0.4}}, [])
    avec = controle_buse.controler(
        reglages_3mf, {"buse": {"diametre_mm": 0.4, "trempee": True}}, []
    )
    assert sans[0]["buse_montee"] == "non déclarée"
    assert avec[0]["buse_montee"] == "trempée déclarée"
    assert sans[0]["statut"] == avec[0]["statut"] == "PASS"
