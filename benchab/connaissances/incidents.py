"""Relie un incident déclaré aux octets de ses tests de non-régression, sans les exécuter."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from xml.etree.ElementTree import ParseError

from benchab.archives_3mf import decoder_xml
from benchab.chemins import atelier_prive, sans_lien
from benchab.connaissances.memoire import _empreinte_blob_git
from benchab.processus import executer
from benchab.qualite.ecritures import ecrire_json_neuf, sortie_privee
from benchab.qualite.empreintes import sha256_fichier

_CHAMPS = {"schema", "issue", "revision", "symptome", "conditions", "limites", "tests"}
_SHA_GIT = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_MAX_DECLARATION = 64 * 1024
_MAX_PREUVE = 64 * 1024 * 1024
_MAX_TESTS = 8
_MAX_JUNIT = 8 * 1024 * 1024
_MAX_CAS_JUNIT = 32


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


def _identite_cas(cas):
    """Vérifie un identifiant JUnit exact, sous un fichier déclaré dans `tests`."""
    if not isinstance(cas, dict) or set(cas) != {"chemin", "classe", "nom"}:
        raise ValueError("Chaque cas JUnit doit déclarer exactement chemin, classe et nom.")
    chemin = _chemin_relatif(cas["chemin"])
    if chemin.parts[0] != "tests" or not re.fullmatch(r"test_[a-zA-Z0-9_]+\.py", chemin.name):
        raise ValueError("Chemin du cas JUnit sous tests/ requis.")
    classe = _texte(cas["classe"], "classe JUnit")
    nom = _texte(cas["nom"], "nom JUnit")
    return (chemin.as_posix(), classe, nom)


def _empreinte_fichier(info):
    """Retourne l’identité observable d’un fichier pendant une lecture bornée."""
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _lire_junit(fichier):
    """Lit une preuve JUnit régulière une fois ; SHA et XML portent sur les mêmes octets."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(fichier, flags)
    try:
        avant = os.fstat(fd)
        chemin_avant = fichier.lstat()
        if (
            not stat.S_ISREG(avant.st_mode) or not stat.S_ISREG(chemin_avant.st_mode)
            or (avant.st_dev, avant.st_ino) != (chemin_avant.st_dev, chemin_avant.st_ino)
            or avant.st_size > _MAX_JUNIT
        ):
            raise ValueError("Rapport JUnit non régulier ou au-delà de 8 Mio.")
        morceaux = []
        restant = _MAX_JUNIT + 1
        while restant:
            bloc = os.read(fd, min(64 * 1024, restant))
            if not bloc:
                break
            morceaux.append(bloc)
            restant -= len(bloc)
        donnees = b"".join(morceaux)
        apres = os.fstat(fd)
        chemin_apres = fichier.lstat()
        if (
            _empreinte_fichier(avant) != _empreinte_fichier(apres)
            or _empreinte_fichier(apres) != _empreinte_fichier(chemin_apres)
        ):
            raise ValueError("Le rapport JUnit a changé pendant sa lecture.")
        if len(donnees) > _MAX_JUNIT:
            raise ValueError("Rapport JUnit au-delà de 8 Mio.")
        return donnees
    finally:
        os.close(fd)


def _rapport_junit(donnees, cas_attendus):
    """Compare des identifiants déclarés à un contenu JUnit ; n’atteste pas l’exécution."""
    try:
        racine = decoder_xml(donnees, limite_octets=_MAX_JUNIT)
    except ParseError as erreur:
        raise ValueError("XML JUnit invalide.") from erreur
    if racine.tag.rsplit("}", 1)[-1] not in {"testsuite", "testsuites"}:
        raise ValueError("Racine XML JUnit testsuite ou testsuites requise.")
    attendus = {_identite_cas(cas): cas for cas in cas_attendus}
    rencontres = {}
    for element in racine.iter():
        if element.tag.rsplit("}", 1)[-1] != "testcase":
            continue
        identite = (element.get("file"), element.get("classname"), element.get("name"))
        if not all(isinstance(partie, str) and partie for partie in identite):
            continue
        if identite in rencontres:
            raise ValueError("Identité de cas JUnit dupliquée.")
        rencontres[identite] = element

    manquants, echecs, ignores = [], [], []
    for identite in attendus:
        element = rencontres.get(identite)
        if element is None:
            manquants.append(dict(zip(("chemin", "classe", "nom"), identite, strict=True)))
        elif any(
            enfant.tag.rsplit("}", 1)[-1] in {"failure", "error"} for enfant in element
        ):
            echecs.append(dict(zip(("chemin", "classe", "nom"), identite, strict=True)))
        elif any(enfant.tag.rsplit("}", 1)[-1] == "skipped" for enfant in element):
            ignores.append(dict(zip(("chemin", "classe", "nom"), identite, strict=True)))
    statut = (
        "CONTENU RAPPORTÉ : CAS MANQUANT" if manquants else
        "CONTENU RAPPORTÉ : ÉCHEC" if echecs else
        "CONTENU RAPPORTÉ : CAS IGNORÉ" if ignores else
        "CONTENU RAPPORTÉ : TOUS LES CAS SANS ÉCHEC"
    )
    return {
        "statut": statut, "cas_attendus": len(attendus), "cas_trouves": len(attendus) - len(manquants),
        "cas_manquants": manquants, "cas_avec_echec": echecs, "cas_ignores": ignores,
    }


