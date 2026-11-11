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
        ("internal_solid_infill_pattern", "monotonic"),
        ("top_surface_pattern", "monotonicline"),
        ("bottom_surface_pattern", "rectilinear"),
        ("wall_sequence", "inner-outer-inner wall"),
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


@pytest.mark.parametrize("demande,repli", [("zig-zag", "zigzag"), ("benchab_fixture_inconnue", "crosshatch")])
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
