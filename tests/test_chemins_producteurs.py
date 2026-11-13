"""Les producteurs utilisent l’atelier sélectionné hors du dépôt."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from benchab.conception import texture
from benchab.fabrication import anycubic, resistance, trancher
from benchab.mesure import environnement


@pytest.fixture
def atelier_externe(monkeypatch):
    """Crée un atelier jetable hors des racines temporaires implicites."""
    with TemporaryDirectory(prefix=".benchab-test-chemins-", dir=Path.home()) as dossier:
        atelier = Path(dossier) / "atelier"
        atelier.mkdir()
        monkeypatch.setenv("BENCHAB_PRIVE", str(atelier))
        yield atelier


def test_nommer_plaques_utilise_atelier_configure(atelier_externe):
    """Une archive de l’atelier choisi passe la garde de chemin."""
    cible = atelier_externe / "absente.3mf"
    with pytest.raises(FileNotFoundError):
        anycubic.nommer_plaques(cible, ["Plaque"])


def test_rapport_environnement_utilise_atelier_explicite(atelier_externe, monkeypatch):
    """Le rapport CLI sort dans l’atelier --prive, prioritaire sur l’environnement."""
    autre = atelier_externe / "autre"
    autre.mkdir()
    monkeypatch.setattr(environnement, "collecter", lambda prive: {"resultats": [], "code": 0})
    cible = autre / "environnement.json"
    assert environnement.main(["--prive", str(autre), "--json", str(cible)]) == 0
    assert cible.is_file()


def test_rapport_resistance_utilise_atelier_configure(atelier_externe, monkeypatch):
    """Le point d’entrée de résistance publie son rapport dans l’atelier choisi."""
    registre = atelier_externe / "materiaux.json"
    registre.write_text('{"materiaux": {}}')
    cible = atelier_externe / "resistance.json"
    monkeypatch.setattr(resistance, "auditer_projet", lambda *args: {"statut": "PASS", "pieces": {}})
    assert resistance.main(["--projet", str(atelier_externe), "--tranche", str(atelier_externe / "test.3mf"),
                            "--json", str(cible), "--materiaux", str(registre)]) == 0
    assert cible.is_file()


def test_texture_accepte_atelier_configure(atelier_externe, monkeypatch):
    """Le témoin texture publie dans l’atelier configuré."""
    cible = atelier_externe / "texture"
    monkeypatch.setattr(texture, "appliquer", lambda mesh, spec: (mesh, {}))
    assert texture.main(["essai", "--sortie", str(cible)]) == 0
    assert (cible / "cube-molete.stl").is_file()


def test_tranchage_accepte_temporaire_natif(tmp_path):
    """Une sortie dans le temporaire natif passe la garde avant le moteur."""
    source = tmp_path / "source.3mf"
    source.touch()
    sortie = tmp_path / "sortie"
    sortie.mkdir()
    (sortie / "source.gcode.3mf").touch()
    with pytest.raises(ValueError, match="Destination existante"):
        trancher.trancher_fichier(source, sortie)
