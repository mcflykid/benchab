"""Les variantes d’orientation sont publiées dans un dossier neuf."""

import json

from benchab.controles import orientations_supports


def test_orientations_refuse_dossier_de_preuves_existant(tmp_path):
    """Aucun rapport antérieur ne peut être remplacé par une nouvelle étude."""
    profil = tmp_path / "profil.3mf"
    profil.write_bytes(b"profil de test")
    dossier = tmp_path / "orientations"
    dossier.mkdir()
    ancien = dossier / "orientations.json"
    ancien.write_bytes(b"preuve precedente")

    assert orientations_supports.main(["--profil", str(profil), "--sortie", str(dossier)]) == 2

    assert ancien.read_bytes() == b"preuve precedente"
    assert profil.read_bytes() == b"profil de test"


def test_orientations_publie_rapport_dans_dossier_neuf_sans_trancher(tmp_path, monkeypatch):
    """Une étude vide ne lance pas le moteur et conserve le contrat JSON."""
    profil = tmp_path / "profil.3mf"
    profil.write_bytes(b"profil de test")
    dossier = tmp_path / "orientations"

    def interdit(*_args):
        raise AssertionError("aucun tranchage demandé dans ce test")

    monkeypatch.setattr(orientations_supports, "trancher", interdit)
    assert orientations_supports.main(["--profil", str(profil), "--sortie", str(dossier)]) == 0
    assert json.loads((dossier / "orientations.json").read_bytes())["pieces"] == {}


def test_orientations_publie_ensemble_ou_rien_si_moteur_refuse(tmp_path, monkeypatch):
    """Les STL et leur rapport sont disponibles ensemble sans modifier la source."""
    import trimesh

    profil = tmp_path / "profil.3mf"
    profil.write_bytes(b"profil de test")
    source = tmp_path / "piece.stl"
    trimesh.creation.box((20, 20, 20)).export(source)
    avant = source.read_bytes()
    dossier = tmp_path / "orientations"
    commun = [str(source), "--profil", str(profil), "--sortie", str(dossier), "--tranches", "1"]

    def refus(*_args):
        raise ValueError("trancheur synthétique refusé")

    monkeypatch.setattr(orientations_supports, "trancher", refus)
    assert orientations_supports.main(commun) == 2
    assert not dossier.exists()

    monkeypatch.setattr(orientations_supports, "trancher", lambda *_: [{"support_g": 1.0}])
    assert orientations_supports.main(commun) == 0
    assert (dossier / "piece-1.stl").is_file()
    rapport = json.loads((dossier / "orientations.json").read_bytes())
    assert rapport["pieces"]["piece"]["meilleures"][0]["tranchage"] == {"support_g": 1.0}
    assert source.read_bytes() == avant
