"""Contrats d’énumération attachés à une identité de binaire observée."""

import pytest

from benchab import reglages

ASN = {
    "version": "2.0.0.3",
    "sha256": "618b3fc1b291f30a564c4df2e87df5e4e6598927ae061e3d9a90dd119754a58d",
}


@pytest.mark.parametrize(
    "cle,valeur",
    [
        ("sparse_infill_pattern", "zigzag"),
        ("sparse_infill_pattern", "crosshatch"),
        ("sparse_infill_pattern", "supportcubic"),
        ("sparse_infill_pattern", "crosszag"),
        ("internal_solid_infill_pattern", "monotonic"),
        ("top_surface_pattern", "monotonicline"),
        ("bottom_surface_pattern", "rectilinear"),
        ("wall_sequence", "inner-outer-inner wall"),
        ("alternate_extra_wall", "0"),
        ("alternate_extra_wall", "1"),
        ("support_interface_pattern", "auto"),
        ("support_interface_pattern", "concentric"),
    ],
)
def test_valeur_qualifiee_ne_change_pas_les_reglages(cle, valeur):
    """Le contrôle confirme le cas mesuré sans changer la valeur d'entrée."""
    donnees = {cle: valeur}
    ligne, = reglages.verifier(donnees, identite_slicer=ASN)
    assert ligne["statut"] == "PASS"
    assert ligne["enumeration"]["valeur_embarquee"] == valeur
    assert donnees == {cle: valeur}


@pytest.mark.parametrize("demande,repli", [
    ("zig-zag", "zigzag"), ("benchab_fixture_inconnue", "crosshatch"), ("2dlattice", "crosshatch"),
])
def test_repli_refuse_meme_si_le_slicer_sort_un_fichier(demande, repli):
    """Un changement natif confirmé n'est pas une validation de la variante."""
    ligne, = reglages.verifier({"sparse_infill_pattern": demande}, identite_slicer=ASN)
    assert ligne["statut"] == "FAIL"
    assert ligne["enumeration"]["valeur_embarquee"] == repli


def test_inconnu_reste_non_verifie():
    """L'identité partielle, les options non mesurées et les clés masquées ne sont pas déduites."""
    for identite, cle, valeur in (
        ({"version": ASN["version"]}, "sparse_infill_pattern", "grid"),
        ({**ASN, "sha256": "0" * 64}, "sparse_infill_pattern", "grid"),
        (ASN, "sparse_infill_pattern", "motif jamais tenté"),
        ({**ASN, "sha256": "0" * 64}, "sparse_infill_pattern", "supportcubic"),
        (ASN, "locked_skeleton_infill_pattern", "rectilinear"),
        (ASN, "support_interface_pattern", "grid"),
        (ASN, "alternate_extra_wall", "2"),
        ({**ASN, "sha256": "0" * 64}, "alternate_extra_wall", "1"),
        ({**ASN, "sha256": "0" * 64}, "support_interface_pattern", "concentric"),
    ):
        diagnostic, = reglages.verifier({cle: valeur}, identite_slicer=identite)
        assert diagnostic["statut"] == "NON VÉRIFIÉ"


def test_verification_de_type_reste_independante():
    """La validation typée reste stable sans identité et bloque toujours les listes."""
    historique, = reglages.verifier({"sparse_infill_pattern": "zig-zag"})
    malforme, = reglages.verifier({"sparse_infill_pattern": ["zigzag"]}, identite_slicer=ASN)
    assert historique["statut"] == "PASS" and "enumeration" not in historique
    assert malforme["statut"] == "FAIL"


def test_commande_compare_archive_au_binaire_sans_modifier(tmp_path, monkeypatch, capsys):
    """Le rapport CLI montre le repli historique sans écrire ni lancer le moteur."""
    import json
    from zipfile import ZipFile

    from benchab.controles import enumerations_asn
    from benchab.fabrication import slicer_runtime

    source = tmp_path / "variante.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", '{"sparse_infill_pattern":"zig-zag"}')
    avant = source.read_bytes()
    monkeypatch.setattr(slicer_runtime, "informations", lambda *, empreinte: ASN if empreinte else {})
    assert enumerations_asn.main([str(source)]) == 2
    rapport = json.loads(capsys.readouterr().out)
    assert rapport["statut"] == "FAIL"
    assert rapport["binaire_cible"] == ASN
    assert rapport["controles"][0]["enumeration"]["valeur_embarquee"] == "zigzag"
    assert source.read_bytes() == avant


