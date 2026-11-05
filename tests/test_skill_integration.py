"""Tests du lien de skill sur des répertoires jetables, sans installation globale."""

import json

import pytest

from benchab import skill


def _source_fixture(tmp_path):
    """Crée les marqueurs requis ; entrée tmp_path, sortie racine fixture, sans unité ni effet global."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: benchab\n---\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("règles\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("guide initial\n", encoding="utf-8")
    (root / "benchab").mkdir()
    (root / "benchab" / "outil.py").write_text("VERSION = 1\n", encoding="utf-8")
    return root


def test_plan_sec_preserve_tiers_retire_liens_anciens_et_idempotence(tmp_path):
    """Vérifie plan/liens/tiers ; entrée tmp_path, sortie assertions PASS, sans unité ni écriture globale."""
    root = _source_fixture(tmp_path)
    old = tmp_path / "ancien-atelier"
    old.mkdir()
    (old / "skill").mkdir()
    (old / "skill" / "SKILL.md").write_text("ancien\n", encoding="utf-8")
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "tiers.txt").write_text("préservé\n", encoding="utf-8")
    elsewhere = tmp_path / "autre-projet"
    elsewhere.mkdir()
    (skills / "autre-projet").symlink_to(elsewhere, target_is_directory=True)
    (skills / "ancien").symlink_to(old / "skill", target_is_directory=True)
    (skills / "dangling").symlink_to(old / "absent", target_is_directory=True)
    plan = skill.planifier(root, [skills], ancien_atelier=old)
    assert plan["statut"] == "PLANIFIÉ"
    assert not (skills / "benchab").exists()
    assert (skills / "ancien").is_symlink() and (skills / "dangling").is_symlink()
    json.dumps(plan)
    resultat = skill.appliquer(plan)
    assert resultat["statut"] == "PASS"
    assert (skills / "benchab").resolve() == root.resolve()
    assert not (skills / "ancien").is_symlink()
    assert not (skills / "dangling").is_symlink()
    assert (skills / "tiers.txt").read_text() == "préservé\n"
    assert (skills / "autre-projet").is_symlink()
    assert (skills / "autre-projet").resolve() == elsewhere.resolve()
    (root / "docs" / "guide.md").write_text("guide actualisé\n", encoding="utf-8")
    (root / "benchab" / "outil.py").write_text("VERSION = 2\n", encoding="utf-8")
    assert (skills / "benchab" / "docs" / "guide.md").read_text() == "guide actualisé\n"
    assert (skills / "benchab" / "benchab" / "outil.py").read_text() == "VERSION = 2\n"
    second = skill.planifier(root, [skills], ancien_atelier=old)
    assert second["operations"] == []
    assert skill.appliquer(second)["liens_verifies"] == 1


def test_lien_benchab_historique_est_remplace_seulement_si_cible_ancien(tmp_path):
    """Teste le remplacement ciblé ; entrée tmp_path, sortie assertions PASS, sans unité ni effet global."""
    root = _source_fixture(tmp_path)
    old = tmp_path / "ancien"
    old.mkdir()
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "benchab").symlink_to(old, target_is_directory=True)
    plan = skill.planifier(root, [skills], ancien_atelier=old)
    assert [x["action"] for x in plan["operations"]] == ["retirer_lien_ancien", "creer_lien_benchab"]
    skill.appliquer(plan)
    assert (skills / "benchab").resolve() == root.resolve()


def test_collision_fichier_ou_lien_vers_autre_projet_refuse_sans_ecraser(tmp_path):
    """Teste les collisions ; entrée tmp_path, sortie assertions PASS, sans unité ni écrasement tiers."""
    root = _source_fixture(tmp_path)
    other = tmp_path / "autre-projet"
    other.mkdir()
    for kind in ("file", "link", "directory"):
        skills = tmp_path / f"skills-{kind}"
        skills.mkdir()
        target = skills / "benchab"
        if kind == "file":
            target.write_text("tiers", encoding="utf-8")
        elif kind == "directory":
            target.mkdir()
            (target / "keep.txt").write_text("tiers", encoding="utf-8")
        else:
            target.symlink_to(other, target_is_directory=True)
        with pytest.raises(FileExistsError):
            skill.planifier(root, [skills])
        if kind == "link":
            assert target.is_symlink() and target.resolve() == other.resolve()
        elif kind == "directory":
            assert (target / "keep.txt").read_text() == "tiers"
        else:
            assert target.read_text() == "tiers"


def test_changement_apres_plan_refuse_avant_creation(tmp_path):
    """Teste la course destination ; entrée tmp_path, sortie assertions PASS, sans unité ni écrasement."""
    root = _source_fixture(tmp_path)
    skills = tmp_path / "skills"
    skills.mkdir()
    plan = skill.planifier(root, [skills])
    collision = skills / "benchab"
    collision.write_text("tiers", encoding="utf-8")
    with pytest.raises(RuntimeError, match="État modifié"):
        skill.appliquer(plan)
    assert collision.read_text() == "tiers"


def test_changement_source_apres_plan_refuse(tmp_path):
    """Teste la course source ; entrée tmp_path, sortie assertions PASS, sans unité ni lien publié."""
    root = _source_fixture(tmp_path)
    skills = tmp_path / "skills"
    skills.mkdir()
    plan = skill.planifier(root, [skills])
    (root / "SKILL.md").write_text("---\nname: autre\n---\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="État modifié"):
        skill.appliquer(plan)
    assert not (skills / "benchab").exists()


def test_dossier_destination_lie_refuse_sans_toucher_cible(tmp_path):
    """Teste parent lié/traversée ; entrée tmp_path, sortie assertions PASS, sans unité ni mutation cible."""
    root = _source_fixture(tmp_path)
    real = tmp_path / "real-skills"
    real.mkdir()
    alias = tmp_path / "alias-skills"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolique"):
        skill.planifier(root, [alias])
    with pytest.raises(ValueError, match="traversée"):
        skill.planifier(root, [real / ".." / "real-skills"])
    assert list(real.iterdir()) == []


def test_source_invalide_et_destination_absente_refuse(tmp_path):
    """Teste les préconditions ; entrée tmp_path, sortie assertions PASS, sans unité ni écriture externe."""
    root = _source_fixture(tmp_path)
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: other\n---\n", encoding="utf-8")
    (bad / "AGENTS.md").write_text("rules", encoding="utf-8")
    (bad / "docs").mkdir()
    skills = tmp_path / "missing"
    with pytest.raises(ValueError, match="nom benchab"):
        skill.planifier(bad, [skills])
    with pytest.raises(FileNotFoundError):
        skill.planifier(root, [skills])


def test_cli_plan_sec_et_application_explicite(tmp_path, capsys, atelier_initialise):
    """Teste la CLI ; entrée tmp_path, sortie états PLANIFIÉ/PASS, sans unité ni lien implicite."""
    from benchab.cli import main

    root = _source_fixture(tmp_path)
    skills = tmp_path / "skills"
    skills.mkdir()
    private = tmp_path / "prive"
    args = ["--prive", str(private), "skill", "--racine", str(root), "--destination", str(skills)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["statut"] == "PLANIFIÉ"
    assert not (skills / "benchab").exists()
    assert main([*args, "--appliquer"]) == 0
    assert json.loads(capsys.readouterr().out)["statut"] == "PASS"
    assert (skills / "benchab").resolve() == root.resolve()


def test_deux_destinations_collision_seconde_preserve_premiere(tmp_path):
    """Précontrôle global des deux hôtes ; collision FAIL avant tout retrait ou lien, aucune écriture globale."""
    root = _source_fixture(tmp_path)
    a, b = tmp_path / "a-skills", tmp_path / "b-skills"
    a.mkdir()
    b.mkdir()
    (b / "benchab").write_text("tiers")
    with pytest.raises(FileExistsError):
        skill.planifier(root, [a, b])
    assert list(a.iterdir()) == []
    assert (b / "benchab").read_text() == "tiers"
    (b / "benchab").unlink()
    plan = skill.planifier(root, [a, b])
    (b / "benchab").write_text("concurrent")
    with pytest.raises(RuntimeError, match="État modifié"):
        skill.appliquer(plan)
    assert list(a.iterdir()) == []
    assert (b / "benchab").read_text() == "concurrent"
