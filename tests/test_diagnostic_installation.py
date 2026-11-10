"""Contrats du diagnostic d'installation en lecture seule."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from benchab.qualite.installation import diagnostiquer


def _base_valide(chemin: Path) -> None:
    """Crée une base miniature structurée pour tester les PRAGMA en lecture seule."""
    with sqlite3.connect(chemin) as connexion:
        connexion.executescript(
            "CREATE TABLE parent(id INTEGER PRIMARY KEY);"
            "CREATE TABLE enfant(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));"
            "CREATE TABLE seances(id TEXT PRIMARY KEY, debut TEXT, lot TEXT, fin TEXT);"
            "INSERT INTO parent VALUES (1); INSERT INTO enfant VALUES (1, 1);"
        )


def test_diagnostic_installation_complete_et_ne_change_pas_les_entrees(tmp_path: Path) -> None:
    """Une installation saine passe sans altérer profil, base ou date du profil."""
    prive = tmp_path / "prive"
    prive.mkdir()
    profil = prive / "profil.json"
    profil.write_text('{"quality":"draft"}', encoding="utf-8")
    from benchab.donnees.base import Base

    instance = Base(prive, creer=True)
    base = instance.prive / "benchab.sqlite"
    instance.fermer()
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    ignore = tmp_path / ".gitignore"
    ignore.write_text("/prive/\n", encoding="utf-8")
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "benchab"\nversion = "0.1.0"\nrequires-python = ">=3.13"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        'version = 1\nrequires-python = ">=3.13"\n[[package]]\nname = "benchab"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    attendu = hashlib.sha256(profil.read_bytes()).hexdigest()
    avant = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in (profil, base)}

    rapport = diagnostiquer(
        tmp_path,
        profils=(profil,),
        profils_attendus={profil: attendu},
        base=base,
        python=python,
        versions_testees=("2.0.0.3",),
        version_slicer="2.0.0.3",
        espace_libre_octets=100,
        espace_minimum_octets=50,
    )

    assert rapport["statut"] == "NON VÉRIFIÉ"
    controles = {item["nom"]: item["statut"] for item in rapport["controles"]}
    assert controles["sqlite"] == "PASS"
    assert controles["profils"] == "PASS"
    assert controles["slicer_binaire"] == "NON VÉRIFIÉ"
    assert avant == {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in (profil, base)}


def test_diagnostic_incomplet_quand_version_slicer_non_observable(tmp_path: Path) -> None:
    """Une version d'application absente reste NON VÉRIFIÉ, sans exécuter le slicer."""
    rapport = diagnostiquer(tmp_path, version_slicer=None)

    controle = next(item for item in rapport["controles"] if item["nom"] == "slicer")
    assert controle["statut"] == "NON VÉRIFIÉ"


def test_diagnostic_signale_anomalie_sqlite_et_espace_insuffisant(tmp_path: Path) -> None:
    """Une base corrompue logiquement et un espace sous le seuil rendent le résultat WARN/FAIL."""
    base = tmp_path / "benchab.sqlite"
    with sqlite3.connect(base) as connexion:
        connexion.executescript(
            "CREATE TABLE parent(id INTEGER PRIMARY KEY);"
            "CREATE TABLE enfant(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));"
            "INSERT INTO enfant VALUES (1, 404);"
        )

    rapport = diagnostiquer(
        tmp_path,
        base=base,
        espace_libre_octets=1,
        espace_minimum_octets=10,
    )

    controles = {item["nom"]: item["statut"] for item in rapport["controles"]}
    assert controles["sqlite"] == "FAIL"
    assert controles["espace"] == "WARN"
    assert rapport["statut"] == "FAIL"


def test_non_verifie_precede_warn_dans_aggregation() -> None:
    """La lacune de couverture reste visible même si un autre contrôle émet WARN."""
    from benchab.qualite.installation import _statut_global

    assert _statut_global([{"statut": "WARN"}, {"statut": "NON VÉRIFIÉ"}]) == "NON VÉRIFIÉ"