def test_style_conditionnel_exige_les_reglages_embarques_sans_repli():
    """Le style snug est qualifié seulement pour le contexte exact lu dans la source."""
    conditions = {
        "enable_support": "1", "support_type": "normal(auto)",
        "support_interface_pattern": "concentric", "support_top_z_distance": "0.18",
    }
    donnees = {"support_style": "snug", **conditions}

    def style(lignes):
        """Sélectionne le contrôle du style, sans dépendre de l’ordre du rapport."""
        ligne, = (item for item in lignes if item["cle"] == "support_style")
        return ligne

    historique = style(reglages.verifier(donnees, identite_slicer=ASN))
    assert "enumeration" not in historique
    qualifie = style(reglages.verifier(donnees, identite_slicer=ASN, inclure_contextuelles=True))
    assert qualifie["statut"] == "PASS"
    assert qualifie["enumeration"]["conditions"] == conditions
    qualifie["enumeration"]["conditions"]["support_type"] = "déclaration modifiée"
    for variante in (
        {"support_style": "snug"},
        {**donnees, "support_type": "tree(auto)"},
        {**donnees, "support_top_z_distance": "0.38"},
        {**donnees, "support_style": "default"},
    ):
        diagnostic = style(reglages.verifier(
            variante, contexte=conditions, identite_slicer=ASN, inclure_contextuelles=True,
        ))
        assert diagnostic["statut"] == "NON VÉRIFIÉ"
        assert diagnostic["enumeration"]["valeur_embarquee"] is None
    apres = style(reglages.verifier(donnees, identite_slicer=ASN, inclure_contextuelles=True))
    assert apres["enumeration"]["conditions"] == conditions


def test_commande_style_contextuel_est_facultative(tmp_path, monkeypatch, capsys):
    """Sans option le nouveau champ ne change pas le rapport historique."""
    import json
    from zipfile import ZipFile

    from benchab.controles import enumerations_asn
    from benchab.fabrication import slicer_runtime

    source = tmp_path / "variante.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps({
            "enable_support": "1", "support_style": "grid", "support_type": "normal(auto)",
            "support_interface_pattern": "concentric", "support_top_z_distance": "0.18",
        }))
    avant = source.read_bytes()
    monkeypatch.setattr(slicer_runtime, "informations", lambda *, empreinte: ASN if empreinte else {})
    assert enumerations_asn.main([str(source)]) == 0
    ancien = json.loads(capsys.readouterr().out)
    assert not any(item["cle"] == "support_style" for item in ancien["controles"])
    assert enumerations_asn.main([str(source), "--contextuelles"]) == 0
    nouveau = json.loads(capsys.readouterr().out)
    assert any(item["cle"] == "support_style" and item["statut"] == "PASS" for item in nouveau["controles"])
    assert source.read_bytes() == avant


@pytest.mark.parametrize("valeur, activation, statut", [
    ("50%", "1", "PASS"), ("100%", "1", "PASS"), ("100%", "0", "NON VÉRIFIÉ"),
    ("75%", "1", "NON VÉRIFIÉ"),
])
def test_hauteur_combinee_reste_liee_aux_conditions(valeur, activation, statut):
    """La hauteur n’est qualifiée qu’avec la combinaison et le profil observés."""
    cle = "infill_combination_max_layer_height"
    donnees = {
        cle: valeur, "infill_combination": activation, "nozzle_diameter": ["0.4"],
        "layer_height": "0.2", "sparse_infill_pattern": "gyroid", "sparse_infill_density": "20%",
    }
    resultats = reglages.verifier(donnees, identite_slicer=ASN, inclure_contextuelles=True)
    lignes = [r for r in resultats if r.get("cle") == cle]
    assert len(lignes) == 1 and lignes[0]["enumeration"]["statut"] == statut