def _env_git():
    """Restreint la lecture à l’objet local, sans alias d’environnement ni téléchargement."""
    return dict(
        {k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_OPTIONAL_LOCKS="0",
        GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="",
    )


def _git_court(depot, arguments, env, diagnostics):
    """Lit une petite réponse Git via le lanceur commun : 5 s, 8 Kio par flux, sans journal durable."""
    resultat = executer(
        ["git", *arguments], cwd=depot, env=env, diagnostics=diagnostics, delai=5, limite_queue=8192,
    )
    if resultat.raison != "TERMINE" or resultat.sortie_tronquee:
        raise ValueError(f"Lecture Git incomplète : {resultat.raison}.")
    return resultat.stdout if resultat.returncode == 0 else ""


def fiche(declaration, *, depot, prive):
    """Vérifie les liens privés vers un commit, ses tests et un rapport optionnel.

    Le PASS porte sur les octets Git et, si elle est fournie, l’empreinte du
    rapport local. Le format JUnit optionnel compare jusqu’à 32 cas exacts issus
    des fichiers `tests/` déclarés et rapporte leur contenu ; l’exécution reste
    NON VÉRIFIÉE. Aucun code, projet, réglage ou unité n’est écrit. Une révision
    ou un rapport non démontré lève ValueError.
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
        with tempfile.TemporaryDirectory(prefix="benchab-fiche-incident-") as diagnostics:
            objet = _git_court(depot, ["rev-parse", "--verify", revision + "^{commit}"], env, diagnostics)
            if objet.strip() != revision:
                raise ValueError("La révision ne désigne pas un commit Git local exact.")
            empreintes = []
            # L’arbre du commit distingue les liens et les fichiers, tous deux stockés en blobs.
            for nom in noms:
                membre = _git_court(
                    depot, ["--literal-pathspecs", "ls-tree", "-z", revision, "--", nom], env, diagnostics,
                )
                info, _, chemin_git = membre.removesuffix("\0").partition("\t")
                mode, _, oid = info.partition(" blob ")
                if chemin_git != nom or mode not in {"100644", "100755"} or not _SHA_GIT.fullmatch(oid):
                    raise ValueError("Fichier de test régulier absent du commit : " + nom)
                digest, code = _empreinte_blob_git(depot, oid, env)
                if code:
                    raise ValueError("Blob Git de test absent : " + nom)
                empreintes.append({"chemin": nom, "sha256": digest})
    except subprocess.TimeoutExpired as erreur:
        raise ValueError("Délai de lecture de la révision ou du blob Git dépassé.") from erreur

    execution = {"statut": "NON VÉRIFIÉ", "portee": "Le rapport déclaré n’authentifie pas une exécution."}
    if "preuve_execution" in declaration:
        preuve = declaration["preuve_execution"]
        if not isinstance(preuve, dict) or not {"chemin", "sha256"} <= preuve.keys():
            raise ValueError("Preuve d’exécution : chemin privé et empreinte SHA-256 requis.")
        format_junit = preuve.get("format") == "junit"
        if set(preuve) != ({"chemin", "sha256", "format", "cas_attendus"} if format_junit else {"chemin", "sha256"}):
            raise ValueError("Preuve d’exécution : champs exacts requis ; le format optionnel reconnu est junit.")
        chemin = _chemin_relatif(preuve["chemin"])
        if not isinstance(preuve["sha256"], str) or not _SHA256.fullmatch(preuve["sha256"]):
            raise ValueError("Preuve d’exécution : empreinte SHA-256 invalide.")
        cas_attendus = preuve.get("cas_attendus")
        if format_junit:
            if not isinstance(cas_attendus, list) or not 1 <= len(cas_attendus) <= _MAX_CAS_JUNIT:
                raise ValueError(f"De 1 à {_MAX_CAS_JUNIT} cas JUnit exacts requis.")
            identites = [_identite_cas(cas) for cas in cas_attendus]
            if len(set(identites)) != len(identites) or any(identite[0] not in noms for identite in identites):
                raise ValueError("Chaque cas JUnit doit être unique et appartenir à un fichier de test déclaré.")
        elif "format" in preuve or "cas_attendus" in preuve:
            raise ValueError("Seul le format explicite junit est reconnu.")
        racine = sans_lien(Path(prive), strict=True)
        fichier = sans_lien(racine / chemin, racine=racine, strict=True)
        limite = _MAX_JUNIT if format_junit else _MAX_PREUVE
        unite = "8 Mio" if format_junit else "64 Mio"
        if not fichier.is_file() or fichier.stat().st_size > limite:
            raise ValueError(f"Preuve d’exécution absente ou au-delà de {unite}.")
        donnees_junit = _lire_junit(fichier) if format_junit else None
        digest = hashlib.sha256(donnees_junit).hexdigest() if format_junit else sha256_fichier(fichier)
        if digest != preuve["sha256"]:
            raise ValueError("L’empreinte du rapport déclaré diffère.")
        execution.update(chemin=chemin.as_posix(), sha256=preuve["sha256"])
        if format_junit:
            execution["rapport_junit"] = _rapport_junit(donnees_junit, cas_attendus)

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
