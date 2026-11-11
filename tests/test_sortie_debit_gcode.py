"""Publication du rapport U25 sans écrasement de source ni de preuve."""

import json
from zipfile import ZipFile

from benchab.controles import debit_gcode


def _archive(tmp_path):
    """Crée un 3MF tranché de test ; aucune impression ni profil réel."""
    source = tmp_path / "debit.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps({"filament_diameter": ["1.75"], "filament_max_volumetric_speed": ["12"]}),
        )
        archive.writestr(
            "Metadata/slice_info.config",
            '<config><plate><metadata key="index" value="1"/><filament id="1"/></plate></config>',
        )
        archive.writestr("Metadata/plate_1.gcode", "G90\nM83\nT0\nG1 X10 E0.4 F900\n")
    return source


def test_source_ne_peut_pas_servir_de_sortie(tmp_path, capsys):
    """Le code de refus ne remplace pas l’archive par son propre diagnostic."""
    source = _archive(tmp_path)
    avant = source.read_bytes()

    assert debit_gcode.main([str(source), "--sortie", str(source)]) == 2

    assert source.read_bytes() == avant
    assert "Erreur technique" in capsys.readouterr().err


def test_preuve_existante_ou_liee_ne_peut_pas_etre_ecrasee(tmp_path):
    """Une destination existante ou liée garde ses octets, même avec un verdict FAIL."""
    source = _archive(tmp_path)
    preuve = tmp_path / "precedent.json"
    preuve.write_text("preuve antérieure", encoding="utf-8")
    lien = tmp_path / "lien.json"
    lien.symlink_to(preuve)

    for cible in (preuve, lien):
        assert debit_gcode.main([str(source), "--sortie", str(cible)]) == 2
        assert preuve.read_text(encoding="utf-8") == "preuve antérieure"
    assert lien.is_symlink()


def test_sortie_neuve_garde_le_rapport_et_le_code_u25(tmp_path):
    """Le JSON sauvegardé reste égal au rapport du contrôle sans publication."""
    source = _archive(tmp_path)
    avant = source.read_bytes()
    attendu = debit_gcode.analyser(source)
    sortie = tmp_path / "rapport.json"

    assert debit_gcode.main([str(source), "--sortie", str(sortie)]) == attendu["code"]

    assert json.loads(sortie.read_bytes()) == attendu
    assert source.read_bytes() == avant


def test_sortie_publique_du_checkout_est_refusee(tmp_path, monkeypatch):
    """Même un checkout jetable ne reçoit pas le rapport dans sa partie publique."""
    source = _archive(tmp_path)
    depot = tmp_path / "depot"
    depot.mkdir()
    monkeypatch.delenv("BENCHAB_PRIVE", raising=False)
    monkeypatch.setattr("benchab.chemins.racine_depot", lambda: depot)
    sortie = depot / "rapport.json"

    assert debit_gcode.main([str(source), "--sortie", str(sortie)]) == 2
    assert not sortie.exists()


def test_resume_et_detail_preservent_le_verdict_et_la_preuve(tmp_path, capsys):
    """Les deux affichages gardent le même rapport, y compris ses erreurs."""
    source = _archive(tmp_path)
    attendu = debit_gcode.analyser(source)
    for mode in ("--resume", "--detail"):
        sortie = tmp_path / f"{mode}.json"
        assert debit_gcode.main([str(source), "--sortie", str(sortie), mode]) == attendu["code"]
        affiche = json.loads(capsys.readouterr().out)
        assert json.loads(sortie.read_bytes()) == attendu
        if mode == "--detail":
            assert affiche == attendu
        else:
            assert affiche["statut"] == attendu["statut"]
            assert affiche["rapport"] == str(sortie)
            assert affiche["plaques"][0]["plaque"] == 1
            assert affiche["plaques"][0]["erreurs"]
            assert len(json.dumps(affiche, ensure_ascii=False)) < len(json.dumps(attendu, ensure_ascii=False))


def test_modes_avec_rapport_requis_refuses_avant_lecture(tmp_path, capsys):
    """Un affichage avec rapport promis doit refuser avant l’analyse sans destination."""
    source = tmp_path / "absente.3mf"
    assert debit_gcode.main([str(source), "--resume"]) == 2
    erreur = capsys.readouterr().err
    assert "--sortie" in erreur and "absente.3mf" not in erreur
