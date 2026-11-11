"""Refus d'écraser une entrée quand les trois contrôles publient leur JSON."""

from pathlib import Path
from unittest.mock import patch

import pytest

from benchab.controles import colonnes_isolees, controles_piece, premiere_couche


@pytest.mark.parametrize(
    ("module", "controle", "arguments", "resultat"),
    [
        (premiere_couche, "controler", lambda source: [str(source), "--json", str(source)], {"statut": "PASS"}),
        (colonnes_isolees, "controler", lambda source: [str(source), "--json", str(source)], {"statut": "PASS"}),
        (
            controles_piece,
            "controler",
            lambda source: ["--sans-cache", "--plateau", "200x200x200", str(source), "--json", str(source)],
            [],
        ),
    ],
)
def test_refuse_sortie_json_identique_a_l_entree_avant_le_controle(
    tmp_path: Path, module, controle, arguments, resultat
):
    """Un refus de destination conserve le fichier source et évite le calcul géométrique."""
    source = tmp_path / "piece.gcode" if module is not controles_piece else tmp_path / "piece.stl"
    source.write_bytes(b"source d'origine")
    avant = source.read_bytes()
    with patch.object(module, controle, return_value=resultat) as calcul:
        if module in (premiere_couche, colonnes_isolees):
            with patch.object(module, "gcodes", return_value=[(1, "G1 X1")]):
                code = module.main(arguments(source))
        else:
            code = module.main(arguments(source))

    assert code == 2
    assert source.read_bytes() == avant
    calcul.assert_not_called()


@pytest.mark.parametrize(
    ("module", "suffixe", "resultat"),
    [
        (premiere_couche, ".gcode", {"statut": "PASS", "defauts": []}),
        (colonnes_isolees, ".gcode", {"statut": "PASS", "series": []}),
    ],
)
def test_rapports_gcode_sont_neufs_et_rendent_le_code_historique(tmp_path, module, suffixe, resultat):
    """Les deux contrôles G-code écrivent un nouveau JSON sans changer PASS=0."""
    source = tmp_path / f"piece{suffixe}"
    source.write_text("; G-code synthétique", encoding="utf-8")
    sortie = tmp_path / "rapport.json"
    with (
        patch.object(module, "gcodes", return_value=[(1, "G1 X1")]),
        patch.object(module, "controler", return_value=resultat),
        patch.object(module, "ecrire_json_neuf", wraps=module.ecrire_json_neuf) as publier,
    ):
        code = module.main([str(source), "--json", str(sortie)])

    assert code == 0
    assert sortie.is_file()
    assert source.read_text(encoding="utf-8") == "; G-code synthétique"
    assert publier.call_args.kwargs["entrees"] == [source]


def test_rapport_maillages_neuf_protege_toutes_les_entrees(tmp_path):
    """Le rapport géométrique neuf déclare chaque maillage lu comme entrée."""
    sources = [tmp_path / "a.stl", tmp_path / "b.stl"]
    for source in sources:
        source.write_text("maillage synthétique", encoding="utf-8")
    sortie = tmp_path / "rapport.json"
    resultat = [{"statut": "PASS", "controle": "fixture", "detail": "calcul synthétique"}]
    with (
        patch.object(controles_piece, "controler", return_value=resultat),
        patch.object(controles_piece, "ecrire_json_neuf", wraps=controles_piece.ecrire_json_neuf) as publier,
    ):
        code = controles_piece.main([
            "--sans-cache", "--plateau", "200x200x200", *(str(source) for source in sources),
            "--json", str(sortie),
        ])

    assert code == 0
    assert sortie.is_file()
    assert publier.call_args.kwargs["entrees"] == sources
    assert all(source.read_text(encoding="utf-8") == "maillage synthétique" for source in sources)
