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


def _poche(x, y, *, lx=6, ly=4, z=2):
    return {"x_mm": x, "y_mm": y, "largeur_x_mm": lx, "largeur_y_mm": ly,
            "profondeur_mm": z, "source_cotes": "plan coté"}


def test_poche_superieure_et_lamage_rejouables_dans_la_meme_fiche(tmp_path):
    """Les volumes disjoints sont retirés et seuls les nombres entrent dans le script."""
    from math import pi

    from benchab.conception.fiche_cao import generer

    fiche = _fiche()
    fiche["percages"][0]["lamage"] = {"diametre_mm": 9, "profondeur_mm": 2, "source_cotes": "plan coté"}
    fiche["poches_rectangulaires"] = [_poche(-8, 2)]
    source = tmp_path / "fiche.json"
    for i in (0, 1):
        source.write_text(json.dumps(fiche, indent=i, sort_keys=bool(i)), encoding="utf-8")
        generer(source, tmp_path / f"p{i}")
    plan = json.loads((tmp_path / "p0/plan.json").read_bytes())
    assert plan["poches_rectangulaires"] == [_poche(-8, 2)]
    assert plan["attendu"]["volume_mm3"] == pytest.approx(
        2400 - pi * 2.5**2 * 4 - pi * (4.5**2 - 2.5**2) * 2 - 6 * 4 * 2
    )
    for fichier in ("plan.json", "modele.py"):
        assert (tmp_path / "p0" / fichier).read_bytes() == (tmp_path / "p1" / fichier).read_bytes()
    assert b"plan cot" not in (tmp_path / "p0/modele.py").read_bytes()


@pytest.mark.parametrize(
    "poches,trou,message",
    [
        ([_poche(12, 0)], None, "marge"),
        ([_poche(0, 0, z=4)], None, "profondeur"),
        ([_poche(0, 0), _poche(6, 0)], None, "chevauchement"),
        ([_poche(5, 0)], {"x_mm": 0, "y_mm": 0, "diametre_mm": 4}, "chevauchement"),
    ],
)
def test_poche_refuse_bord_fond_et_interference(tmp_path, poches, trou, message):
    """Une poche tangente au bord ou à un outil voisin ne publie aucun plan."""
    from benchab.conception.fiche_cao import generer

    fiche = {**_fiche(), "poches_rectangulaires": poches, "percages": [trou] if trou else []}
    source = tmp_path / "fiche.json"
    source.write_text(json.dumps(fiche), encoding="utf-8")
    cible = tmp_path / "sortie"
    with pytest.raises(ValueError, match=message):
        generer(source, cible)
    assert not cible.exists()


def test_poche_distincte_dun_passage_ne_confond_pas_coin_et_boite_englobante(tmp_path):
    """Le coin du rectangle laisse un jeu réel, même si les boîtes XY se recouvrent."""
    from benchab.conception.fiche_cao import generer

    fiche = {**_fiche(), "poches_rectangulaires": [_poche(3.8, 3.8, lx=4, ly=4)],
             "percages": [{"x_mm": 0, "y_mm": 0, "diametre_mm": 4}]}
    source = tmp_path / "fiche.json"
    source.write_text(json.dumps(fiche), encoding="utf-8")
    assert generer(source, tmp_path / "sortie")["statut"] == "NON VÉRIFIÉ"  # Tenue hors périmètre.


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


def test_base_cylindrique_avec_percage_et_poche_rejouable(tmp_path):
    """Le volume du disque retire les deux usinages disjoints, sans réécriture."""
    from math import pi

    from benchab.conception.fiche_cao import generer

    fiche = {
        "schema": 1, "id": "disque-test", "base_cylindrique": {"diametre_mm": 30, "epaisseur_mm": 6},
        "percages": [{"x_mm": 5, "y_mm": 0, "diametre_mm": 4}],
        "poches_rectangulaires": [_poche(-5, 0, lx=4, ly=3, z=2)],
    }
    source = tmp_path / "fiche.json"
    for i in (0, 1):
        source.write_text(json.dumps(fiche, indent=i, sort_keys=bool(i)), encoding="utf-8")
        generer(source, tmp_path / f"p{i}")
    plan = json.loads((tmp_path / "p0/plan.json").read_bytes())
    assert plan["base_cylindrique"] == fiche["base_cylindrique"]
    assert plan["attendu"]["dimensions_mm"] == [30, 30, 6]
    assert plan["attendu"]["volume_mm3"] == pytest.approx(pi * (15**2 - 2**2) * 6 - 4 * 3 * 2)
    for nom in ("modele.py", "plan.json"):
        assert (tmp_path / "p0" / nom).read_bytes() == (tmp_path / "p1" / nom).read_bytes()


