"""Garde contre un second décodeur pour le membre des réglages projet."""

import ast
from pathlib import Path


def _contient_membre(expr, noms):
    return any(
        (isinstance(n, ast.Constant) and n.value == "Metadata/project_settings.config")
        or (isinstance(n, ast.Name) and n.id in noms)
        for n in ast.walk(expr)
    )


def _propager(noeuds, noms=()):
    noms = set(noms)
    while True:
        marques = {
            cible.id
            for n in noeuds
            if isinstance(n, ast.Assign) and _contient_membre(n.value, noms)
            for cible in n.targets
            if isinstance(cible, ast.Name)
        }
        if marques <= noms:
            return noms
        noms.update(marques)


def test_pas_de_json_loads_direct_sur_le_membre_projet():
    """Détecte les lectures JSON directes, y compris via une variable intermédiaire."""
    racine = Path(__file__).resolve().parents[1]
    fautes = []
    for fichier in (racine / "benchab").rglob("*.py"):
        if fichier == racine / "benchab/reglages.py":
            continue
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        globaux = _propager(arbre.body)
        decodeurs = {"json.loads", "json.load"}
        for n in arbre.body:
            if isinstance(n, ast.ImportFrom) and n.module == "json":
                decodeurs.update(a.asname or a.name for a in n.names if a.name in {"loads", "load"})
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name == "json" and a.asname:
                        decodeurs.update({f"{a.asname}.loads", f"{a.asname}.load"})
        scopes = [arbre.body] + [
            tuple(ast.walk(n))
            for n in ast.walk(arbre)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for scope in scopes:
            noms = _propager(scope, globaux)
            for appel in scope:
                if not isinstance(appel, ast.Call) or ast.unparse(appel.func) not in decodeurs:
                    continue
                if any(_contient_membre(arg, noms) for arg in appel.args):
                    fautes.append(f"{fichier.relative_to(racine)}:{appel.lineno}")
    assert not fautes, "Utiliser le décodeur commun des réglages 3MF : " + ", ".join(fautes)
