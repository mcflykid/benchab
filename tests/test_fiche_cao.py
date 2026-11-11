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


def test_lamage_superieur_ajoute_seulement_le_volume_annulaire_et_reste_deterministe(tmp_path):
    """Le plan soustrait l’anneau à la profondeur demandée, en plus du trou traversant."""
    from math import pi

    from benchab.conception.fiche_cao import generer

    fiche = _fiche()
    fiche["percages"][0]["lamage"] = {"diametre_mm": 9, "profondeur_mm": 2, "source_cotes": "cotes déclarées"}
    source = tmp_path / "fiche.json"
    for i in (0, 1):
        source.write_text(json.dumps(fiche, indent=i, sort_keys=bool(i)), encoding="utf-8")
        generer(source, tmp_path / f"p{i}")
    plan = json.loads((tmp_path / "p0/plan.json").read_bytes())
    assert plan["attendu"]["volume_mm3"] == pytest.approx(2400 - pi * 2.5**2 * 4 - pi * (4.5**2 - 2.5**2) * 2)
    assert plan["percages"][0]["lamage"] == {
        "diametre_mm": 9, "profondeur_mm": 2, "source_cotes": "cotes déclarées",
    }
    assert (tmp_path / "p0/plan.json").read_bytes() == (tmp_path / "p1/plan.json").read_bytes()
    script = (tmp_path / "p0/modele.py").read_bytes()
    assert script == (tmp_path / "p1/modele.py").read_bytes()
    assert plan["script_sha256"] == hashlib.sha256(script).hexdigest()
    assert b"cotes d" not in script


@pytest.mark.parametrize(
    "x,diametre,profondeur,autre,source_cotes,message",
    [
        (0, 5, 1, False, "plan", "diamètre"),
        (0, 9, 4, False, "plan", "profondeur"),
        (11, 9, 2, False, "plan", "marge"),
        (0, 9, 2, True, "plan", "chevauchement"),
        (0, 9, 2, False, "", "source"),
    ],
)
def test_lamage_refuse_la_collision_ou_les_cotes_sans_source(
    tmp_path, x, diametre, profondeur, autre, source_cotes, message
):
    """Le rayon d’entrée, le fond restant et l’origine explicite des cotes sont contrôlés."""
    from benchab.conception.fiche_cao import generer

    fiche = _fiche()
    fiche["percages"] = [{
        "x_mm": x, "y_mm": 0, "diametre_mm": 5,
        "lamage": {"diametre_mm": diametre, "profondeur_mm": profondeur, "source_cotes": source_cotes},
    }]
    if autre:
        fiche["percages"].append({"x_mm": 6, "y_mm": 0, "diametre_mm": 5})
    source = tmp_path / "fiche.json"
    source.write_text(json.dumps(fiche), encoding="utf-8")
    cible = tmp_path / "sortie"
    with pytest.raises(ValueError, match=message):
        generer(source, cible)
    assert not cible.exists()


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
