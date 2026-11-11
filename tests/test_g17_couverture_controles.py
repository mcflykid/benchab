"""Vérifie qu’un cercle exact ne disparaît pas des contrôles linéaires."""

from zipfile import ZipFile

from benchab.controles import colonnes_isolees, disposition_reelle


def test_cercle_complet_interdit_une_disposition_dite_conforme(tmp_path):
    """Un dépôt circulaire à extrémités égales ne vaut pas un trajet nul."""
    placement = {"plates": [{"name": "témoin"}], "objects": [{"plate": 1}]}
    avant = (
        "G90\nM83\nG17\nG1 X10 Y10 Z0.2 F1200\n"
        "EXCLUDE_OBJECT_START NAME=temoin\n;TYPE:Outer wall\nG1 X12 E0.1\n"
    )
    apres = "EXCLUDE_OBJECT_END NAME=temoin\n"
    r = {}
    for nom, arc in (("lineaire", ""), ("circulaire", "G2 X12 Y10 I2 J0 E1\n")):
        source = tmp_path / f"{nom}.3mf"
        with ZipFile(source, "w") as archive:
            archive.writestr("Metadata/plate_1.gcode", avant + arc + apres)
        r[nom] = disposition_reelle.analyser(source, placement, plateau=[220, 220, 250])
    assert r["lineaire"]["conforme"] is True
    assert r["circulaire"]["conforme"] is False
    assert any("arc analytique" in e for e in r["circulaire"]["plaques"][0]["erreurs"])


def test_cercle_complet_n_est_pas_une_corde_dans_les_colonnes():
    """Des trajets linéaires voisins ne cachent pas l’absence de couverture de l’arc."""
    gcode = (
        "G90\nM83\nG17\n;LAYER_CHANGE\n;Z:0.2\nG1 X10 Y10 Z0.2 F1200\n"
        ";TYPE:Outer wall\nG1 X12 E0.1\n"
    )
    assert colonnes_isolees.controler(gcode)["statut"] == "PASS"
    rapport = colonnes_isolees.controler(gcode + "G3 X12 Y10 I2 J0 E1\n")
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert "analyse de colonne linéaire" in rapport["detail"]
