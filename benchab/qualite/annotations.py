"""Contrôle Ruff D10x progressif sur les changements Git du paquet public."""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Changement:
    """Décrit un fichier Python touché et ses lignes modifiées dans HEAD."""

    chemin: str
    ancien: str | None
    lignes: frozenset[int]
    nouveau: bool


def _git(racine: Path, *arguments: str) -> bytes:
    """Exécute Git sans shell ; entrées : racine et arguments ; sortie : octets ; échec explicite."""
    try:
        return subprocess.run(["git", *arguments], cwd=racine, check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as erreur:
        detail = getattr(erreur, "stderr", b"")
        raise ValueError(f"Git a échoué ({' '.join(arguments[:2])}) : {detail.decode(errors='replace')}") from erreur


def _blobs(racine: Path, base: str, tete: str) -> list[Changement]:
    """Lit le diff NUL de benchab ; bases et chemins invalides provoquent un échec."""
    _git(racine, "cat-file", "-e", f"{base}^{{commit}}")
    _git(racine, "cat-file", "-e", f"{tete}^{{commit}}")
    if _git(racine, "rev-parse", "HEAD").decode().strip() != _git(racine, "rev-parse", tete).decode().strip():
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
        actuel = cible.read_text(encoding="utf-8").splitlines()
        if ancien is None or statut.startswith("R"):
            lignes = frozenset(range(1, len(actuel) + 1))
        else:
            precedent = _git(racine, "show", f"{base}:{ancien}").decode("utf-8").splitlines()
            diff = difflib.SequenceMatcher(a=precedent, b=actuel, autojunk=False)
            touchees: set[int] = set()
            for statut_diff, _, _, debut, fin in diff.get_opcodes():
                if statut_diff == "equal":
                    continue
                if fin > debut:
                    touchees.update(range(debut + 1, fin + 1))
                elif actuel:
                    # Une suppression seule ancre le contrôle à la ligne voisine conservée.
                    touchees.add(max(1, min(debut, len(actuel))))
            lignes = frozenset(touchees)
        resultat.append(Changement(chemin, ancien, lignes, ancien is None or statut.startswith("R")))
    return resultat


def _portees_publiques(source: str, lignes: frozenset[int]) -> set[int]:
    """Retourne les lignes de définition publiques touchées selon leurs corps AST."""
    arbre = ast.parse(source)
    portees: list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef] = []
    portees.extend(
        n
        for n in ast.walk(arbre)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not n.name.startswith("_")
    )
    return {n.lineno for n in portees if any(n.lineno <= ligne <= (n.end_lineno or n.lineno) for ligne in lignes)}


def controler(racine: Path, base: str, tete: str) -> list[str]:
    """Retourne les violations D10x des modules nouveaux ou portées publiques modifiées."""
    changements = _blobs(racine, base, tete)
    chemins = [c.chemin for c in changements]
    if not chemins:
        return []
    try:
        resultat = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--select", "D10", "--output-format", "json", *chemins],
            cwd=racine,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as erreur:
        raise ValueError(f"Ruff est indisponible : {erreur}") from erreur
    if resultat.returncode not in (0, 1):
        raise ValueError(f"Ruff a échoué ({resultat.returncode}) : {resultat.stderr}")
    try:
        diagnostics = json.loads(resultat.stdout)
    except json.JSONDecodeError as erreur:
        raise ValueError("Ruff n'a pas renvoyé de diagnostic JSON valide.") from erreur
    par_chemin = {racine / c.chemin: c for c in changements}
    violations: list[str] = []
    for diagnostic in diagnostics:
        chemin = Path(diagnostic["filename"]).resolve()
        changement = next((c for p, c in par_chemin.items() if p.resolve() == chemin), None)
        if changement is None:
            continue
        code = diagnostic.get("code", "")
        ligne = diagnostic.get("location", {}).get("row", 0)
        source = (racine / changement.chemin).read_text(encoding="utf-8")
        debut_module = changement.nouveau and code == "D100"
        portees = _portees_publiques(source, changement.lignes)
        if (debut_module and ligne == 1) or ligne in portees:
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
