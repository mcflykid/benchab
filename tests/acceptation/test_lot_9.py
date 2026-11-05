"""Réception du skill sur deux destinations jetables ; aucune installation globale."""

from pathlib import Path

from benchab import skill


def test_bascule_deux_destinations_garde_tiers_et_charge_skill(tmp_path):
    """Vérifie les deux liens et leur SKILL.md, les seuls retraits autorisés et le plan idempotent."""
    depot = Path(__file__).resolve().parents[2]
    ancien = tmp_path / "ancien-atelier"
    ancien.mkdir()
    ancienne_cible = ancien / "outil"
    ancienne_cible.mkdir()
    contenu = b"Marqueur de la fixture\n"
    (ancienne_cible / "marqueur.txt").write_bytes(contenu)
    tiers = tmp_path / "outil-tiers"
    tiers.mkdir()
    destinations = [tmp_path / nom / "skills" for nom in ("agent-a", "agent-b")]
    for dossier in destinations:
        dossier.mkdir(parents=True)
        (dossier / "ancien").symlink_to(ancienne_cible)
        (dossier / "tiers").symlink_to(tiers)
        (dossier / "garder.txt").write_bytes(contenu)

    plan = skill.planifier(depot, destinations, ancien_atelier=ancien)
    assert plan["statut"] == "PLANIFIÉ"
    assert len(plan["operations"]) == 4
    assert all((dossier / "ancien").is_symlink() and not (dossier / "benchab").exists() for dossier in destinations)
    assert skill.appliquer(plan)["liens_verifies"] == 2
    for dossier in destinations:
        lien = dossier / "benchab"
        assert lien.is_symlink() and lien.resolve(strict=True) == depot
        assert (lien / "SKILL.md").read_bytes() == (depot / "SKILL.md").read_bytes()
        assert b"name: benchab\n" in (lien / "SKILL.md").read_bytes()
        assert not (dossier / "ancien").is_symlink()
        assert (dossier / "tiers").resolve(strict=True) == tiers
        assert (dossier / "garder.txt").read_bytes() == contenu
    assert (ancienne_cible / "marqueur.txt").read_bytes() == contenu
    assert skill.planifier(depot, destinations, ancien_atelier=ancien)["operations"] == []
