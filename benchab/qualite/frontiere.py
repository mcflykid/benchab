"""Frontière et interpréteur : remplace les vérifications dispersées de l'ancien atelier."""

import ast
import codecs
import os
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

EXCLUS = {"prive", ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build"}
CHEMIN_PERSONNEL = re.compile(r"/Users/[A-Za-z0-9_.-]+/")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
JETON = re.compile(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{20,}\b")
IDENTIFIANT = re.compile(
    r"""(?i)(?<![\w])['"]?(?:printer_id|printerId|device_id|account_id|user_id|serial_number)['"]?"""
    r"""\s*[:=]\s*(?:"[^"\n]+"|'[^'\n]+'|[0-9]+)"""
)
IDENTIFIANT_PYTHON = IDENTIFIANT
IDENTIFIANT = re.compile(
    r"(?i)(?<![\w])['\"]?(?:printer_id|printerId|device_id|account_id|user_id|serial_number)['\"]?"
    r"\s*[:=]\s*(?:\"[^\"\n]+\"|'[^'\n]+'|[A-Za-z0-9_-]+)"
)
TEXTES = {".py", ".md", ".toml", ".json", ".jsonl", ".yaml", ".yml", ".sh", ".sql", ".txt", ".lock"}
MAX_PUBLIC_ZIP_BYTES = 128 * 1024**2
MAX_PUBLIC_ZIP_MEMBERS = 10_000
MAX_PUBLIC_ZIP_MEMBER = 32 * 1024**2
MAX_PUBLIC_ZIP_TOTAL = 256 * 1024**2
MAX_PUBLIC_ZIP_RATIO = 500
MAX_PUBLIC_ZIP_NAME = 4096
MAX_PATTERN_OVERLAP = 64 * 1024
# La frontière doit échouer fermé sur une archive démesurée (docs/decisions/2026-11-04-frontiere-public-prive.md).


def _fenetre_motif(expressions):
    longueur = max((len(motif.pattern) for motif in expressions), default=1)
    if longueur > MAX_PATTERN_OVERLAP:
        raise ValueError("Motif trop long pour un contrôle borné")
    return longueur


def _contient_flux(flux, expressions):
    """Cherche des motifs sans charger le contenu ; mémoire bornée, booléen seulement."""
    recouvrement = _fenetre_motif(expressions)
    decodeur = codecs.getincrementaldecoder("utf-8")("ignore")
    queue = ""
    while bloc := flux.read(64 * 1024):
        texte = queue + decodeur.decode(bloc)
        if any(motif.search(texte) for motif in expressions):
            return True
        queue = texte[-recouvrement:] if recouvrement else ""
    texte = queue + decodeur.decode(b"", final=True)
    return any(motif.search(texte) for motif in expressions)


def _inspecter_zip(chemin, relatif, expressions, erreurs):
    """Inspecte noms et contenus décompressés sous budgets ; signale uniquement le chemin public."""
    try:
        taille_archive = chemin.stat().st_size
        if taille_archive > MAX_PUBLIC_ZIP_BYTES:
            erreurs.append(f"{relatif} : archive ZIP hors budget d’inspection")
            return
        with zipfile.ZipFile(chemin) as archive:
            membres = archive.infolist()
            if len(membres) > MAX_PUBLIC_ZIP_MEMBERS:
                erreurs.append(f"{relatif} : archive ZIP hors budget d’inspection")
                return
            total_annonce = 0
            total_lu = 0
            for membre in membres:
                if len(membre.filename) > MAX_PUBLIC_ZIP_NAME:
                    erreurs.append(f"{relatif} : archive ZIP hors budget d’inspection")
                    return
                if any(motif.search(membre.filename) for motif in expressions):
                    erreurs.append(f"{relatif} : nom privé dans un membre d’archive")
                    return
                total_annonce += membre.file_size
                ratio = membre.file_size / max(1, membre.compress_size)
                if (
                    membre.file_size > MAX_PUBLIC_ZIP_MEMBER
                    or total_annonce > MAX_PUBLIC_ZIP_TOTAL
                    or ratio > MAX_PUBLIC_ZIP_RATIO
                ):
                    # Un en-tête ne suffit pas : ces plafonds bornent aussi le travail et le flux réellement lus.
                    erreurs.append(f"{relatif} : archive ZIP hors budget d’inspection")
                    return
                if membre.is_dir():
                    continue
                lu = 0
                with archive.open(membre, "r") as contenu:
                    decodeur = codecs.getincrementaldecoder("utf-8")("ignore")
                    queue = ""
                    recouvrement = _fenetre_motif(expressions)
                    while bloc := contenu.read(64 * 1024):
                        lu += len(bloc)
                        total_lu += len(bloc)
                        if lu > membre.file_size or lu > MAX_PUBLIC_ZIP_MEMBER or total_lu > MAX_PUBLIC_ZIP_TOTAL:
                            raise ValueError("budget")
                        texte = queue + decodeur.decode(bloc)
                        if any(motif.search(texte) for motif in expressions):
                            erreurs.append(f"{relatif} : nom ou identifiant privé dans un membre d’archive")
                            return
                        queue = texte[-recouvrement:] if recouvrement else ""
                    texte = queue + decodeur.decode(b"", final=True)
                    if any(motif.search(texte) for motif in expressions):
                        erreurs.append(f"{relatif} : nom ou identifiant privé dans un membre d’archive")
                        return
                    if lu != membre.file_size:
                        raise ValueError("archive incomplète")
    except Exception:
        erreurs.append(f"{relatif} : archive ZIP illisible, incomplète ou hors budget")


def _est_zip(chemin):
    """Reconnaît une signature ZIP sans décompresser ni extraire de membre."""
    try:
        with chemin.open("rb") as flux:
            return flux.read(4) in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}
    except OSError:
        return False


def appels_interdits(texte, python):
    """Signale les appels littéraux au Python système détectés dans le texte ou son AST."""
    erreurs = []
    if re.search(r"^#!.*\bpython3\b", texte, re.M):
        erreurs.append("interpréteur système dans une ligne de lancement")
    if not python:
        if re.search(r"(?m)^\s*(?:python3|/usr/bin/python3)\s", texte):
            erreurs.append("appel du Python système")
        return erreurs
    try:
        arbre = ast.parse(texte)
    except SyntaxError:
        return ["syntaxe Python invalide"]
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.Call) or not noeud.args:
            continue
        fonction = noeud.func.attr if isinstance(noeud.func, ast.Attribute) else ""
        if fonction not in {"run", "Popen", "call", "check_call", "check_output", "system"}:
            continue
        argument = noeud.args[0]
        if isinstance(argument, (ast.List, ast.Tuple)) and argument.elts:
            premier = argument.elts[0]
            valeurs = [
                n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else "" for n in argument.elts
            ]
            if valeurs and Path(valeurs[0]).name == "env":
                indice = 1
                while indice < len(valeurs):
                    valeur = valeurs[indice]
                    if valeur in {"-u", "--unset", "-C", "--chdir"}:
                        indice += 2
                    elif valeur.startswith("-") or "=" in valeur:
                        indice += 1
                    else:
                        break
                if indice < len(argument.elts):
                    premier = argument.elts[indice]
            if isinstance(premier, ast.Constant) and isinstance(premier.value, str):
                interpreteur = Path(premier.value)
                if interpreteur.name == "python3" and ".venv" not in interpreteur.parts:
                    erreurs.append(f"appel du Python système ligne {noeud.lineno}")
        elif isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            if re.search(r"(?:^|[;&|]\s*)\s*(?:/usr/bin/)?python3\s", argument.value):
                erreurs.append(f"appel du Python système ligne {noeud.lineno}")
    return erreurs


