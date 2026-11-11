"""Qualifie le G-code de première couche sur un contexte 3MF commun."""

from zipfile import ZipFile

from benchab.controles import premiere_couche as PC

GCODE = "M83\r\n;Z:.2\r\n;TYPE:Outer wall\r\nG1 X30 E1 F600"


def test_une_ouverture_avec_identifiants_et_fins_de_ligne_conserves(tmp_path, monkeypatch):
    """Deux plaques sont relues du même ZIP, dans l’ordre historique des membres."""
    chemin = tmp_path / "deux.3mf"
    with ZipFile(chemin, "w") as archive:
        archive.writestr("Metadata/plate_2.gcode", "G90")
        archive.writestr("Metadata/plate_010.gcode", GCODE)
    ouvertures = []
    init = ZipFile.__init__

    def compter(self, *args, **kwargs):
        ouvertures.append(1)
        init(self, *args, **kwargs)

    monkeypatch.setattr(ZipFile, "__init__", compter)
    assert list(PC.gcodes(chemin)) == [(10, GCODE), (2, "G90")]
    assert ouvertures == [1]


def test_alias_duplique_refuse_avant_la_publication(tmp_path, capsys):
    """Deux graphies d’une plaque rendent le contrôle non vérifié et la source intacte."""
    chemin = tmp_path / "alias.3mf"
    sortie = tmp_path / "nouveau.json"
    with ZipFile(chemin, "w") as archive:
        archive.writestr("Metadata/plate_01.gcode", GCODE)
        archive.writestr("Metadata/plate_1.gcode", GCODE)
    source = chemin.read_bytes()
    assert PC.main([str(chemin), "--json", str(sortie)]) == 2
    message = capsys.readouterr().err
    assert "NON VÉRIFIÉ" in message and "numéro de plaque dupliqué" in message
    assert not sortie.exists()
    assert chemin.read_bytes() == source


def test_archive_invalide_ne_publie_pas_de_rapport(tmp_path, capsys):
    """Une archive incomplète renvoie le diagnostic exact sans verdict de plaque."""
    chemin = tmp_path / "incomplete.3mf"
    chemin.write_bytes("Archive de test incomplète".encode("utf-8"))
    sortie = tmp_path / "nouveau.json"
    assert PC.main([str(chemin), "--json", str(sortie)]) == 2
    message = capsys.readouterr().err
    assert "NON VÉRIFIÉ" in message and "File is not a zip file" in message
    assert not sortie.exists()
