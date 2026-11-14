"""Identité de version entre paquet, métadonnées installées et CLI."""

import importlib.metadata
import tomllib
from pathlib import Path

from benchab import __version__
from benchab.cli import main


def test_version_unique_partagee_par_paquet_distribution_et_cli(capsys):
    """Le paquet installé et son option CLI exposent la même version déclarée."""
    racine = Path(__file__).resolve().parents[1]
    with (racine / "pyproject.toml").open("rb") as fichier:
        configuration = tomllib.load(fichier)
    projet = configuration["project"]
    assert "version" not in projet
    assert "version" in projet["dynamic"]
    assert configuration["tool"]["hatch"]["version"]["path"] == "benchab/__init__.py"
    version_installee = importlib.metadata.version("benchab")
    assert version_installee == __version__, (
        f"Métadonnées BENCHAB {version_installee}, code {__version__}. "
        "Depuis ce checkout, exécuter uv sync --locked --reinstall-package benchab."
    )
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__
    readme = (racine / "README.md").read_text()
    assert "aucune version livrée" not in readme
    assert "CHANGELOG.md" in readme and "tags Git" in readme
