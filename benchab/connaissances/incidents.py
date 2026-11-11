"""Relie un incident déclaré aux octets de ses tests de non-régression, sans les exécuter."""

import argparse
import hashlib
import http.client
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
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
_MAX_CI_JSON = 256 * 1024
_DELAI_CI = 5
_API_GITHUB = "https://api.github.com"
_WORKFLOW_CI = ".github/workflows/tests.yml"
_ETAPE_CI = "Run uv run pytest -q"


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    """Refuse toute redirection hors de l’API GitHub explicitement prévue."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Ne construit aucune nouvelle requête à partir d’une réponse HTTP."""
        return None


def _get_github_public(url):
    """Lit un endpoint public GitHub fixe : GET sans identifiants, 5 s et 256 Kio."""
    partie = urllib.parse.urlsplit(url)
    chemin_autorise = re.fullmatch(
        r"/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[1-9][0-9]*/attempts/[1-9][0-9]*(?:/jobs)?",
        partie.path,
    )
    if (
        partie.scheme != "https" or partie.hostname != "api.github.com" or partie.port is not None
        or partie.username is not None or partie.password is not None
        or chemin_autorise is None
        or (partie.path.endswith("/jobs") and partie.query != "per_page=20")
        or (not partie.path.endswith("/jobs") and partie.query)
    ):
        raise ValueError("Endpoint GitHub hors périmètre.")
    requete = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "BENCHAB-incident-readonly",
        },
    )
    ouvreur = urllib.request.build_opener(urllib.request.ProxyHandler({}), _SansRedirection)
    with ouvreur.open(requete, timeout=_DELAI_CI) as reponse:
        if getattr(reponse, "status", 200) != 200:
            raise ValueError("Réponse HTTP GitHub inattendue.")
        donnees = reponse.read(_MAX_CI_JSON + 1)
    if len(donnees) > _MAX_CI_JSON:
        raise ValueError("Réponse GitHub au-delà du budget.")
    resultat = json.loads(donnees, object_pairs_hook=_cles_uniques)
    if not isinstance(resultat, dict):
        raise ValueError("Réponse GitHub JSON objet requise.")
    return resultat


def _depot_github(depot):
    """Dérive un dépôt public uniquement depuis un origin GitHub sans identifiants."""
    env = _env_git()
    with tempfile.TemporaryDirectory(prefix="benchab-origine-ci-") as diagnostics:
        cles = _git_court(
            depot, ["config", "--local", "--name-only", "--get-regexp", r"^remote\.origin\.url$"],
            env, diagnostics,
        )
        if cles.splitlines() != ["remote.origin.url"]:
            raise ValueError("origin local absent ou ambigu")
        # Git filtre la valeur avant capture : un origin avec identifiants
        # n’apparaît jamais dans la sortie du lanceur, même en diagnostic.
        motif = (
            r"^((https://github\.com|ssh://git@github\.com)/|git@github\.com:)"
            r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"
        )
        origin = _git_court(depot, ["config", "--local", "--get", "remote.origin.url", motif], env, diagnostics).strip()
    if not origin:
        raise ValueError("origin local hors périmètre GitHub")

    def identifier_depuis_chemin(chemin):
        """Valide les deux composants d’un chemin de dépôt GitHub."""
        morceaux = chemin.split("/")
        if len(morceaux) != 3 or morceaux[0] != "":
            return None
        proprietaire, depot_nom = morceaux[1:]
        if depot_nom.endswith(".git"):
            depot_nom = depot_nom[:-4]
        if proprietaire in {".", ".."} or depot_nom in {".", ".."} or not re.fullmatch(
            r"[A-Za-z0-9_.-]+", proprietaire
        ) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+", depot_nom
        ):
            return None
        return proprietaire + "/" + depot_nom

    if origin.startswith("https://"):
        url = urllib.parse.urlsplit(origin)
        if (
            url.scheme != "https" or url.hostname != "github.com" or url.port is not None
            or url.username is not None or url.password is not None or url.query or url.fragment
        ):
            raise ValueError("origin HTTPS hors périmètre")
        resultat = identifier_depuis_chemin(url.path)
    elif origin.startswith("ssh://"):
        url = urllib.parse.urlsplit(origin)
        if (
            url.scheme != "ssh" or url.hostname != "github.com" or url.username != "git"
            or url.password is not None or url.port is not None or url.query or url.fragment
        ):
            raise ValueError("origin SSH hors périmètre")
        resultat = identifier_depuis_chemin(url.path)
    else:
        correspondance = re.fullmatch(r"git@github\.com:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", origin)
        resultat = None if correspondance is None else "/".join(correspondance.groups())
        if resultat is not None and resultat.split("/")[0] in {".", ".."}:
            resultat = None
        if resultat is not None and resultat.rsplit("/", 1)[-1] in {".", ".."}:
            resultat = None
        if resultat is not None and resultat.endswith(".git"):
            resultat = resultat[:-4]
    if resultat is None:
        raise ValueError("origin hors périmètre GitHub")
    return resultat


def _ci_non_verifiee(run_id, tentative, raison):
    """Construit un statut public neutre sans inclure la réponse ou une erreur brute."""
    return {
        "statut": "NON VÉRIFIÉ",
        "motif": raison,
        "run_id": run_id,
        "tentative": tentative,
    }


