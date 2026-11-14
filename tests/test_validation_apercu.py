"""Validation privée de l’aperçu avant le tranchage d’une candidate."""

import json

import pytest

from benchab.fabrication.trancher import trancher_fichier
from benchab.projets import validation_apercu
from benchab.qualite.empreintes import sha256_fichier


def _candidat(tmp_path):
    source = tmp_path / "projets" / "piece" / "candidats" / "v1" / "plaques.3mf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fixture")
    return source


def test_candidat_sans_fiche_refuse_avant_moteur(tmp_path):
    """Le chemin projet déclenche la vérification en usage impression."""
    source = _candidat(tmp_path)
    with pytest.raises(ValueError, match=r"plaques\.3mf\.apercu\.json"):
        trancher_fichier(source, tmp_path, prive=tmp_path)


def test_fiche_candidate_par_defaut(tmp_path):
    """La fiche locale s’applique à sa seule archive de candidate."""
    source = _candidat(tmp_path)
    fiche = source.with_name("plaques.3mf.apercu.json")
    fiche.write_text('{"schema": 1, "decision": "À AJUSTER", "source_sha256": "", "apercu": null}')
    assert validation_apercu.fiche_candidate(source, prive=tmp_path) == fiche
    with pytest.raises(ValueError, match="VALIDÉ"):
        trancher_fichier(source, tmp_path, prive=tmp_path)
    assert validation_apercu.fiche_candidate(tmp_path / "importe.3mf", prive=tmp_path) is None


def test_validation_refuse_peremption_apercu_et_source(tmp_path):
    """Les deux empreintes doivent correspondre à la décision déclarée."""
    source = tmp_path / "candidate.3mf"
    apercu = tmp_path / "apercu.html"
    fiche = tmp_path / "validation.json"
    source.write_bytes(b"3mf temoin")
    apercu.write_bytes(b"apercu temoin")
    fiche.write_text(json.dumps({
        "schema": 1, "decision": "VALIDÉ", "source_sha256": sha256_fichier(source),
        "apercu": {"fichier": apercu.name, "sha256": sha256_fichier(apercu)},
    }))
    assert validation_apercu.verifier(source, fiche, prive=tmp_path)["statut"] == "PASS"
    apercu.write_bytes(b"apercu modifie")
    with pytest.raises(ValueError, match="empreinte"):
        validation_apercu.verifier(source, fiche, prive=tmp_path)
    apercu.write_bytes(b"apercu temoin")
    source.write_bytes(b"3mf modifie")
    with pytest.raises(ValueError, match="empreinte"):
        validation_apercu.verifier(source, fiche, prive=tmp_path)


def test_tranchage_candidate_refuse_approbation_en_attente_avant_le_moteur(tmp_path):
    """La garde de la candidate intervient avant toute invocation native."""
    source = tmp_path / "candidate.3mf"
    source.write_bytes(b"fixture")
    fiche = tmp_path / "candidat.json"
    fiche.write_text('{"schema": 1, "decision": "EN ATTENTE", "source_sha256": "", "apercu": null}')
    with pytest.raises(ValueError, match="VALIDÉ"):
        trancher_fichier(source, tmp_path, prive=tmp_path, validation_visuelle=fiche)


def test_apercu_exact_et_decision_explicite(tmp_path, capsys):
    """L’aperçu charge le 3MF et seule une décision liée aux deux octets permet PASS."""
    from zipfile import ZipFile

    from benchab.cli import main

    source = _candidat(tmp_path)
    with ZipFile(source, "w") as archive:
        archive.writestr("3D/3dmodel.model", """
        <model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
          <resources><object id="1"><mesh>
            <vertices><vertex x="0" y="0" z="0"/><vertex x="10" y="0" z="0"/>
              <vertex x="0" y="6" z="0"/><vertex x="0" y="0" z="4"/></vertices>
            <triangles><triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/>
              <triangle v1="1" v2="2" v3="3"/><triangle v1="2" v2="0" v3="3"/></triangles>
          </mesh></object></resources>
          <build><item objectid="1"/></build>
        </model>""")
    page = source.with_suffix(".html")
    atelier = ["--prive", str(tmp_path)]
    assert main([*atelier, "init"]) == 0
    capsys.readouterr()
    commande = [*atelier, "conception", "apercu", "--"]
    assert main([*commande, "produire", str(source), "--sortie", str(page)]) == 0
    proposition = json.loads(capsys.readouterr().out)
    assert proposition["decision"] == "EN ATTENTE"
    assert "Plan ⟂ X" in page.read_text()
    assert proposition["source_sha256"] == sha256_fichier(source)
    with pytest.raises(ValueError, match="obligatoire"):
        validation_apercu.fiche_candidate(source, prive=tmp_path)

    autre = source.with_name("autre.3mf")
    autre.write_bytes(source.read_bytes() + b"\n")
    decision = [
        "--source-sha256", proposition["source_sha256"], "--apercu-sha256", proposition["apercu_sha256"],
    ]
    assert main([*commande, "decider", str(autre), str(page), "--decision", "VALIDÉ", *decision]) == 2
    assert not validation_apercu.fiche_laterale(autre).exists()

    parametres = [*commande, "decider", str(source), str(page), *decision, "--decision"]
    assert main([*parametres, "À AJUSTER"]) == 0
    with pytest.raises(ValueError, match="VALIDÉ"):
        validation_apercu.verifier_candidate(source, prive=tmp_path)
    assert main([*parametres, "VALIDÉ"]) == 0
    assert validation_apercu.verifier_candidate(source, prive=tmp_path)["statut"] == "PASS"


def test_course_candidate_refuse_avant_chargement(tmp_path, monkeypatch):
    """Un des STL sous candidats déclenche le contrôle visuel avant d’ouvrir les maillages."""
    from benchab.controles import course_vissage

    male = _candidat(tmp_path).with_suffix(".stl")
    femelle = tmp_path / "femelle.stl"
    male.touch()
    femelle.touch()
    monkeypatch.setattr(course_vissage, "charger_maillage", lambda *_: pytest.fail("chargement prématuré"))
    with pytest.raises(ValueError, match="Aperçu validé"):
        course_vissage.main([
            str(male), str(femelle), "--pas-mm", "1", "--sens", "droite", "--butee-mm", "0",
            "--phase-deg", "0", "--course-mm", "1", "--increment-mm", "0.1",
            "--zone-male-mm", "0", "1", "--zone-femelle-mm", "0", "1",
        ], prive=tmp_path)
