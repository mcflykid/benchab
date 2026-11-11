"""Contrôle Ruff D10x progressif sur les changements Git du paquet public."""

from __future__ import annotations

import argparse
import ast
import json
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from benchab.processus import executer


@dataclass(frozen=True)
class Changement:
    """Décrit un fichier Python et les définitions à contrôler dans la révision HEAD."""

    chemin: str
    lignes: frozenset[int]
    verifier_module: bool


def _commande(racine, arguments, *, delai):
    """Exécute un outil borné en secondes et relit ses captures privées en octets."""
    with tempfile.TemporaryDirectory(prefix="benchab-annotations-") as temporaire:
        resultat = executer(arguments, cwd=racine, diagnostics=temporaire, delai=delai,
                            conserver_capture=True, limite_queue=8192)
        if resultat.diagnostic is None:
            raise ValueError("Capture de diagnostic absente")
        dossier = Path(resultat.diagnostic).parent
        sortie, erreur = dossier / "stdout.log", dossier / "stderr.log"
        # Le diff NUL exige les octets d'origine, pas la queue texte du lanceur (#37).
        if any(f.stat().st_size > 32 * 1024 * 1024 for f in (sortie, erreur)):
            raise ValueError("Budget de capture d'annotations dépassé (32 Mio)")
        return resultat.code_controle, sortie.read_bytes(), erreur.read_bytes()


def _git(racine: Path, *arguments: str) -> bytes:
    """Exécute Git sans shell ; sortie en octets, échec explicite sans modifier le dépôt."""
    code, sortie, erreur = _commande(racine, ["git", *arguments], delai=10)
    if code:
        raise ValueError(f"Git a échoué ({' '.join(arguments[:2])}) : {erreur.decode(errors='replace')}")
    return sortie


def _definitions(arbre):
    """Indexe les définitions par portée et AST, indépendamment de leurs numéros de ligne."""
    resultat = []

    def parcourir(noeud, contexte):
        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            contexte = (*contexte, (type(noeud).__name__, noeud.name))
            resultat.append(((contexte, ast.dump(noeud)), noeud.lineno))
        for enfant in ast.iter_child_nodes(noeud):
            parcourir(enfant, contexte)

    parcourir(arbre, ())
    return resultat


def _portees_modifiees(actuel, precedent=None):
    """Rend les lignes de définitions et le contrôle du module, sans imputer les suppressions au voisin."""
    arbre = ast.parse(actuel)
    if precedent is None:
        return frozenset(ligne for _, ligne in _definitions(arbre)), True
    ancien = ast.parse(precedent)
    historiques = Counter(cle for cle, _ in _definitions(ancien))
    lignes = set()
    # Les décorateurs font partie de l'AST, même si la ligne du def ne change pas.
    # Ruff décide ensuite de la visibilité, y compris des constructeurs D107.
    for cle, ligne in _definitions(arbre):
        if historiques[cle]:
            historiques[cle] -= 1
        else:
            lignes.add(ligne)
    suppression_doc_module = bool(ast.get_docstring(ancien)) and not ast.get_docstring(arbre)
    return frozenset(lignes), suppression_doc_module


def _revision(racine, ref):
    """Résout une révision en SHA de commit avant de l'utiliser dans le diff."""
    return _git(racine, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()


def _blobs(racine: Path, base: str, tete: str) -> list[Changement]:
    """Lit le diff NUL de benchab ; exige une base antérieure et le code exact de HEAD."""
    base, tete = _revision(racine, base), _revision(racine, tete)
    if base == tete:
        raise ValueError("La base doit précéder la révision analysée.")
    _git(racine, "merge-base", "--is-ancestor", base, tete)
    if _revision(racine, "HEAD") != tete or _git(racine, "diff", "--name-only", "-z", tete, "--", "benchab"):
        raise ValueError("La révision HEAD doit correspondre à la copie analysée.")
    champs = _git(racine, "diff", "--name-status", "-z", "-M", base, tete, "--", "benchab").split(b"\0")
    if champs and champs[-1] == b"":
        champs.pop()
    resultat: list[Changement] = []
    index = 0
    while index < len(champs):
        statut = champs[index].decode("ascii", errors="strict")
        index += 1
        if statut.startswith("R") or statut.startswith("C"):
            ancien = champs[index].decode("utf-8", errors="surrogateescape")
            chemin = champs[index + 1].decode("utf-8", errors="surrogateescape")
            index += 2
        else:
            ancien = None if statut == "A" else champs[index].decode("utf-8", errors="surrogateescape")
            chemin = champs[index].decode("utf-8", errors="surrogateescape")
            index += 1
        if not chemin.startswith("benchab/") or Path(chemin).suffix != ".py" or statut == "D":
            continue
        cible = (racine / chemin).resolve()
        if not cible.is_relative_to(racine.resolve()) or not cible.is_file():
            raise ValueError(f"Chemin de changement invalide ou absent : {chemin!r}")
        actuel = cible.read_text(encoding="utf-8")
        precedent = None if ancien is None or statut.startswith(("R", "C")) else (
            _git(racine, "show", f"{base}:{ancien}").decode("utf-8")
        )
        lignes, verifier_module = _portees_modifiees(actuel, precedent)
        resultat.append(Changement(chemin, lignes, verifier_module))
    return resultat


def controler(racine: Path, base: str, tete: str) -> list[str]:
    """Retourne les violations D10x des modules nouveaux ou portées publiques modifiées."""
    changements = _blobs(racine, base, tete)
    chemins = [c.chemin for c in changements]
    if not chemins:
        return []
    code, sortie, erreur = _commande(
        racine, [sys.executable, "-m", "ruff", "check", "--select", "D10", "--output-format", "json", *chemins],
        delai=30,
    )
    if code not in (0, 1):
        raise ValueError(f"Ruff a échoué ({code}) : {erreur.decode(errors='replace')}")
    try:
        diagnostics = json.loads(sortie)
    except json.JSONDecodeError as erreur:
        raise ValueError("Ruff n'a pas renvoyé de diagnostic JSON valide.") from erreur
    par_chemin = {(racine / c.chemin).resolve(): c for c in changements}
    violations: list[str] = []
    for diagnostic in diagnostics:
        chemin = Path(diagnostic["filename"]).resolve()
        changement = par_chemin.get(chemin)
        if changement is None:
            continue
        code = diagnostic.get("code", "")
        ligne = diagnostic.get("location", {}).get("row", 0)
        if code in {"D100", "D104"}:
            retenu = changement.verifier_module
        else:
            retenu = ligne in changement.lignes
        if retenu:
            violations.append(f"{changement.chemin}:{ligne}: {code} {diagnostic.get('message', '')}")
    return violations


def main(argv: list[str] | None = None) -> int:
    """Commande CLI : accepte base, tête et racine ; retourne 0 si conforme, 1 sinon."""
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--base", required=True, help="Révision Git de comparaison")
    parseur.add_argument("--tete", default="HEAD", help="Révision Git analysée")
    parseur.add_argument("--racine", type=Path, default=Path.cwd(), help="Racine du dépôt")
    arguments = parseur.parse_args(argv)
    try:
        violations = controler(arguments.racine.resolve(), arguments.base, arguments.tete)
    except (ValueError, SyntaxError) as erreur:
        print(f"Échec du contrôle des annotations : {erreur}", file=sys.stderr)
        return 1
    if violations:
        print("Docstrings Ruff D manquantes dans le périmètre modifié :", file=sys.stderr)
        print("\n".join(violations), file=sys.stderr)
        return 1
    print("Ruff D10x : conforme sur les nouveaux modules et portées publiques modifiées.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