@pytest.mark.parametrize(
    ("trous", "poches"),
    [
        ([{"x_mm": 10, "y_mm": 10, "diametre_mm": 2}], []),
        ([], [_poche(10, 10, lx=2, ly=2)]),
    ],
)
def test_base_cylindrique_refuse_operations_hors_du_cercle(tmp_path, trous, poches):
    """Une opération dans la boîte englobante peut sortir de la matière du disque."""
    from benchab.conception.fiche_cao import generer

    source = tmp_path / "fiche.json"
    source.write_text(json.dumps({
        "schema": 1, "id": "disque-bord", "base_cylindrique": {"diametre_mm": 30, "epaisseur_mm": 6},
        "percages": trous, "poches_rectangulaires": poches,
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="marge"):
        generer(source, tmp_path / "p")
    assert not (tmp_path / "p").exists()


def test_fraisage_supprime_seulement_le_tronc_autour_du_passage(tmp_path):
    """Le diamètre d’entrée et l’angle imposent la profondeur et le volume nominal."""
    from math import pi

    from benchab.conception.fiche_cao import generer

    data = _fiche()
    data["percages"][0]["fraisage"] = {
        "diametre_mm": 9, "angle_inclus_deg": 90, "source_cotes": "géométrie de référence",
    }
    source = tmp_path / "fiche.json"
    for i in (0, 1):
        source.write_text(json.dumps(data, indent=i, sort_keys=bool(i)), encoding="utf-8")
        generer(source, tmp_path / f"p{i}")
    plan = json.loads((tmp_path / "p0/plan.json").read_bytes())
    fraisage = plan["percages"][0]["fraisage"]
    assert fraisage["profondeur_calculee_mm"] == pytest.approx(2)
    assert fraisage["angle_inclus_deg"] == 90
    anneau = pi * 2 * ((4.5**2 + 4.5 * 2.5 + 2.5**2) / 3 - 2.5**2)
    assert plan["attendu"]["volume_mm3"] == pytest.approx(2400 - pi * 2.5**2 * 4 - anneau)
    for fichier in ("plan.json", "modele.py"):
        assert (tmp_path / "p0" / fichier).read_bytes() == (tmp_path / "p1" / fichier).read_bytes()
    script = (tmp_path / "p0/modele.py").read_bytes()
    assert b"Cone(" in script and b"g\xc3\xa9om\xc3\xa9trie de r" not in script


@pytest.mark.parametrize(
    ("fraisage", "autres", "message"),
    [
        ({"diametre_mm": 9, "angle_inclus_deg": True}, {}, "angle"),
        ({"diametre_mm": 5, "angle_inclus_deg": 90}, {}, "diamètre supérieur"),
        ({"diametre_mm": 15, "angle_inclus_deg": 90}, {}, "profondeur"),
        ({"diametre_mm": 9, "angle_inclus_deg": 90}, {"lamage": {}}, "exclusifs"),
        ({"diametre_mm": 9, "angle_inclus_deg": 90}, {"y_mm": 7}, "marge insuffisante"),
    ],
)
def test_fraisage_invalide_ne_publie_aucun_plan(tmp_path, fraisage, autres, message):
    """Fond débouchant, angle invalide et opérations concurrentes sont refusés."""
    from benchab.conception.fiche_cao import generer

    data = _fiche()
    data["percages"][0].update(autres, fraisage={**fraisage, "source_cotes": "fixture"})
    source = tmp_path / "fiche.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        generer(source, tmp_path / "sortie")
    assert not (tmp_path / "sortie").exists()


def test_poche_arrondie_volume_et_script_deterministes(tmp_path):
    """Le plan retire seulement l’aire aux coins arrondis, à la profondeur déclarée."""
    from math import pi

    from benchab.conception.fiche_cao import generer

    fiche = _fiche()
    fiche["poches_rectangulaires"] = [{**_poche(-8, 2), "rayon_angle_mm": 1}]
    source = tmp_path / "fiche.json"
    for i in (0, 1):
        source.write_text(json.dumps(fiche, indent=i, sort_keys=bool(i)), encoding="utf-8")
        generer(source, tmp_path / f"p{i}")
    plan = json.loads((tmp_path / "p0/plan.json").read_bytes())
    assert plan["poches_rectangulaires"][0]["rayon_angle_mm"] == 1
    assert plan["attendu"]["volume_mm3"] == pytest.approx(2400 - pi * 2.5**2 * 4 - (24 - 4 + pi) * 2)
    script = (tmp_path / "p0/modele.py").read_bytes()
    assert b"RectangleRounded(6.0, 4.0, 1.0)" in script
    assert script == (tmp_path / "p1/modele.py").read_bytes()
    assert (tmp_path / "p0/plan.json").read_bytes() == (tmp_path / "p1/plan.json").read_bytes()


@pytest.mark.parametrize("centre,accepte", [(2.0, True), (1.8, False)])
def test_poche_arrondie_et_percage_verifient_le_contour_reel(tmp_path, centre, accepte):
    """Un perçage peut libérer un coin arrondi tout en croisant l’encombrement."""
    from benchab.conception.fiche_cao import generer

    fiche = {**_fiche(), "poches_rectangulaires": [{**_poche(0, 0, lx=4, ly=4), "rayon_angle_mm": 1.8}],
             "percages": [{"x_mm": centre, "y_mm": centre, "diametre_mm": 1}]}
    source = tmp_path / "fiche.json"
    source.write_text(json.dumps(fiche))
    if accepte:
        assert generer(source, tmp_path / "p")["statut"] == "NON VÉRIFIÉ"
    else:
        with pytest.raises(ValueError, match="chevauchement"):
            generer(source, tmp_path / "p")


@pytest.mark.parametrize("rayon", [0, 2, 3])
def test_poche_arrondie_refuse_rayon_absurde(tmp_path, rayon):
    """Un rayon nul ou tangentiel ne déclenche pas une forme dégénérée."""
    from benchab.conception.fiche_cao import generer

    fiche = {**_fiche(), "poches_rectangulaires": [{**_poche(-8, 2), "rayon_angle_mm": rayon}]}
    source = tmp_path / "fiche.json"
    source.write_text(json.dumps(fiche))
    with pytest.raises(ValueError, match="rayon"):
        generer(source, tmp_path / "p")
    assert not (tmp_path / "p").exists()


def test_poche_arrondie_tient_dans_un_disque_ou_le_coin_carre_touche(tmp_path):
    """La portée radiale est mesurée sur le contour et non le coin supprimé."""
    from benchab.conception.fiche_cao import generer

    fiche = {"schema": 1, "id": "disque", "base_cylindrique": {"diametre_mm": 20, "epaisseur_mm": 4},
             "percages": [], "poches_rectangulaires": [{**_poche(6, 4, lx=4, ly=4), "rayon_angle_mm": 1.5}]}
    source = tmp_path / "fiche.json"
    source.write_text(json.dumps(fiche))
    assert generer(source, tmp_path / "p")["statut"] == "PASS"


@pytest.mark.parametrize("decalage,accepte", [(3.6, True), (3.0, False)])
def test_poches_arrondies_intersection_selon_leurs_deux_contours(tmp_path, decalage, accepte):
    """Deux arrondis voisins peuvent rester disjoints malgré des boîtes qui se croisent."""
    from benchab.conception.fiche_cao import generer

    poches = [{**_poche(c, c, lx=4, ly=4), "rayon_angle_mm": 1.5} for c in (0, decalage)]
    fiche = {**_fiche(), "percages": [], "poches_rectangulaires": poches}
    source = tmp_path / "fiche.json"
    source.write_text(json.dumps(fiche))
    if accepte:
        assert generer(source, tmp_path / "p")["statut"] == "NON VÉRIFIÉ"
    else:
        with pytest.raises(ValueError, match="chevauchement"):
            generer(source, tmp_path / "p")


@pytest.mark.parametrize("rayon", [None, 1])
def test_ouverture_rectangulaire_traversante_explicite(tmp_path, rayon):
    """Une ouverture enlève la section sur toute l’épaisseur, sans modifier la base."""
    from math import pi

    from benchab.conception.fiche_cao import generer

    ouverture = _poche(-8, 2)
    ouverture.pop("profondeur_mm")
    ouverture["traversante"] = True
    if rayon is not None:
        ouverture["rayon_angle_mm"] = rayon
    fiche = {**_fiche(), "percages": [], "poches_rectangulaires": [ouverture]}
    source = tmp_path / "fiche.json"
    source.write_text(json.dumps(fiche), encoding="utf-8")
    generer(source, tmp_path / "sortie")
    plan = json.loads((tmp_path / "sortie/plan.json").read_bytes())
    assert plan["poches_rectangulaires"] == [{**ouverture, "profondeur_mm": 4.0}]
    assert plan["attendu"]["volume_mm3"] == pytest.approx(2400 - (24 - (4 - pi) * (rayon or 0)**2) * 4)
    script = (tmp_path / "sortie/modele.py").read_text()
    assert "Pos(-8.0, 2.0, -1.0)" in script
    assert "6.0" in script


@pytest.mark.parametrize(
    "option",
    [{"traversante": True, "profondeur_mm": 2}, {"traversante": False}, {"traversante": 1}],
)
def test_ouverture_traversante_refuse_declaration_ambigue(tmp_path, option):
    """Un entier, un faux drapeau ou deux profondeurs ne produisent pas de plan."""
    from benchab.conception.fiche_cao import generer

    ouverture = _poche(-8, 2)
    ouverture.pop("profondeur_mm")
    fiche = {**_fiche(), "percages": [], "poches_rectangulaires": [{**ouverture, **option}]}
    source = tmp_path / "fiche.json"
    source.write_text(json.dumps(fiche), encoding="utf-8")
    with pytest.raises(ValueError, match="profondeur|traversante"):
        generer(source, tmp_path / "sortie")
    assert not (tmp_path / "sortie").exists()
