"""Garde des lancements et dépassements de délai aux frontières de BENCHAB."""

import ast
import sys
from collections import Counter
from pathlib import Path

import trimesh

from benchab.conception import capacites_locales
from benchab.controles import controles_piece

# Les exceptions gardent leur contrat spécifique. Les deux exécutions de
# diagnostics déjà bornées et l'adaptateur historique sont hors migration #59.
# Voir docs/reference/controles.md et docs/reference/slicer-anycubic.md.
_EXCEPTIONS = {
    ("visualisation/serveur.py", "ouvrir", "run"): "Remise de l’URL au navigateur macOS (Safari ou par défaut).",
    ("visualisation/serveur.py", "main", "Popen"): "Serveur détaché, survit volontairement à la CLI.",
    ("fabrication/trancher.py", "_executer", "Popen"): "Groupe slicer, verrou, délai et rapports de crash propres.",
    ("fabrication/trancher.py", "demander_ouverture", "Popen"): "Superviseur graphique avec accusé borné.",
    ("fabrication/trancher.py", "_visualiser", "Popen"): "Durée de vie de fenêtre, verrou jusqu’à sa fermeture.",
    ("qualite/parcours_safari.py", "parcours", "Popen"): "Session WebDriver avec arrêt explicite dans finally.",
    ("qualite/qualite.py", "executer_borne", "run"): "Adaptateur historique CompletedProcess avec timeout.",
    ("organisation/automatisation.py", "environnement", "run"): "Sonde Python bornée à 20 s.",
    ("connaissances/memoire.py", "_empreinte_blob_git", "Popen"): "Hachage en flux avec sélecteur, délai et plafond.",
    ("imports3d/maillage_avance.py", "moteur", "run"): "Moteur natif et répertoire jetable déjà bornés.",
}


def test_lancements_directs_limites_aux_exceptions_documentees():
    """Refuse toute nouvelle exécution directe de run/Popen hors du lanceur commun."""
    racine = Path(__file__).resolve().parents[1] / "benchab"
    appels = Counter()
    for fichier in racine.rglob("*.py"):
        if fichier == racine / "processus.py":
            continue
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        noms = {"subprocess.run": "run", "subprocess.Popen": "Popen"}
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.ImportFrom) and noeud.module == "subprocess":
                noms.update({a.asname or a.name: a.name for a in noeud.names if a.name in {"run", "Popen"}})
            if isinstance(noeud, ast.Import):
                for alias in noeud.names:
                    if alias.name == "subprocess" and alias.asname:
                        noms.update({f"{alias.asname}.{m}": m for m in ("run", "Popen")})
            for enfant in ast.iter_child_nodes(noeud):
                enfant.parent = noeud
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.Call) or (methode := noms.get(ast.unparse(noeud.func))) is None:
                continue
            contexte = noeud.parent
            while not isinstance(contexte, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)):
                contexte = contexte.parent
            fonction = getattr(contexte, "name", "<module>")
            appels[(fichier.relative_to(racine).as_posix(), fonction, methode)] += 1
    assert appels == Counter({cle: 1 for cle in _EXCEPTIONS})


def test_delai_couches_courtes_non_verifie(tmp_path, monkeypatch):
    """Une analyse de couches bloquée ne donne jamais PASS, même sans îlot observé."""
    piece = tmp_path / "cube.stl"
    trimesh.creation.box().export(piece)
    script = tmp_path / "bloque.py"
    script.write_text("import time; time.sleep(10)", encoding="utf-8")
    monkeypatch.setattr(controles_piece, "CC", script)
    monkeypatch.setattr(controles_piece, "DELAI_COUCHES_S", 0.05)
    rapport = controles_piece.controler(piece)
    couches = next(c for c in rapport if c["controle"] == "couches courtes")
    assert couches["statut"] == "NON VÉRIFIÉ" and "DELAI" in couches["detail"]


def test_delai_sonde_python_non_verifie(monkeypatch):
    """Une sonde bloquée reste distincte d'une dépendance absente."""
    original = capacites_locales.processus.executer
    commandes = []

    def dormir(cmd, **options):
        commandes.append((cmd, options["delai"]))
        return original([*cmd[:2], "-c", "import time; time.sleep(10)"], **dict(options, delai=0.05))

    monkeypatch.setattr(capacites_locales.processus, "executer", dormir)
    rapport = capacites_locales.evaluer(
        {"fixture": ("Sonde", sys.executable, ["json"], [], "benchab/__init__.py")}
    )["fixture"]
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["versions_observees"] == {"json": None}
    assert all(cmd[:2] == [sys.executable, "-B"] for cmd, _ in commandes)
    assert [d for _, d in commandes] == [60, 15]
