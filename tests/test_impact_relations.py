"""L'impact conserve les relations documentaires et déclarées, sans réduire la couverture des tests."""

import json

from benchab.qualite.impact import impact, planifier


def test_relations_documents_make_et_declarations_transitives(tmp_path):
    """Une source modifiée impacte son guide puis ses dépendants ; chemins sans unité, aucune commande exécutée."""
    (tmp_path / "source.md").write_text("Source technique")
    (tmp_path / "guide.md").write_text("[Source](source.md)")
    (tmp_path / "synthese.md").write_text("Synthèse")
    (tmp_path / "relations.json").write_text(json.dumps({"dependances": {"guide.md": ["synthese.md"]}}))
    (tmp_path / "src").mkdir()
    (tmp_path / "src/outil.py").write_text("valeur = 1\n")
    (tmp_path / "Makefile").write_text("controle:\n\t$(PY) src/outil.py\n")
    rapport = impact(tmp_path, "source.md")
    assert rapport["dependants"] == ["guide.md", "synthese.md"]
    assert rapport["relations_directes"] == [{"fichier": "guide.md", "nature": ["documentaire"]}]
    assert impact(tmp_path, "src/outil.py")["dependants"] == ["Makefile"]
    assert impact(tmp_path, "guide.md")["relations_directes"] == [{"fichier": "synthese.md", "nature": ["déclarée"]}]


def test_impact_ne_reduit_pas_les_tests_et_inclut_references_or(tmp_path):
    """Toute retouche de fabrication garde la suite complète et les références fixes ; aucun PASS d'indépendance."""
    (tmp_path / "benchab/fabrication").mkdir(parents=True)
    source = tmp_path / "benchab/fabrication/outil.py"
    source.write_text("valeur = 1\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_references_or.py").write_text("def test_fixe(): pass\n")
    (tmp_path / "tests/test_autre.py").write_text("def test_autre(): pass\n")
    plan = planifier(tmp_path, ["benchab/fabrication/outil.py"])
    assert plan["mode"] == "COMPLET"
    assert plan["tests"] == ["tests/test_autre.py", "tests/test_references_or.py"]


def test_planifier_reconnait_les_acceptations_nommees_par_domaine(tmp_path):
    """Un test d’acceptation renommé reste sélectionnable comme cible connue."""
    chemin = "tests/acceptation/test_acceptation_schema_relationnel.py"
    cible = tmp_path / chemin
    cible.parent.mkdir(parents=True)
    cible.write_text("def test_schema(): pass\n")
    plan = planifier(tmp_path, [chemin])
    assert plan["mode"] == "CIBLE"
    assert plan["tests"] == [chemin]


def test_lot_de_sources_nanalyse_le_graphe_quune_fois(tmp_path, monkeypatch):
    """Deux changements partagent les lectures et gardent les dépendants transitifs."""
    from collections import Counter
    from pathlib import Path

    (tmp_path / "source.py").write_text("valeur = 1\n")
    (tmp_path / "relais.py").write_text("import source\n")
    (tmp_path / "test_relais.py").write_text("import relais\n")
    lectures = Counter()
    lire = Path.read_text

    def compter(chemin, *args, **kwargs):
        if chemin.is_relative_to(tmp_path):
            lectures[chemin.name] += 1
        return lire(chemin, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", compter)
    plan = planifier(tmp_path, ["source.py", "relais.py"])
    assert plan["mode"] == "COMPLET"
    assert plan["tests"] == ["test_relais.py"]
    assert [x["dependants"] for x in plan["impacts"]] == [
        ["relais.py", "test_relais.py"], ["test_relais.py"],
    ]
    assert dict(lectures) == {"source.py": 1, "relais.py": 1, "test_relais.py": 1}
