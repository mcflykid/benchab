"""Relie un incident déclaré aux octets de ses tests de non-régression, sans les exécuter."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

from benchab.chemins import atelier_prive, sans_lien
from benchab.connaissances.memoire import _empreinte_blob_git
from benchab.qualite.ecritures import ecrire_json_neuf, sortie_privee
from benchab.qualite.empreintes import sha256_fichier

_CHAMPS = {"schema", "issue", "revision", "symptome", "conditions", "limites", "tests"}
_SHA_GIT = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_MAX_DECLARATION = 64 * 1024
_MAX_PREUVE = 64 * 1024 * 1024
_MAX_TESTS = 8


def _chemin_relatif(chemin):
    """Refuse les chemins ambigus, absolus et les variantes d’encodage de séparateurs."""
    if (
        not isinstance(chemin, str) or not chemin or "\\" in chemin or ":" in chemin
        or any(c in chemin for c in "\0\r\n")
        or any(p in {"", ".", ".."} for p in chemin.split("/"))
    ):
        raise ValueError("Chemin relatif canonique requis.")
    return PurePosixPath(chemin)


def _texte(valeur, nom):
    """Vérifie un texte déclaré et borné ; aucune condition n’est inventée."""
    if not isinstance(valeur, str) or not valeur.strip() or len(valeur) > 1500 or "\0" in valeur:
        raise ValueError(f"Texte non vide de 1 500 caractères maximum requis : {nom}.")
    return valeur


def _conditions(valeurs, nom):
    """Conserve les conditions et limites explicites, dans l’ordre déclaré."""
    if not isinstance(valeurs, list) or not 1 <= len(valeurs) <= 12:
        raise ValueError(f"Liste de 1 à 12 éléments requise : {nom}.")
    return [_texte(valeur, nom) for valeur in valeurs]


def _env_git():
    """Restreint la lecture à l’objet local, sans alias d’environnement ni téléchargement."""
    return dict(
        {k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_OPTIONAL_LOCKS="0",
        GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="",
    )


def fiche(declaration, *, depot, prive):
    """Vérifie les liens de la fiche privée, sans unité ni exécution de test.

    Le PASS porte sur les octets Git et, si elle est fournie, l’empreinte du
    rapport local. L’exécution reste NON VÉRIFIÉE ; aucun code, projet ou réglage
    n’est écrit. Une révision ou un rapport non démontré lève ValueError.
    """
    if (
        not isinstance(declaration, dict)
        or not _CHAMPS <= declaration.keys() <= _CHAMPS | {"preuve_execution"}
        or type(declaration["schema"]) is not int or declaration["schema"] != 1
    ):
        raise ValueError("Déclaration d’incident schéma 1 et champs exacts requis.")
    issue, revision = declaration["issue"], declaration["revision"]
    if type(issue) is not int or not 1 <= issue <= 10**9:
        raise ValueError("Numéro d’issue BENCHAB positif requis.")
    if not isinstance(revision, str) or not _SHA_GIT.fullmatch(revision):
        raise ValueError("Une révision Git exacte est requise.")
    symptome = _texte(declaration["symptome"], "symptome")
    conditions = _conditions(declaration["conditions"], "conditions")
    limites = _conditions(declaration["limites"], "limites")
    tests = declaration["tests"]
    if not isinstance(tests, list) or not 1 <= len(tests) <= _MAX_TESTS:
        raise ValueError(f"Liste de 1 à {_MAX_TESTS} fichiers de test requise.")
    noms = []
    for nom in tests:
        try:
            chemin = _chemin_relatif(nom)
        except ValueError as erreur:
            raise ValueError(f"Chemin de test invalide : {erreur}") from erreur
        if (
            chemin.parts[0] != "tests" or not re.fullmatch(r"test_[a-zA-Z0-9_]+\.py", chemin.name)
            or nom in noms
        ):
            raise ValueError("Fichier de test unique sous tests/ requis.")
        noms.append(nom)

    depot = sans_lien(Path(depot), strict=True)
    if not depot.is_dir():
        raise ValueError("Dépôt Git local requis.")
    env = _env_git()
    try:
        objet = subprocess.run(
            ["git", "-C", str(depot), "rev-parse", "--verify", "--end-of-options", revision + "^{commit}"],
            capture_output=True, env=env, timeout=5, check=False,
        )
        if objet.returncode or objet.stdout.strip() != revision.encode("ascii"):
            raise ValueError("La révision ne désigne pas un commit Git local exact.")
        empreintes = []
        # L’arbre du commit distingue les liens et les fichiers, tous deux stockés en blobs.
        for nom in noms:
            membre = subprocess.run(
                ["git", "-C", str(depot), "--literal-pathspecs", "ls-tree", "-z", revision, "--", nom],
                capture_output=True, env=env, timeout=5, check=False,
            )
            info, _, chemin_git = membre.stdout.removesuffix(b"\0").partition(b"\t")
            mode, _, oid = info.partition(b" blob ")
            if (
                membre.returncode or chemin_git != os.fsencode(nom) or mode not in {b"100644", b"100755"}
                or not _SHA_GIT.fullmatch(oid.decode("ascii", errors="replace"))
            ):
                raise ValueError("Fichier de test régulier absent du commit : " + nom)
            digest, code = _empreinte_blob_git(depot, oid.decode("ascii"), env)
            if code:
                raise ValueError("Blob Git de test absent : " + nom)
            empreintes.append({"chemin": nom, "sha256": digest})
    except subprocess.TimeoutExpired as erreur:
        raise ValueError("Délai de lecture de la révision ou du blob Git dépassé.") from erreur

    execution = {"statut": "NON VÉRIFIÉ", "portee": "Le rapport déclaré n’authentifie pas une exécution."}
    if "preuve_execution" in declaration:
        preuve = declaration["preuve_execution"]
        if not isinstance(preuve, dict) or set(preuve) != {"chemin", "sha256"}:
            raise ValueError("Preuve d’exécution : chemin privé et empreinte SHA-256 requis.")
        chemin = _chemin_relatif(preuve["chemin"])
        if not isinstance(preuve["sha256"], str) or not _SHA256.fullmatch(preuve["sha256"]):
            raise ValueError("Preuve d’exécution : empreinte SHA-256 invalide.")
        racine = sans_lien(Path(prive), strict=True)
        fichier = sans_lien(racine / chemin, racine=racine, strict=True)
        if not fichier.is_file() or fichier.stat().st_size > _MAX_PREUVE:
            raise ValueError("Preuve d’exécution absente ou au-delà de 64 Mio.")
        if sha256_fichier(fichier) != preuve["sha256"]:
            raise ValueError("L’empreinte du rapport déclaré diffère.")
        execution.update(chemin=chemin.as_posix(), sha256=preuve["sha256"])

    return {
        "schema": 1, "statut_lien": "PASS", "issue": issue, "revision": revision,
        "symptome": symptome, "conditions": conditions, "limites": limites, "tests": empreintes,
        "execution": execution, "validation_physique": False,
    }


def _cles_uniques(paires):
    """Refuse les déclarations JSON ambiguës plutôt que de retenir la dernière valeur."""
    donnees = {}
    for cle, valeur in paires:
        if cle in donnees:
            raise ValueError("Clé JSON dupliquée : " + cle)
        donnees[cle] = valeur
    return donnees


def main(argv=None, *, racine=None):
    """Vérifie et publie facultativement une fiche privée neuve ; 0 lien prouvé, 2 refus.

    Reçoit le chemin JSON de déclaration, un dépôt Git local et une sortie
    optionnelle. Aucun test n’est exécuté, aucune donnée source n’est modifiée.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("declaration", type=Path)
    parser.add_argument("--depot", type=Path, required=True)
    parser.add_argument("--sortie", type=Path)
    args = parser.parse_args(argv)
    try:
        prive = atelier_prive(racine)
        source = sans_lien(args.declaration, racine=prive, strict=True)
        if not source.is_file():
            raise ValueError("La déclaration doit être un fichier privé ordinaire.")
        with source.open("rb") as flux:
            contenu = flux.read(_MAX_DECLARATION + 1)
        if len(contenu) > _MAX_DECLARATION:
            raise ValueError("Déclaration au-delà de 64 Kio.")
        donnees = json.loads(contenu, object_pairs_hook=_cles_uniques)
        rapport = fiche(donnees, depot=args.depot, prive=prive)
        digest = hashlib.sha256(contenu).hexdigest()
        if sha256_fichier(source) != digest:
            raise ValueError("La déclaration a changé pendant la vérification.")
        rapport["declaration_sha256"] = digest
        if args.sortie:
            cible = sortie_privee(args.sortie, prive=prive)
            entrees = [source]
            if "chemin" in rapport["execution"]:
                entrees.append(prive / rapport["execution"]["chemin"])
            ecrire_json_neuf(cible, rapport, entrees=entrees)
            rapport = {"statut_lien": rapport["statut_lien"], "rapport": str(cible)}
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, RuntimeError) as erreur:
        print(f"BENCHAB : {erreur}", file=sys.stderr)
        return 2
