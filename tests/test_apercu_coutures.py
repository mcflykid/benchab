"""Vérifie la projection du rapport, sans déduire ni déplacer une couture."""

import json
from xml.etree import ElementTree as ET

import pytest


def _rapport():
    return {
        "schema": 1,
        "source_sha256": "a" * 64,
        "liaison_source": "PASS",
        "objet_id": "Pièce <fixture>",
        "plaque": 1,
        "statut": "WARN",
        "regions": [
            {"id": "visible", "visibilite": "visible", "polygone_xy_mm": [[0, 0], [4, 0], [4, 1]],
             "z_min_mm": 0, "z_max_mm": 2},
        ],
        "boucles": [
            {"id": "b1", "couche": 1, "ligne_debut": 10, "point_depart_xyz_mm": [1, 0.1, 0.2],
             "region_id": "visible", "visibilite": "visible", "statut": "WARN"},
            {"id": "b2", "couche": 2, "ligne_debut": 20, "point_depart_xyz_mm": [1, 0.1, 0.4],
             "region_id": "visible", "visibilite": "visible", "statut": "WARN"},
            {"id": "b3", "couche": 3, "ligne_debut": 30, "point_depart_xyz_mm": None,
             "region_id": None, "visibilite": None, "statut": "NON VÉRIFIÉ"},
        ],
    }


def test_apercu_lie_au_sha_et_aux_points_du_rapport():
    """Les marqueurs gardent leurs lignes/couches et les inconnues sans point sont signalées."""
    from benchab.visualisation.apercu_coutures import rendre

    rapport = _rapport()
    xml = rendre(rapport)
    svg = ET.fromstring(xml)
    ns = {"s": "http://www.w3.org/2000/svg"}
    assert svg.get("data-source-sha256") == rapport["source_sha256"]
    points = svg.findall(".//s:circle", ns)
    assert len(points) == 4
    assert [(p.get("data-plan"), p.get("data-couche"), p.get("data-ligne")) for p in points] == [
        ("XY", "1", "10"), ("XY", "2", "20"), ("XZ", "1", "10"), ("XZ", "2", "20"),
    ]
    assert points[0].get("cx") == points[1].get("cx")
    assert points[2].get("cy") != points[3].get("cy")
    assert "&lt;fixture&gt;" in xml
    assert "1 boucle sans coordonnées" in xml
    assert svg.find(".//s:script", ns) is None


def test_rapport_et_apercu_publies_dans_un_dossier_neuf(tmp_path):
    """Une nouvelle publication n’écrase ni le rapport précédent ni son aperçu."""
    from benchab.visualisation.apercu_coutures import publier

    rapport = _rapport()
    cible = tmp_path / "resultat"
    resume = publier(rapport, cible)
    assert json.loads((cible / "rapport.json").read_bytes()) == rapport
    assert resume["statut"] == rapport["statut"]
    svg = (cible / "apercu.svg").read_bytes()
    with pytest.raises(FileExistsError):
        publier(rapport, cible)
    assert (cible / "apercu.svg").read_bytes() == svg


def test_cli_coutures_avertit_et_lie_les_deux_preuves(tmp_path, capsys, monkeypatch):
    """Le parcours entier sauvegarde aperçu et rapport sans Base ni changement de source."""
    from test_coutures import _archive, _declaration

    from benchab import cli
    from benchab.qualite.statuts import code_statut

    def base_interdite(*_args, **_kwargs):
        raise AssertionError("Le contrôle de couture ne doit pas ouvrir Base().")

    monkeypatch.setattr(cli, "Base", base_interdite)
    source = _archive(tmp_path)
    avant = source.read_bytes()
    prive = tmp_path / "prive"
    prive.mkdir()
    declaration = prive / "regions.json"
    declaration.write_text(json.dumps(_declaration(source)), encoding="utf-8")
    sortie = prive / "preuve"
    args = ["--prive", str(prive), "controle", "coutures", str(source),
            "--declaration", str(declaration), "--sortie", str(sortie)]
    assert cli.main(args) == code_statut("WARN")
    resume = json.loads(capsys.readouterr().out)
    rapport = json.loads((sortie / "rapport.json").read_bytes())
    svg = ET.parse(sortie / "apercu.svg").getroot()
    assert resume["statut"] == rapport["statut"] == "WARN"
    assert svg.get("data-source-sha256") == rapport["source_sha256"]
    assert all(p["statut"] == "CANDIDAT" for p in rapport["propositions"]) and rapport["propositions"]
    assert source.read_bytes() == avant and not (prive / "benchab.sqlite").exists()

    # Une déclaration dont l’empreinte est fausse ne reçoit pas de dessin lié à la source.
    declaration.write_text(json.dumps({**_declaration(source), "source_sha256": "0" * 64}), encoding="utf-8")
    sortie_refusee = prive / "rapport-sans-apercu"
    assert cli.main([*args[:-1], str(sortie_refusee)]) == code_statut("NON VÉRIFIÉ")
    assert json.loads(capsys.readouterr().out)["apercu"] is None
    rapport_refuse = json.loads((sortie_refusee / "rapport.json").read_bytes())
    assert rapport_refuse["liaison_source"] == "NON VÉRIFIÉ"
    assert not (sortie_refusee / "apercu.svg").exists()
