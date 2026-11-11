"""Géométries synthétiques ; aucune fiche de projet active."""

import json

import pytest

from benchab.qualite import qualite as Q


def fixture(base):
    """Deux pièces génériques : une protégée, une à fabriquer."""
    import hashlib
    import zipfile

    import trimesh

    c = base / "candidats"
    c.mkdir()
    models = {}
    for name in ("Reference", "Piece"):
        mesh = trimesh.creation.box((10, 10, 4))
        mesh.apply_translation(-mesh.bounds[0])
        path = c / (name + ".stl")
        mesh.export(path)
        models[name] = {
            "stl_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "print_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    (c / "assemblage.json").write_text(
        json.dumps({"models": models, "parts": [{"name": n, "model": n} for n in models]})
    )
    with zipfile.ZipFile(c / "plaques.3mf", "w") as z:
        z.writestr(
            "Metadata/model_settings.config",
            '<config><object id="1"><metadata key="name" value="Piece"/></object><plate>'
            '<metadata key="plater_name" value="Plaque"/><model_instance>'
            '<metadata key="object_id" value="1"/></model_instance></plate></config>',
        )
    h = models["Reference"]["stl_sha256"]
    p = base / "qualite.json"
    p.write_text(
        json.dumps(
            {
                "manifeste": "candidats/assemblage.json",
                "stl_assemblage": "candidats",
                "stl_impression": "candidats",
                "plaques_3mf": "candidats/plaques.3mf",
                "protegees": {
                    "Reference": {"sha256_assemblage": h, "sha256_impression": h, "instances": ["Reference"]}
                },
            }
        )
    )
    return p


def test_niveau1_protection_empreintes_et_instances(tmp_path):
    cfg_path = fixture(tmp_path)
    assert Q.niveau1(cfg_path) != Q.KO
    cfg = json.loads(cfg_path.read_text())
    modele = next(iter(cfg["protegees"]))
    autre = tmp_path / ".qualite-test.json"
    falsifie = json.loads(cfg_path.read_text())
    falsifie["protegees"][modele]["sha256_impression"] = "0" * 64
    autre.write_text(json.dumps(falsifie))
    assert Q.niveau1(autre) == Q.KO
    manquant = json.loads(cfg_path.read_text())
    manquant["protegees"][modele]["instances"].append("Instance_inexistante")
    autre.write_text(json.dumps(manquant))
    assert Q.niveau1(autre) == Q.KO
    assert Q.niveau1(cfg_path) != Q.KO


def test_rapport_qualite_publie_en_groupe_sans_recrire_ni_ecraser(tmp_path, capsys):
    """Un rejeu identique ne touche pas aux preuves et un résultat différent ne les remplace pas."""
    cfg = tmp_path / "qualite.json"
    cfg.write_text("{}")
    entree = tmp_path / "source.stl"
    entree.write_bytes("entrée".encode())
    rapport = Q.Rapport("niveau3", cfg)
    rapport.ajouter_entrees(entree)
    rapport.add("contrôle", Q.OK)
    suppl = {"plaques.json": "[]\n"}
    assert rapport.ecrire(suppl) == Q.OK
    cible = tmp_path / "preuves/qualite/niveau3.json"
    cible_tableau = cible.parent / "plaques.json"
    sorties = (cible, cible.with_suffix(".md"), cible_tableau)
    avant = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in sorties}

    rejeu = Q.Rapport("niveau3", cfg)
    rejeu.ajouter_entrees(entree)
    rejeu.add("contrôle", Q.OK)
    assert rejeu.ecrire(suppl) == Q.OK
    assert {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in avant} == avant

    different = Q.Rapport("niveau3", cfg)
    different.ajouter_entrees(entree)
    different.add("contrôle", Q.KO, "nouveau résultat")
    assert different.ecrire({"plaques.json": "[]\n"}) == Q.KO
    assert "nouvelle preuve" in capsys.readouterr().out
    assert {p: p.read_bytes() for p in avant} == {p: v[0] for p, v in avant.items()}
    archives = list(cible.parent.glob("rejeu-*"))
    assert len(archives) == 1
    copie = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in archives[0].iterdir()}
    assert different.ecrire(suppl) == Q.KO
    assert list(cible.parent.glob("rejeu-*")) == archives
    assert {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in copie} == copie


def test_rapport_qualite_preserve_un_rejeu_incomplet(tmp_path):
    """Un groupe incomplet à la même empreinte reste intact et reçoit un refus explicite."""
    cfg = tmp_path / "qualite.json"
    cfg.write_text("{}")
    premier = Q.Rapport("niveau1", cfg)
    assert premier.ecrire() == Q.OK
    divergent = Q.Rapport("niveau1", cfg)
    divergent.add("contrôle", Q.KO, "nouveau résultat")
    assert divergent.ecrire() == Q.KO
    groupe, = (tmp_path / "preuves/qualite").glob("rejeu-*")
    restant = groupe / "niveau1.md"
    avant = restant.read_bytes()
    (groupe / "niveau1.json").unlink()
    with pytest.raises(ValueError, match="Preuve de rejeu incomplète"):
        divergent.ecrire()
    assert list(groupe.iterdir()) == [restant]
    assert restant.read_bytes() == avant