def _fichiers_suivis(racine):
    from benchab.processus import executer

    # La capture complète empêche de perdre une entrée privée en fin d’index
    # (docs/decisions/2026-11-04-frontiere-public-prive.md).
    with tempfile.TemporaryDirectory(prefix="benchab-frontiere-") as travail:
        suivi = executer(
            ["git", "-C", str(racine), "ls-files", "-z"],
            cwd=racine, diagnostics=travail, delai=10, conserver_capture=True,
        )
        if not suivi.ok:
            return [], suivi.code_controle
        flux = Path(suivi.diagnostic).parent / "stdout.log"
        return [os.fsdecode(nom) for nom in flux.read_bytes().split(b"\0") if nom], 0


def verifier(racine, *, prive=None):
    """Contrôle chemins/fichiers publics ; erreurs = FAIL, liste vide = PASS, sans unité ni nom privé révélé."""
    racine = Path(racine).resolve()
    if not racine.is_dir():
        return ["Racine publique absente ou non accessible"]
    erreurs = []
    from benchab.chemins import atelier_prive
    from benchab.qualite.confidentialite import contient, motifs

    try:
        racine_privee = atelier_prive(prive if prive is not None else racine / "prive")
        if racine_privee == racine:
            return ["L’atelier privé recouvre la racine publique"]
        privees = {racine / "prive", racine_privee}
        expressions = motifs(racine_privee)
    except (OSError, ValueError, sqlite3.Error):
        return ["Confidentialité NON VÉRIFIÉE : base privée illisible"]

    def erreur_parcours(_erreur):
        erreurs.append("Parcours public incomplet : répertoire inaccessible")

    for dossier, sous_dossiers, fichiers in os.walk(racine, followlinks=False, onerror=erreur_parcours):
        sous_dossiers[:] = [
            n for n in sous_dossiers
            if n not in EXCLUS and not n.endswith(".egg-info") and Path(dossier) / n not in privees
        ]
        for nom in [*sous_dossiers, *fichiers]:
            chemin = Path(dossier) / nom
            relatif = chemin.relative_to(racine).as_posix()
            if chemin.is_symlink():
                cible = chemin.resolve()
                if not cible.is_relative_to(racine) or any(cible.is_relative_to(p) for p in privees):
                    erreurs.append(f"{relatif} : lien public sortant ou privé")
                continue
            if chemin.suffix in {".sqlite", ".sqlite3", ".db"}:
                erreurs.append(f"{relatif} : base de données hors du répertoire privé")
                continue
            if not chemin.is_file():
                continue
            if contient(relatif, expressions):
                erreurs.append(f"{relatif} : nom ou identifiant privé dans la partie publique")
            # Les archives renommées restent contrôlées grâce à leur signature ;
            # voir docs/decisions/2026-11-04-frontiere-public-prive.md.
            if chemin.suffix.lower() in {".zip", ".3mf"} or _est_zip(chemin):
                _inspecter_zip(chemin, relatif, expressions, erreurs)
            try:
                with chemin.open("rb") as contenu:
                    fuite_brute = _contient_flux(contenu, expressions)
            except (OSError, ValueError):
                erreurs.append(f"{relatif} : lecture publique incomplète")
            else:
                if fuite_brute:
                    erreurs.append(f"{relatif} : nom ou identifiant privé dans la partie publique")
            script = chemin.suffix in {".py", ".sh"} or nom == "Makefile" or os.access(chemin, os.X_OK)
            if chemin.suffix not in TEXTES and nom not in {"Makefile", ".gitignore"} and not script:
                with chemin.open("rb") as flux:
                    script = flux.read(2) == b"#!"
                if not script:
                    continue
            texte = chemin.read_text(errors="replace")
            for motif, description in (
                (CHEMIN_PERSONNEL, "chemin personnel"),
                (EMAIL, "adresse e-mail"),
                (JETON, "jeton"),
                (IDENTIFIANT_PYTHON if chemin.suffix == ".py" else IDENTIFIANT, "identifiant personnel ou de machine"),
            ):
                if motif.search(texte):
                    erreurs.append(f"{relatif} : {description}")
            if script:
                erreurs.extend(f"{relatif} : {erreur}" for erreur in appels_interdits(texte, chemin.suffix == ".py"))
    if (racine / ".git").exists():
        suivi, code = _fichiers_suivis(racine)
        if code == 124:
            erreurs.append("Délai de lecture de l’index Git dépassé : frontière non vérifiée")
        elif code:
            erreurs.append("Index Git illisible : frontière des fichiers suivis non vérifiée")
        else:
            # Garder l’ancien préfixe protégé même si l’atelier actif est déplacé.
            # Voir docs/decisions/2026-11-04-frontiere-public-prive.md.
            prefixes = [p.relative_to(racine).as_posix() for p in privees if p.is_relative_to(racine)]
            if any(PurePosixPath(nom).is_relative_to(prefixe) for nom in suivi for prefixe in prefixes):
                erreurs.append("Un fichier privé est suivi par Git")
    from benchab.qualite.publication import controler_modeles

    erreurs.extend(controler_modeles(racine, EXCLUS, racines_privees=privees))
    return erreurs


def main(argv=None):
    """Contrôle une racine publique ; renvoie 0 PASS ou 2 FAIL sans ouvrir de Base ni exposer de secret."""
    import argparse
    import sys

    parseur = argparse.ArgumentParser(description="Contrôle les fichiers publics et suivis du dépôt.")
    parseur.add_argument("--racine", type=Path, default=Path.cwd(), help="Racine du dépôt à contrôler")
    arguments = parseur.parse_args(argv)
    erreurs = verifier(arguments.racine)
    if erreurs:
        print("\n".join(erreurs), file=sys.stderr)
        from benchab.qualite.statuts import code_statut

        return code_statut("FAIL")
    print("PASS : frontière privée, secrets et fichiers publics suivis.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