def _observer_ci_github(reference, revision, depot_local):
    """Vérifie une tentative publique précise sans authentifier la fiche ni son exécution."""
    if not isinstance(reference, str) or not (
        match := re.fullmatch(r"([1-9][0-9]{0,19})/([1-9][0-9]{0,3})", reference)
    ):
        return _ci_non_verifiee(None, None, "identifiant de run/tentative invalide")
    run_id, tentative = map(int, match.groups())
    try:
        depot_github = _depot_github(depot_local)
        base = f"{_API_GITHUB}/repos/{depot_github}/actions/runs/{run_id}/attempts/{tentative}"
        run = _get_github_public(base)
        travaux = _get_github_public(base + "/jobs?per_page=20")
    except (OSError, TimeoutError, http.client.HTTPException, urllib.error.URLError, ValueError, TypeError,
            json.JSONDecodeError,
            subprocess.SubprocessError):
        return _ci_non_verifiee(run_id, tentative, "lecture publique indisponible ou invalide")

    depot = run.get("repository")
    depot_tete = run.get("head_repository")
    chemin = run.get("path")
    nom_depot = depot.get("full_name") if isinstance(depot, dict) else None
    nom_tete = depot_tete.get("full_name") if isinstance(depot_tete, dict) else None
    if not isinstance(nom_depot, str) or nom_depot.casefold() != depot_github.casefold():
        return _ci_non_verifiee(run_id, tentative, "dépôt du run non concordant")
    if not isinstance(nom_tete, str) or nom_tete.casefold() != depot_github.casefold():
        return _ci_non_verifiee(run_id, tentative, "dépôt source non concordant")
    if run.get("head_sha") != revision:
        return _ci_non_verifiee(run_id, tentative, "SHA de tête différent de la fiche")
    if (
        type(run.get("id")) is not int or run.get("id") != run_id
        or type(run.get("run_attempt")) is not int or run.get("run_attempt") != tentative
    ):
        return _ci_non_verifiee(run_id, tentative, "run ou tentative différent de la demande")
    if run.get("event") != "push" or run.get("head_branch") != "main":
        return _ci_non_verifiee(run_id, tentative, "événement ou branche différent de push main")
    if not isinstance(chemin, str) or chemin not in {_WORKFLOW_CI, _WORKFLOW_CI + "@refs/heads/main"}:
        return _ci_non_verifiee(run_id, tentative, "workflow différent du workflow de tests attendu")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        return _ci_non_verifiee(run_id, tentative, "run non terminé avec succès")
    if not isinstance(travaux, dict) or type(travaux.get("total_count")) is not int or travaux.get("total_count") != 1:
        return _ci_non_verifiee(run_id, tentative, "nombre de jobs différent du job unique attendu")
    jobs = travaux.get("jobs")
    if not isinstance(jobs, list) or len(jobs) != 1 or not isinstance(jobs[0], dict):
        return _ci_non_verifiee(run_id, tentative, "liste de jobs non unique ou invalide")
    job = jobs[0]
    if type(job.get("run_id")) is not int or job.get("run_id") != run_id:
        return _ci_non_verifiee(run_id, tentative, "job rattaché à un autre run")
    if type(job.get("run_attempt")) is not int or job.get("run_attempt") != tentative:
        return _ci_non_verifiee(run_id, tentative, "job rattaché à une autre tentative")
    if job.get("head_sha") != revision or job.get("head_branch") != "main":
        return _ci_non_verifiee(run_id, tentative, "SHA ou branche du job non concordant")
    if job.get("name") != "tests" or job.get("status") != "completed" or job.get("conclusion") != "success":
        return _ci_non_verifiee(run_id, tentative, "job tests non terminé avec succès")
    etapes = job.get("steps")
    if not isinstance(etapes, list):
        return _ci_non_verifiee(run_id, tentative, "étapes du job absentes")
    etapes_cible = [etape for etape in etapes if isinstance(etape, dict) and etape.get("name") == _ETAPE_CI]
    if (
        len(etapes_cible) != 1 or etapes_cible[0].get("status") != "completed"
        or etapes_cible[0].get("conclusion") != "success"
    ):
        return _ci_non_verifiee(run_id, tentative, "étape pytest unique absente ou non réussie")
    return {
        "statut": "OBSERVATION CI PUBLIQUEMENT VÉRIFIÉE",
        "source": "API publique GitHub, réponse sans authentification",
        "depot": depot_github,
        "revision": revision,
        "evenement": "push",
        "branche": "main",
        "workflow": _WORKFLOW_CI,
        "run_id": run_id,
        "tentative": tentative,
        "job": "tests",
        "etape": _ETAPE_CI,
        "conclusion": "success",
        "portee": (
            "Observation publique du run ; ne prouve ni l’identité de l’exécuteur "
            "ni l’authenticité d’un rapport JUnit."
        ),
    }


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
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
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


def fiche(declaration, *, depot, prive, ci_github=None):
    """Vérifie les liens privés vers un commit, ses tests et un rapport optionnel.

    Le PASS porte sur les octets Git et, si elle est fournie, l’empreinte du
    rapport local. Le format JUnit optionnel compare jusqu’à 32 cas exacts issus
    des fichiers `tests/` déclarés et rapporte leur contenu ; l’exécution reste
    NON VÉRIFIÉE. Aucun code, projet, réglage ou unité n’est écrit. Une révision
    ou un rapport non démontré lève ValueError. L’observation CI publique est
    ajoutée seulement si `ci_github` contient explicitement `ID/TENTATIVE`.
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

    resultat = {
        "schema": 1, "statut_lien": "PASS", "issue": issue, "revision": revision,
        "symptome": symptome, "conditions": conditions, "limites": limites, "tests": empreintes,
        "execution": execution, "validation_physique": False,
    }
    if ci_github is not None:
        resultat["ci_github"] = _observer_ci_github(ci_github, revision, depot)
    return resultat


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
    parser.add_argument(
        "--ci-github", metavar="ID/TENTATIVE", help="Observe explicitement une tentative CI publique GitHub"
    )
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
        rapport = fiche(donnees, depot=args.depot, prive=prive, ci_github=args.ci_github)
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
