"""Impact documentaire et Python ; couverture partielle, repli conservateur."""

import ast
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from benchab.chemins import atelier_prive
from benchab.qualite.edition import cibles_markdown
from benchab.qualite.publication import relatif

EXCLUS = {"prive", ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist"}


def fichiers(root):
    """Énumère les chemins Python réguliers du dépôt hors dossiers exclus et liens symboliques."""
    root = Path(root).resolve()
    for dossier, dirs, noms in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs if d not in EXCLUS and not d.startswith(".venv") and not (Path(dossier) / d).is_symlink()
        )
        for nom in sorted(noms):
            p = Path(dossier) / nom
            if p.suffix == ".py" and not p.is_symlink():
                yield p


def impact(root, source):
    """Relève les dépendants Python, documents, Make et relations déclarées ; chemins sans unité, aucune exécution.

    La couverture reste partielle ; une absence de lien ne constitue jamais PASS sur l'indépendance.
    """
    root = Path(root).resolve()
    return _rapport_impact(source, _graphe(root, fichiers(root)))


def _graphe(root, fichiers_python):
    """Relève les relations Python et documentaires pour toutes les sources du lot."""
    edges, dynamiques, preuves = {}, set(), {}

    def ajouter(cible, dependant, nature):
        cible, dependant = Path(cible).resolve(), Path(dependant).resolve()
        if (
            not (
                cible.is_relative_to(root)
                and dependant.is_relative_to(root)
                and cible.is_file()
                and dependant.is_file()
            )
            or cible == dependant
        ):
            return
        a, b = cible.relative_to(root).as_posix(), dependant.relative_to(root).as_posix()
        edges.setdefault(a, set()).add(b)
        preuves.setdefault((a, b), set()).add(nature)

    for p in fichiers_python:
        rel = p.relative_to(root).as_posix()
        try:
            tree = ast.parse(p.read_text())
        except (SyntaxError, UnicodeError):
            dynamiques.add(rel)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                getattr(node.func, "id", "") in {"__import__", "eval", "exec"}
                or getattr(node.func, "attr", "") in {"import_module", "spec_from_file_location"}
            ):
                dynamiques.add(rel)
            names = []
            if isinstance(node, ast.Import):
                names = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module, *(node.module + "." + n.name for n in node.names)]
                if node.level:
                    # Les imports relatifs restent signalés comme couverture incomplète.
                    dynamiques.add(rel)
            for name in names:
                part = Path(*name.split("."))
                for base in (root, p.parent):
                    for cible in (base / part.with_suffix(".py"), base / part / "__init__.py"):
                        if cible.is_file() and cible.is_relative_to(root):
                            ajouter(cible, p, "certaine")
    # La reprise conserve aussi les relations non Python de l'ancien atelier.
    # Le repli complet reste obligatoire (docs/guides/developpement.md).
    _relations_non_python(root, ajouter, dynamiques)
    return edges, dynamiques, preuves


def _rapport_impact(source, graphe):
    edges, dynamiques, preuves = graphe
    vus, pile = {source}, [source]
    while pile:
        for nom in edges.get(pile.pop(), set()):
            if nom not in vus:
                vus.add(nom)
                pile.append(nom)
    return {
        "entree": source,
        "dependants": sorted(vus - {source}),
        "relations_directes": [
            {"fichier": n, "nature": sorted(preuves[(source, n)])} for n in sorted(edges.get(source, []))
        ],
        "imports_dynamiques_non_resolus": sorted(dynamiques),
        "couverture": "partielle ; ne pas réduire les tests sur une absence de relation",
    }