def test_regle_git_negation_annulee_est_detectee(tmp_path: Path) -> None:
    """Une négation Git ultérieure empêche de déclarer prive effectivement protégé."""
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("/prive/*\n!/prive/benchab.sqlite\n", encoding="utf-8")

    resultat = diagnostiquer(tmp_path, version_slicer="2.0", versions_testees=("2.0",))

    controle = next(item for item in resultat["controles"] if item["nom"] == "git_prive")
    assert controle["statut"] == "FAIL"


def test_base_sqlite_etrangere_nest_pas_benchab(tmp_path: Path) -> None:
    """Une base SQLite saine sans identité et schéma BENCHAB ne passe pas."""
    base = tmp_path / "benchab.sqlite"
    _base_valide(base)

    resultat = diagnostiquer(tmp_path, base=base)

    controle = next(item for item in resultat["controles"] if item["nom"] == "sqlite")
    assert controle["statut"] == "FAIL"


def test_profils_non_attendus_restent_non_verifies(tmp_path: Path) -> None:
    """Une empreinte capturée sans empreinte attendue ne certifie pas un profil."""
    profil = tmp_path / "profil.json"
    profil.write_text('{"quality":"draft"}', encoding="utf-8")

    resultat = diagnostiquer(tmp_path, profils=(profil,), versions_testees=(), version_slicer="2.0")

    controle = next(item for item in resultat["controles"] if item["nom"] == "profils")
    assert controle["statut"] == "NON VÉRIFIÉ"


def test_base_benchab_verifiee_en_lecture_seule_et_session_orpheline_warn(tmp_path: Path) -> None:
    """Une vraie base BENCHAB est reconnue et une séance sans verrou valide est signalée orpheline."""
    from benchab.donnees.base import Base

    instance = Base(tmp_path / "prive", creer=True)
    chemin = instance.prive / "benchab.sqlite"
    instance.connexion.execute(
        "INSERT INTO seances (id, debut, lot) VALUES ('s1', '2026-11-11T00:00:00+00:00', 'test')"
    )
    instance.connexion.commit()
    instance.fermer()

    resultat = diagnostiquer(tmp_path, base=chemin)

    controles = {item["nom"]: item["statut"] for item in resultat["controles"]}
    assert controles["sqlite"] == "PASS"
    assert controles["seance"] == "WARN"


def test_seance_verrouillee_est_distincte_dune_orpheline(tmp_path: Path) -> None:
    """La lecture de la séance reconnait un verrou valide et ne la classe pas comme orpheline."""
    from benchab.donnees.base import Base
    from benchab.donnees.seance import ouvrir

    instance = Base(tmp_path / "prive", creer=True)
    chemin = instance.prive / "benchab.sqlite"
    ouvrir(instance, lot="test")
    instance.fermer()

    resultat = diagnostiquer(tmp_path, base=chemin)

    controle = next(item for item in resultat["controles"] if item["nom"] == "seance")
    assert controle["statut"] == "PASS"
    assert "verrou valide" in controle["resume"]


def test_python_hors_contrainte_et_verrou_coherent_signale_echec(tmp_path: Path) -> None:
    """Une contrainte Python valide dans les deux TOML reste FAIL si l'interpréteur est trop ancien."""
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("/prive/\n", encoding="utf-8")
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "benchab"\nversion = "0.1.0"\nrequires-python = ">=99"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        'version = 1\nrequires-python = ">=99"\n[[package]]\nname = "benchab"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    resultat = diagnostiquer(tmp_path, python=python, version_slicer="2.0", versions_testees=("2.0",))

    controles = {item["nom"]: item["statut"] for item in resultat["controles"]}
    assert controles["uv"] == "PASS"
    assert controles["python"] == "FAIL"


def test_profil_refuse_symlink_et_signale_sha_attendue_differente(tmp_path: Path) -> None:
    """Le diagnostic refuse les liens et classe une empreinte attendue discordante en FAIL."""
    profil = tmp_path / "profil.json"
    profil.write_text('{"quality":"draft"}', encoding="utf-8")
    lien = tmp_path / "profil-lie.json"
    lien.symlink_to(profil)

    resultat = diagnostiquer(
        tmp_path,
        profils=(profil, lien),
        profils_attendus={profil: "0" * 64},
        versions_testees=(),
        version_slicer="2.0",
        detail=True,
    )

    controle = next(item for item in resultat["controles"] if item["nom"] == "profils")
    assert controle["statut"] == "FAIL"
    assert "erreur" in controle["detail"][1]