def test_rapport_qualite_ne_rattache_pas_un_tableau_orphelin(tmp_path):
    """Un niveau 3 arrêté avant le tableau ne complète pas une ancienne preuve orpheline."""
    cfg = tmp_path / "qualite.json"
    cfg.write_text("{}")
    historique = tmp_path / "preuves/qualite"
    historique.mkdir(parents=True)
    plaques = historique / "plaques.json"
    plaques.write_text("[123]")
    rapport = Q.Rapport("niveau3", cfg)
    rapport.add("fichier de tranchage absent", Q.KO)

    assert rapport.ecrire() == Q.KO
    assert not (historique / "niveau3.json").exists()
    assert plaques.read_text() == "[123]"
    rejeux = list(historique.glob("rejeu-*"))
    assert len(rejeux) == 1
    assert {p.name for p in rejeux[0].iterdir()} == {"niveau3.json", "niveau3.md"}


def test_rapport_qualite_refuse_lien_et_collision_entree(tmp_path):
    """Un rapport lié ou alias d'une entrée est refusé avant publication."""
    cfg = tmp_path / "qualite.json"
    cfg.write_text("{}")
    rapport = Q.Rapport("niveau1", cfg)
    cible = tmp_path / "preuves/qualite/niveau1.json"
    cible.parent.mkdir(parents=True)
    cible.symlink_to(cfg)
    with pytest.raises(ValueError):
        rapport.ecrire()

    cible.unlink()
    rapport.ajouter_entrees(cible)
    with pytest.raises(ValueError, match="Collision"):
        rapport.ecrire()


def test_rapport_qualite_refuse_preparation_figee(tmp_path):
    """Une préparation Pnn ne reçoit jamais les rapports mutables de qualité."""
    projet = tmp_path / "projet"
    (projet / "preparations/P01").mkdir(parents=True)
    (projet / "qualite.json").write_text("{}")
    cfg = projet / "preparations/P01/qualite.json"
    cfg.write_text("{}")
    rapport = Q.Rapport("niveau3", cfg)
    with pytest.raises(ValueError, match="Préparation figée"):
        rapport.ecrire({"plaques.json": "[]\n"})
    assert not (cfg.parent / "preuves").exists()


def test_niveau3_preserve_le_tableau_plaques_au_rejeu(tmp_path, monkeypatch):
    """Le tableau plaques appartient au même groupe que JSON/Markdown et son rejeu est sans écriture."""
    from zipfile import ZipFile

    projet = tmp_path
    tranche = projet / "tranche.3mf"
    with ZipFile(tranche, "w") as archive:
        archive.writestr(
            "Metadata/slice_info.config",
            '<config><plate><metadata key="index" value="1"/><metadata key="prediction" value="3600"/>'
            '<metadata key="weight" value="2"/></plate></config>',
        )
        archive.writestr("Metadata/plate_1.gcode", "G1 X1")
        archive.writestr(
            "Metadata/model_settings.config",
            '<config><plate><metadata key="plater_name" value="Fixture"/></plate></config>',
        )
        archive.writestr("Metadata/project_settings.config", "{}")
    plaques = projet / "plaques.3mf"
    plaques.write_bytes(tranche.read_bytes())
    placement = projet / "placement.json"
    placement.write_text('{"objects": []}')
    config = projet / "qualite.json"
    config.write_text(json.dumps({
        "tranche_3mf": tranche.name,
        "plaques_3mf": plaques.name,
        "placement": placement.name,
    }))
    monkeypatch.setattr("benchab.controles.routine.controler", lambda *_: {"conforme": True, "statut": "PASS"})

    assert Q.niveau3(config) == Q.NV
    rapports = [projet / "preuves/qualite" / nom for nom in ("niveau3.json", "niveau3.md", "plaques.json")]
    initial = {p: (p.read_bytes(), p.stat().st_ino) for p in rapports}
    assert Q.niveau3(config) == Q.NV
    assert {p: (p.read_bytes(), p.stat().st_ino) for p in rapports} == initial


def test_cli_qualite_retourne_code_2_sur_refus_publication(monkeypatch, capsys):
    """Un refus de publication est une erreur CLI propre, sans traceback."""
    monkeypatch.setattr(Q, "niveau1", lambda *_: (_ for _ in ()).throw(ValueError("sortie liée")))
    assert Q.main(["niveau1", "qualite.json"]) == 2
    assert "sortie liée" in capsys.readouterr().err


@pytest.mark.parametrize(
    "objet,attendu",
    [
        ({"name": "Vis_x", "accroche": {"assise_mm2": 30, "elancement": 5}, "reglages": {"brim_type": "no_brim"}}, 8),
        (
            {
                "name": "Vis_x",
                "accroche": {"assise_mm2": 30, "elancement": 5},
                "reglages": {"brim_type": "outer_only", "brim_width": "8"},
            },
            4,
        ),
        (
            {
                "name": "Plaque",
                "accroche": {"assise_mm2": 5000, "elancement": 0.1},
                "reglages": {"brim_type": "no_brim"},
            },
            0,
        ),
    ],
)
def test_score_accroche_suit_ses_regles(objet, attendu):
    score, _ = Q.score_accroche(objet, {"familles_echecs": ["Vis_"]})
    assert score == attendu
