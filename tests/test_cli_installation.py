"""Le diagnostic public n'ouvre ni migration ni mesure, même en cas de refus."""
import hashlib
import json

import pytest

from benchab.cli import main
from benchab.donnees.base import Base


def _empreintes(racine):
    """Empreinte les fichiers privés, sans unité ni modification."""
    return {p.relative_to(racine).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in racine.rglob('*') if p.is_file()}


@pytest.mark.parametrize('options', [[], ['--option-inconnue'], ['--token', 'valeur-fixture']])
def test_installation_cli_ne_modifie_pas_atelier(tmp_path, capsys, options):
    """Le parcours, l'aide et ses erreurs ne changent ni SQLite, ni exports, ni nombre de fichiers."""
    prive = tmp_path / 'prive'
    base = Base(prive)
    base.fermer()
    avant = _empreintes(prive)
    code = main(['--prive', str(prive), 'controle', 'installation', '--racine', str(tmp_path), *options])
    assert code != 0
    assert _empreintes(prive) == avant
    if not options:
        assert json.loads(capsys.readouterr().out)['lecture_seule'] is True


def test_installation_sortie_conserve_le_detail_hors_du_resume(tmp_path, capsys):
    """L’option de rapport ne modifie ni le verdict ni les fichiers de l’atelier."""
    prive = tmp_path / "prive"
    Base(prive).fermer()
    avant = _empreintes(prive)
    cmd = ["--prive", str(prive), "controle", "installation", "--racine", str(tmp_path)]
    code = main(cmd)
    court = json.loads(capsys.readouterr().out)
    rapport = tmp_path / "diagnostic.json"
    assert main([*cmd, "--sortie", str(rapport)]) == code
    affiche = json.loads(capsys.readouterr().out)
    assert affiche.pop("rapport") == str(rapport)
    assert affiche == court
    detail = json.loads(rapport.read_text())
    assert detail["code"] == code and "detail" in detail
    assert _empreintes(prive) == avant