def test_version_dynamique_hatch_est_comparee_sans_executer_le_module(tmp_path: Path) -> None:
    """Une version littérale de Hatch est comparable au verrou ; le module source n'est jamais exécuté."""
    from benchab.qualite.installation import _controle_uv

    paquet = tmp_path / 'benchab'
    paquet.mkdir()
    source = paquet / '__init__.py'
    source.write_text('__version__ = "1.1.0"\nraise RuntimeError("ne pas exécuter")\n')
    (tmp_path / 'pyproject.toml').write_text(
        '[project]\nname="benchab"\nrequires-python=">=3.13"\ndynamic=["version"]\n'
        '[tool.hatch.version]\npath="benchab/__init__.py"\n')
    (tmp_path / 'uv.lock').write_text(
        'version=1\nrequires-python=">=3.13"\n[[package]]\nname="benchab"\nversion="1.1.0"\n')
    assert _controle_uv(tmp_path)[0]['statut'] == 'PASS'
    source.write_text('__version__ = "1.2.0"\n')
    assert _controle_uv(tmp_path)[0]['statut'] == 'FAIL'


def test_verrou_editable_uv_sans_numero_conserve_le_contrat_dynamique(tmp_path: Path) -> None:
    """uv omet normalement la version d'un paquet éditable dynamique ; ce n'est pas une corruption."""
    from benchab.qualite.installation import _controle_uv

    paquet = tmp_path / 'benchab'
    paquet.mkdir()
    (paquet / '__init__.py').write_text('__version__ = "1.1.0"\n')
    (tmp_path / 'pyproject.toml').write_text(
        '[project]\nname="benchab"\nrequires-python=">=3.13"\ndynamic=["version"]\n'
        '[tool.hatch.version]\npath="benchab/__init__.py"\n')
    (tmp_path / 'uv.lock').write_text(
        'version=1\nrequires-python=">=3.13"\n[[package]]\nname="benchab"\nsource={editable="."}\n')
    assert _controle_uv(tmp_path)[0]['statut'] == 'PASS'


def test_sonde_reconnait_bundle_anycubic_sans_espaces(monkeypatch):
    """Le nom réel du bundle macOS se lit par Info.plist sans lancer l'application."""
    import io
    import plistlib

    from benchab.fabrication import slicer_runtime
    from benchab.qualite.installation import _version_locale

    def indisponible(**_options):
        raise ValueError('Configuration absente')

    def ouvrir_plist(chemin, *_args, **_kwargs):
        if str(chemin) == '/Applications/AnycubicSlicerNext.app/Contents/Info.plist':
            return io.BytesIO(plistlib.dumps({'CFBundleShortVersionString': '2.0.0.3'}))
        raise FileNotFoundError(chemin)

    monkeypatch.setattr(slicer_runtime, 'informations', indisponible)
    with monkeypatch.context() as contexte:
        contexte.setattr(Path, 'open', ouvrir_plist)
        assert _version_locale() == '2.0.0.3'


def test_diagnostic_ne_confond_pas_prive_par_defaut_et_atelier_choisi(tmp_path: Path) -> None:
    """Un atelier explicite sous le dépôt doit être ignoré lui-même, pas seulement prive/."""
    import subprocess

    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / '.gitignore').write_text('/prive/\n')
    resultat = diagnostiquer(tmp_path, base=tmp_path / 'atelier-visible/benchab.sqlite')
    assert next(c for c in resultat['controles'] if c['nom'] == 'git_prive')['statut'] == 'FAIL'


def test_metadonnees_uv_mal_formees_donnent_un_diagnostic(tmp_path: Path) -> None:
    """Un TOML valide mais de forme inattendue produit FAIL au lieu d'un traceback."""
    from benchab.qualite.installation import _controle_uv

    (tmp_path / 'pyproject.toml').write_text('[project]\nname="benchab"\nrequires-python=">=3.13"\n')
    (tmp_path / 'uv.lock').write_text('version=1\nrequires-python=">=3.13"\npackage=["invalide"]\n')
    assert _controle_uv(tmp_path)[0]['statut'] == 'FAIL'