@pytest.mark.parametrize(("valeur", "alignement", "statut"), [
    ("45", "0", "PASS"), ("75", "0", "PASS"), ("75", "1", "NON VÉRIFIÉ"), ("60", "0", "NON VÉRIFIÉ"),
])
def test_direction_rectiligne_exige_le_repere_observe(valeur, alignement, statut):
    """Les angles observés restent liés au profil et à la désactivation de l’alignement au modèle."""
    donnees = {
        "infill_direction": valeur, "align_infill_direction_to_model": alignement,
        "nozzle_diameter": ["0.4"], "layer_height": "0.2",
        "sparse_infill_pattern": "rectilinear", "sparse_infill_density": "20%", "infill_combination": "0",
    }
    resultats = reglages.verifier(donnees, identite_slicer=ASN, inclure_contextuelles=True)
    ligne, = (r for r in resultats if r["cle"] == "infill_direction")
    assert ligne["enumeration"]["statut"] == statut


@pytest.mark.parametrize("cle", ["locked_skin_infill_pattern", "locked_skeleton_infill_pattern"])
@pytest.mark.parametrize("valeur", ["rectilinear", "zigzag"])
def test_options_lockedzag_disparues_de_la_sortie(cle, valeur):
    """Le refus documenté porte sur le profil et le motif effectivement éprouvés."""
    donnees = {
        cle: valeur, "sparse_infill_pattern": "lockedzag", "sparse_infill_density": "20%",
        "printer_settings_id": "Anycubic Kobra S1 0.4 nozzle", "nozzle_diameter": ["0.4"],
        "layer_height": "0.2", "infill_combination": "0",
    }
    def verdict(reglages_source):
        return next(ligne["enumeration"] for ligne in reglages.verifier(
            reglages_source, identite_slicer=ASN,
        ) if ligne["cle"] == cle)

    obtenu = verdict(donnees)
    assert obtenu["statut"] == "FAIL"
    assert obtenu["valeur_embarquee"] is None
    assert "absente" in obtenu["detail"]
    assert verdict({**donnees, "sparse_infill_pattern": "rectilinear"})["statut"] == "NON VÉRIFIÉ"


def test_alignement_rectiligne_reste_lie_aux_conditions_observees():
    """La qualification du repère pièce exige le motif et le profil du coupon."""
    cle = "align_infill_direction_to_model"
    source = {
        cle: "1", "infill_direction": "45", "infill_combination": "0",
        "printer_settings_id": "Anycubic Kobra S1 0.4 nozzle", "nozzle_diameter": ["0.4"],
        "layer_height": "0.2", "sparse_infill_pattern": "rectilinear", "sparse_infill_density": "20%",
    }

    def ligne(donnees, **options):
        return next(r for r in reglages.verifier(donnees, identite_slicer=ASN, **options) if r["cle"] == cle)

    assert "enumeration" not in ligne(source)
    assert ligne(source, inclure_contextuelles=True)["enumeration"]["statut"] == "PASS"
    variante = {**source, "sparse_infill_pattern": "gyroid"}
    assert ligne(variante, inclure_contextuelles=True)["enumeration"]["statut"] == "NON VÉRIFIÉ"


@pytest.mark.parametrize(("cle", "valeur"), [
    ("skin_infill_density", "55%"),
    ("skeleton_infill_density", "8%"),
])
def test_densites_lockedzag_reservees_au_contexte_observe(cle, valeur):
    """Les deux densités qualifiées hors ligne gardent la contrainte du profil Locked Zag."""
    donnees = {
        cle: valeur,
        "sparse_infill_pattern": "lockedzag", "sparse_infill_density": "20%",
        "printer_settings_id": "Anycubic Kobra S1 0.4 nozzle", "nozzle_diameter": ["0.4"],
        "layer_height": "0.2", "infill_combination": "0",
    }

    def ligne(source, **options):
        return next(r for r in reglages.verifier(source, identite_slicer=ASN, **options) if r["cle"] == cle)

    assert "enumeration" not in ligne(donnees)
    assert ligne(donnees, inclure_contextuelles=True)["enumeration"]["statut"] == "PASS"
    for variante in ({cle: "14%"}, {"sparse_infill_pattern": "crosszag"}, {"layer_height": "0.24"}):
        resultat = ligne({**donnees, **variante}, inclure_contextuelles=True)
        assert resultat["enumeration"]["statut"] == "NON VÉRIFIÉ"
