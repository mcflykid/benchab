"""Les retours externes restent des hypothèses contextualisées et non prescriptives."""

import pytest

from benchab.connaissances import retours_communautaires


def test_registre_retient_dates_sources_conditions_et_limites():
    """Les deux signalements sont paraphrasés, datés et bornés aux conditions déclarées."""
    entrees = retours_communautaires.lire_registre()
    par_id = {entree["id"]: entree for entree in entrees}

    assert set(par_id) == {"asnext-issue-9", "asnext-issue-4"}
    assert par_id["asnext-issue-9"]["date_retour"] == "2026-09-18"
    assert par_id["asnext-issue-9"]["date_relecture"] == "2026-11-11"
    assert par_id["asnext-issue-9"]["conditions"] == {
        "machine": "Anycubic Kobra S1 Combo",
        "version_logiciel": "Anycubic Slicer Next 2.0.0.3",
        "systeme": "Windows 11",
    }
    assert par_id["asnext-issue-4"]["date_retour"] == "2026-08-20"
    assert par_id["asnext-issue-4"]["conditions"] == {
        "machine": "Anycubic Kobra S1 Combo avec ACE Pro",
        "version_logiciel": "Anycubic Slicer Next 1.3.9.4 et versions ultérieures signalées",
        "systeme": "Windows 11",
    }
    assert all(entree["nature"] == "hypothèse externe" for entree in entrees)
    assert all(entree["limites"] for entree in entrees)
    assert all(entree["source"].startswith("https://github.com/ANYCUBIC-3D/AnycubicSlicerNext/issues/")
               for entree in entrees)
    assert all("valeurs" not in entree and "reglages_recommandes" not in entree for entree in entrees)


def test_priorite_documentaire_ne_vaut_quapres_comparabilite():
    """La hiérarchie documentaire ne classe pas des cas non comparables."""
    rangs = [
        retours_communautaires.priorite_documentaire(nature, comparabilite_etablie=True)
        for nature in ("mesure locale", "observation locale", "fabricant", "hypothèse externe")
    ]
    assert rangs == sorted(rangs)
    assert retours_communautaires.priorite_documentaire("hypothèse externe", comparabilite_etablie=False) is None
    with pytest.raises(ValueError, match="nature documentaire inconnue"):
        retours_communautaires.priorite_documentaire("profil importé", comparabilite_etablie=True)


def test_registre_rendu_modifiable_par_appelant_sans_mutation_globale():
    """Les consommateurs ne peuvent pas modifier les entrées du registre partagé."""
    premier = retours_communautaires.lire_registre()
    premier[0]["conditions"]["machine"] = "autre machine"
    second = retours_communautaires.lire_registre()
    assert second[0]["conditions"]["machine"] != "autre machine"


def test_validation_refuse_source_et_champ_non_documentaires():
    """Le registre n’accepte ni lien tiers arbitraire ni champ propice à l’adoption d’un réglage."""
    import copy
    import json
    from importlib.resources import files

    registre = json.loads(files("benchab").joinpath("reference/retours-communautaires.json").read_text())
    with pytest.raises(ValueError, match="Schéma"):
        retours_communautaires._valider({**registre, "schema": True})

    identifiant_non_textuel = copy.deepcopy(registre)
    identifiant_non_textuel["retours"][0]["id"] = []
    with pytest.raises(ValueError, match="Identifiant"):
        retours_communautaires._valider(identifiant_non_textuel)

    mauvais_lien = copy.deepcopy(registre)
    mauvais_lien["retours"][0]["source"] = "https://example.org/issue/9"
    with pytest.raises(ValueError, match="Source primaire"):
        retours_communautaires._valider(mauvais_lien)

    mauvais_champ = copy.deepcopy(registre)
    mauvais_champ["retours"][0]["reglages_recommandes"] = {"vitesse": "aucune"}
    with pytest.raises(ValueError, match="non prévue"):
        retours_communautaires._valider(mauvais_champ)