def _relations_non_python(root, ajouter, dynamiques):
    """Lit les liens locaux et déclarations, sans interpréter les commandes ni suivre de liens sortants."""
    for dossier, dirs, noms in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs if d not in EXCLUS and not d.startswith(".venv") and not (Path(dossier) / d).is_symlink()
        )
        for nom in sorted(noms):
            p = Path(dossier) / nom
            if p.is_symlink() or (p.suffix not in {".md", ".json"} and nom != "Makefile"):
                continue
            rel = p.relative_to(root).as_posix()
            try:
                if p.stat().st_size > 4 * 1024**2:
                    dynamiques.add(rel)
                    continue
                texte = p.read_text(encoding="utf-8")
                if p.suffix == ".md":
                    for cible in cibles_markdown(texte):
                        u = urlsplit(cible.strip("<>"))
                        if not u.scheme and not u.netloc and u.path:
                            ajouter(p.parent / unquote(u.path), p, "documentaire")
                elif nom == "Makefile":
                    for ligne in texte.splitlines():
                        if ligne.lstrip().startswith("#"):
                            continue
                        for m in re.finditer(
                            r"(?<![\w./-])(?:\$\((RACINE|ROOT)\)/)?"
                            r"((?:benchab|outils|src|scripts)/[\w./-]+\.(?:py|sh))",
                            ligne,
                        ):
                            ajouter((root if m.group(1) else p.parent) / m.group(2), p, "certaine")
                        for module in re.findall(r"(?<!\S)-m\s+(benchab\.[\w.]+)", ligne):
                            ajouter(root / Path(*module.split(".")).with_suffix(".py"), p, "certaine")
                else:
                    objet = json.loads(texte)
                    declares = objet.get("dependances", {}) if isinstance(objet, dict) else {}
                    if not isinstance(declares, dict):
                        raise ValueError("Dépendances déclarées invalides")
                    for origine, cibles in declares.items():
                        if not isinstance(origine, str) or not isinstance(cibles, list):
                            raise ValueError("Dépendances déclarées invalides")
                        for cible in cibles:
                            if not isinstance(cible, str):
                                raise ValueError("Chemin déclaré invalide")
                            ajouter(root / origine, root / cible, "déclarée")
            except (OSError, UnicodeError, ValueError):
                dynamiques.add(rel)


def planifier(root, changements):
    """Prépare les tests et impacts d’un lot, sans exécuter ni modifier les sources.

    Les changements de code gardent le mode COMPLET ; un lot de tests seuls
    peut être CIBLE. Un graphe partiel ne prouve jamais l'indépendance.
    """
    root = Path(root).resolve()
    chemins = [relatif(root, p).relative_to(root).as_posix() for p in changements]
    python = list(fichiers(root))
    tests = sorted(p.relative_to(root).as_posix() for p in python if p.name.startswith("test_"))
    direct = bool(chemins) and all(p in tests for p in chemins)
    sources = [p for p in chemins if p not in tests and (root / p).is_file()]
    # Une analyse commune explique le lot sans rescanner ni réduire la suite (#34, U64).
    graphe = _graphe(root, python) if sources else None
    return {
        "mode": "CIBLE" if direct else "COMPLET",
        "raison": "tests modifiés explicitement connus" if direct else "couverture partielle : repli conservateur",
        "tests": sorted(set(chemins)) if direct else tests,
        "impacts": [_rapport_impact(p, graphe) for p in sources],
    }


def executer(root, plan):
    """Exécute les tests sélectionnés et publie le diagnostic privé.

    Renvoie le statut, le code de sortie exact et le chemin du diagnostic avec sa durée en secondes.
    Aucun atelier n’est initialisé implicitement.
    """
    from benchab.processus import executer as processus

    if not plan["tests"]:
        return {**plan, "statut": "FAIL", "erreur": "aucun test exécutable découvert"}
    resultat = processus(
        [sys.executable, "-B", "-m", "pytest", "-q", *plan["tests"]],
        cwd=root,
        delai=600,
        diagnostics=atelier_prive() / "diagnostics/tests",
    )
    return {
        **plan,
        "statut": "PASS" if resultat.ok else "FAIL",
        "code": resultat.returncode,
        "diagnostic": resultat.diagnostic,
    }
