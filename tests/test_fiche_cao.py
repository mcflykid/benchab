"""Contrat numérique du premier gabarit CAO à partir d’une fiche cotée."""

import hashlib
import json

import pytest


def _fiche():
    return {
        "schema": 1,
        "id": "plaque-test",
        "bloc_mm": [30, 20, 4],
        "percages": [{"x_mm": 2, "y_mm": -3, "diametre_mm": 5}],
        "hors_perimetre": ["Tenue sous charge"],
    }


def test_plan_et_script_identiques_sur_rejeu(tmp_path):
    """Les espaces et l’ordre des clés ne changent ni le script ni le plan."""
    from benchab.conception.fiche_cao import generer

    source = tmp_path / "fiche.json"
    source.write_text(json.dumps(_fiche()), encoding="utf-8")
    premiere = generer(source, tmp_path / "p1")
    source.write_text(json.dumps(_fiche(), indent=2, sort_keys=True), encoding="utf-8")
    seconde = generer(source, tmp_path / "p2")
    plan = json.loads((tmp_path / "p1/plan.json").read_bytes())
    script = (tmp_path / "p1/modele.py").read_bytes()
    assert premiere["statut"] == seconde["statut"] == "NON VÉRIFIÉ"
    assert plan["exigences_non_verifiees"] == ["Tenue sous charge"]
    assert plan["script_sha256"] == hashlib.sha256(script).hexdigest()
    assert plan["attendu"]["volume_mm3"] == pytest.approx(2321.460183660255)
    assert script == (tmp_path / "p2/modele.py").read_bytes()
    assert (tmp_path / "p1/plan.json").read_bytes() == (tmp_path / "p2/plan.json").read_bytes()
    with pytest.raises(FileExistsError):
        generer(source, tmp_path / "p1")
    assert (tmp_path / "p1/modele.py").read_bytes() == script


@pytest.mark.parametrize(
    "bloc,percages,message",
    [
        ([True, 20, 4], [], "dimension"),
        ([30, float("inf"), 4], [], "dimension"),
        ([30, 20, 4], [{"x_mm": 13, "y_mm": 0, "diametre_mm": 4}], "marge"),
        ([30, 20, 4], [{"x_mm": 0, "y_mm": 0, "diametre_mm": 6},
                       {"x_mm": 3, "y_mm": 0, "diametre_mm": 4}], "chevauchement"),
    ],
)
def test_fiche_invalide_ne_publie_rien(tmp_path, bloc, percages, message):
    """Une cote non numérique, tangente ou recouvrante ne donne aucun script."""
    from benchab.conception.fiche_cao import generer

    source = tmp_path / "fiche.json"
    source.write_text(json.dumps({**_fiche(), "bloc_mm": bloc, "percages": percages}), encoding="utf-8")
    sortie = tmp_path / "sortie"
    with pytest.raises(ValueError, match=message):
        generer(source, sortie)
    assert not sortie.exists()


def test_cle_json_dupliquee_et_source_liee_refusees(tmp_path):
    """Les entrées ambiguës ou liées sont refusées avant toute publication."""
    from benchab.conception.fiche_cao import generer

    source = tmp_path / "fiche.json"
    source.write_text('{"schema":1,"schema":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="dupliquée"):
        generer(source, tmp_path / "sortie")
    source.write_text(json.dumps(_fiche()), encoding="utf-8")
    lien = tmp_path / "lien.json"
    lien.symlink_to(source)
    with pytest.raises(ValueError, match="[Ll]ien"):
        generer(lien, tmp_path / "sortie")
    assert not (tmp_path / "sortie").exists()


def test_cli_resume_detail_et_exigence_non_verifiee(tmp_path, capsys, atelier_initialise):
    """Le code 2 garde la preuve complète disponible et explicite l’exclusion."""
    from benchab.cli import main

    source = tmp_path / "fiche.json"
    source.write_text(json.dumps(_fiche()), encoding="utf-8")
    for detail in (False, True):
        sortie = tmp_path / f"p{detail}"
        arguments = [
            "--prive", str(tmp_path / "prive"), "conception", "fiche-cao", "--",
            str(source), "--sortie", str(sortie),
        ]
        assert main([*arguments, "--detail"] if detail else arguments) == 2
        affiche = json.loads(capsys.readouterr().out)
        assert affiche["rapport"] == str(sortie / "plan.json")
        assert ("plan" in affiche) == detail
        if detail:
            assert affiche["plan"] == json.loads((sortie / "plan.json").read_bytes())
