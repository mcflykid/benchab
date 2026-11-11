"""Parcours local BENCHAB 3D : diagnostic, synthèse, reprise et ouverture contrôlée.

Les vues et checkpoints sont écrits uniquement dans un espace explicite hors dépôt.
Aucune commande de fabrication déclarée n'est lancée implicitement, aucun accès Cloud.
"""

import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path

from benchab.archives_3mf import Archive3MF
from benchab.chemins import atelier_prive
from benchab.fabrication.slicer_runtime import executable, informations
from benchab.fabrication.trancher import demander_ouverture
from benchab.organisation.synthese_fichiers import MAX_ARCHIVE, _archive_liee, _lecture
from benchab.qualite import publication
from benchab.qualite.ecritures import PublicationNeuve, _sans_lien
from benchab.qualite.empreintes import sha256_fichier as sha
from benchab.qualite.statuts import code_statut

ICI = Path(__file__).resolve().parent


def lire_json(path, limite=2000000, sha_attendu=None):
    path = _sans_lien(Path(path).absolute())
    from benchab.visualisation.stockage_commentaires import ouvrir_dossier

    directory = ouvrir_dossier(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(fd, "rb") as f:
        avant = os.fstat(f.fileno())
        if not stat.S_ISREG(avant.st_mode) or avant.st_size > limite:
            raise ValueError("document irrégulier ou volumineux")
        body = f.read(limite + 1)
        apres = os.fstat(f.fileno())
        if len(body) > limite or (avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns) != (
            apres.st_size,
            apres.st_mtime_ns,
            apres.st_ctime_ns,
        ):
            raise ValueError("document modifié pendant lecture")
    if sha_attendu is not None and hashlib.sha256(body).hexdigest() != sha_attendu:
        raise ValueError("document altéré depuis le contrôle")
    try:
        return json.loads(body)
    except RecursionError:
        raise ValueError("document JSON trop profond") from None


def lire_etat(path, nom):
    _sans_lien(path)
    if not path.exists():
        return {"schema": 1, "projet": nom, "etapes": {}}
    d = lire_json(path, 20000000)
    if (
        not isinstance(d, dict)
        or d.get("schema") != 1
        or d.get("projet") != nom
        or (not isinstance(d.get("etapes"), dict))
    ):
        raise ValueError("état de reprise invalide ou associé à un autre projet")
    for entry in d["etapes"].values():
        if not isinstance(entry, dict):
            raise ValueError("étape de reprise invalide")
        for key in ("entrees", "sorties"):
            values = entry.get(key, {})
            if not isinstance(values, dict) or any(
                (not isinstance(k, str) or not isinstance(v, str) for k, v in values.items())
            ):
                raise ValueError("empreintes de reprise invalides")
    return d


def projet(root, nom):
    """Charge un projet privé par identifiant; chemins sans unité, FAIL si absent ou incohérent."""
    root = atelier_prive(root)
    if not re.fullmatch("[a-zA-Z0-9][a-zA-Z0-9_-]*", nom):
        raise ValueError("nom de projet invalide")
    p = publication.relatif(root, "projets/" + nom)
    if not p.is_dir():
        raise ValueError("projet absent")
    q, s = publication.charger(p)
    return (p, q)


def source(root, p, rel):
    """Les manifestes historiques mélangent chemins racine et chemins projet."""
    return publication.chemin_projet(root, p, rel)


def preparation(root, nom, ident=None):
    p, q = projet(root, nom)
    ident = ident or q["suivi"].get("preparation_courante")
    if not isinstance(ident, str) or not re.fullmatch("P[0-9]+", ident):
        raise ValueError("choisir une préparation Pnn explicitement")
    f = publication.relatif(p, "preparations/" + ident + "/PREPARATION.json")
    if not f.is_file() or f.stat().st_size > 2000000:
        raise ValueError("manifeste de préparation absent ou volumineux")
    d = lire_json(f)
    if not isinstance(d, dict) or d.get("id") != ident:
        raise ValueError("identifiant de préparation incohérent")
    return (p, q, f, d)


def controle_preparation(root, nom, ident=None):
    """Contrôle une préparation figée; renvoie PASS/FAIL et SHA, sans modifier ses réglages."""
    root = atelier_prive(root)
    p, q, f, d = preparation(root, nom, ident)
    erreurs = []
    avertissements = []
    fichiers = []
    for key in ("plaques_3mf", "tranche_3mf"):
        item = d.get(key, {})
        if (
            not isinstance(item, dict)
            or not item.get("fichier")
            or (not re.fullmatch("[0-9a-f]{64}", str(item.get("sha256", ""))))
        ):
            erreurs.append(key + " : fichier et SHA-256 requis")
            continue
        target = source(root, p, item["fichier"])
        if not target.is_file() or sha(target) != item["sha256"]:
            erreurs.append(key + " : fichier absent ou altéré")
            continue
        try:
            with _archive_liee(target, item["sha256"]) as archive:
                if not archive.noms():
                    raise ValueError("archive vide")
                archive.verifier()
        except (zipfile.BadZipFile, OSError, ValueError):
            erreurs.append(key + " : archive invalide ou trop volumineuse")
            continue
        fichiers.append(str(target.relative_to(Path(root))))
    controles = d.get("controles", {})
    if not isinstance(controles, dict) or not controles:
        erreurs.append("contrôles de préparation absents")
    else:
        for rel, info in controles.items():
            target = source(root, p, rel)
            if not isinstance(info, dict) or not target.is_file() or sha(target) != info.get("sha256"):
                erreurs.append("contrôle absent ou altéré : " + rel)
                continue
            if target.stat().st_size > 2000000:
                erreurs.append("contrôle trop volumineux : " + rel)
                continue
            data = lire_json(target, sha_attendu=info.get("sha256"))
            from benchab.qualite.validation import statuts

            actual = statuts(data) or ["NON VÉRIFIÉ"]
            if any((str(v).upper() in ("FAIL", "ERROR", "BLOQUE", "BLOQUÉ", "NO GO") for v in actual)):
                erreurs.append("contrôle en échec : " + rel)
            if any((str(v).upper() not in ("PASS", "OK") for v in actual)):
                avertissements.append("réserves à lire : " + rel)
    return {
        "statut": "FAIL" if erreurs else "PASS",
        "preparation": d["id"],
        "manifeste": str(f.relative_to(Path(root))),
        "fichiers": fichiers,
        "erreurs": erreurs,
        "avertissements": avertissements,
        "limite": "intégrité documentaire ; aucune aptitude physique garantie",
    }


def environnement(root):
    """Décrit l'environnement local; versions et empreintes sans unité, aucune installation."""
    root = atelier_prive(root)
    py = sys.executable
    moteur = informations(executable(), empreinte=False)
    checks = [
        {"controle": "slicer", "statut": "PASS" if moteur["present"] else "WARN", "detail": moteur},
        {
            "controle": "Blender facultatif",
            "statut": "PASS" if shutil.which("blender") else "WARN",
            "detail": "nécessaire aux rendus Blender seulement",
        },
    ]
    code = (
        "import sys,importlib.util,json;print(json.dumps({'python':sys.versio"
        "n.split()[0],'modules':{n:importlib.util.find_spec(n) is not None fo"
        "r n in ['numpy','trimesh','manifold3d']}}))"
    )
    try:
        res = subprocess.run([py, "-I", "-c", code], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        res = None
    if res is not None and res.returncode == 0:
        info = json.loads(res.stdout)
        checks.append(
            {
                "controle": "interpréteur géométrique",
                "statut": "PASS" if all(info["modules"].values()) else "WARN",
                "detail": info,
            }
        )
    else:
        checks.append(
            {"controle": "interpréteur géométrique", "statut": "WARN", "detail": "import impossible ou délai dépassé"}
        )
    return {
        "statut": "WARN" if any((x["statut"] != "PASS" for x in checks)) else "PASS",
        "controles": checks,
        "imprimante": "NON_VERIFIEE",
        "ecriture_systeme": False,
    }


def cache_projet(root, nom):
    p, q = projet(root, nom)
    from benchab.controles.cache_geometrie import Projet, cache_lire

    class Lecture(Projet):
        def __init__(self):
            self.racine = p
            self.d = p / "organisation"

        def journal(self, **kw):
            pass

        def ecrire(self, *args):
            raise ValueError("consultation seule")

        def charger(self, nom, defaut):
            return data if nom == "cache.json" else defaut

    lecture = Lecture()
    f = publication.relatif(p, "organisation/cache.json")
    if not f.is_file():
        return {"statut": "ABSENT", "raison": "aucun résultat enregistré", "entrees": {}}
    if f.stat().st_size > 2000000:
        raise ValueError("cache volumineux")
    data = lire_json(f)
    if not isinstance(data, dict) or any((not isinstance(v, dict) for v in data.values())):
        raise ValueError("cache invalide")
    for entry in data.values():
        if not isinstance(entry.get("dependances", []), list) or any(
            (not isinstance(v, str) for v in entry.get("dependances", []))
        ):
            raise ValueError("dépendances de cache invalides")
        for field in ("entrees", "outils"):
            values = entry.get(field, {})
            if not isinstance(values, dict) or any(
                (not isinstance(k, str) or not isinstance(v, str) for k, v in values.items())
            ):
                raise ValueError("empreintes de cache invalides")
            for rel in values:
                publication.relatif(p, rel)
    return {
        "statut": "CONSULTE",
        "entrees": {
            k: {
                **cache_lire(lecture, k),
                "hypotheses_non_verifiees": v.get("hypotheses", []),
                "conditions_non_evaluees": v.get("invalide_si", []),
            }
            for k, v in data.items()
        },
    }


def creer_preparation(root, nom, sortie, executer_creation=False):
    """Prépare une candidate privée; chemins sans unité, état PASS/WARN/FAIL, réglages conservés."""
    root = atelier_prive(root)
    p, q = projet(root, nom)
    pair = {
        k: publication.relatif(p, "candidats/" + n) for k, n in [("plaques", "plaques.3mf"), ("tranche", "tranche.3mf")]
    }
    if not pair["tranche"].is_file() and publication.relatif(p, "candidats/tranches.3mf").is_file():
        pair["tranche"] = p / "candidats/tranches.3mf"
    controles = [
        publication.relatif(p, "preuves/" + n)
        for n in ("controle-envoi.json", "geometrie.json")
        if publication.relatif(p, "preuves/" + n).is_file()
    ]
    missing = [k for k, v in pair.items() if not v.is_file()]
    if not controles:
        missing.append("rapports de contrôles candidats")
    if missing:
        return {
            "statut": "BLOQUE",
            "manquants": missing,
            "action": "produire les candidats et leurs contrôles avec les cibles du projet ; aucun tranchage implicite",
        }
    for f in controles:
        if f.stat().st_size > 2000000:
            raise ValueError("rapport de contrôle volumineux")
        from benchab.qualite.validation import statuts

        if any((str(v).upper() in ("FAIL", "ERROR", "BLOQUE", "BLOQUÉ", "NO GO") for v in statuts(lire_json(f)))):
            return {"statut": "BLOQUE", "erreur": "contrôle candidat en échec : " + f.name}
    for f in pair.values():
        with _lecture(f, 128 * 1024 * 1024) as flux, Archive3MF.depuis_descripteur(
            f, flux.fileno(), taille_max_membre=MAX_ARCHIVE, taille_max_totale=MAX_ARCHIVE,
        ) as archive:
            if not archive.noms():
                raise ValueError("archive candidate vide")
            archive.verifier()
    ids = [int(x.name[1:]) for x in (p / "preparations").glob("P*") if re.fullmatch("P[0-9]+", x.name)]
    ident = "P" + str(max(ids, default=0) + 1).zfill(2)
    argv = [
        sys.executable,
        "-B",
        "-m",
        "benchab.fabrication.preparation",
        "--prive",
        str(root),
        "creer",
        "--projet",
        str(p),
        "--id",
        ident,
        "--plaques",
        str(pair["plaques"]),
        "--tranche",
        str(pair["tranche"]),
        "--controles",
        *map(str, controles),
    ]
    if not executer_creation:
        return {
            "statut": "PLANIFIE",
            "preparation": ident,
            "commande": argv,
            "action": "ajouter --creer pour figer une nouvelle version sans écraser les précédentes",
        }
    out = dossier_sortie(root, sortie)
    with verrou_execution(out):
        if any((p / "preparations").glob("P*.en-cours")):
            return {"statut": "BLOQUE", "erreur": "création interrompue à examiner avant nouvelle préparation"}
        from benchab.processus import executer as processus

        result = processus(argv, cwd=root, delai=180, diagnostics=out / "diagnostics")
        report = {
            "statut": "PASS" if result.ok else "BLOQUE",
            "preparation": ident,
            "code": result.returncode,
            "raison": result.raison,
            "action": "consulter la nouvelle préparation"
            if result.ok
            else "examiner le dossier .en-cours et le diagnostic ; aucune relance automatique",
        }
        sauver(out / "creation.json", report)
        return report


def planifier(root, nom, ident=None):
    p, q = projet(root, nom)
    ident = ident or q["suivi"].get("preparation_courante")
    blocages = []
    if not ident:
        blocages.append(
            {
                "cause": "préparation non choisie",
                "action": (
                    "indiquer --preparation Pnn ; créer auparavant une préparation avec preparation.py si elle manque"
                ),
            }
        )
    else:
        try:
            ctl = controle_preparation(root, nom, ident)
            blocages += [
                {"cause": e, "action": "corriger ou choisir une nouvelle préparation ; préserver Pnn"}
                for e in ctl["erreurs"]
            ]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            blocages.append({"cause": str(exc), "action": "vérifier le manifeste et ses chemins"})
    cfg = publication.relatif(p, "organisation/organisation.json")
    cibles = json.loads(cfg.read_text()).get("cibles", {}) if cfg.is_file() else {}
    return {
        "projet": nom,
        "preparation": ident,
        "statut": "BLOQUE" if blocages else "PLANIFIE",
        "blocages": blocages,
        "etapes": ["environnement", "preparation", "resume", "connaissances", "cache", "vues"],
        "fabrication_declaree": [
            {
                "nom": n,
                "commande": v.get("commande"),
                "apres": v.get("apres", []),
                "execution": "non lancée par la consultation ; suivre les prérequis du projet",
            }
            for n, v in cibles.items()
        ],
        "physique": "aucune commande",
        "cloud": "aucune connexion implicite",
    }


def diagnostiquer(root, nom, ident=None):
    plan = planifier(root, nom, ident)
    globales = []
    if (Path(root) / "BASE.json").is_file() or (Path(root) / "benchab.sqlite").is_file():
        from benchab.qualite import coherence

        globales = [
            {"cause": e, "action": "vérifier le registre et son export ; aucune réparation automatique"}
            for e in coherence.verifier(Path(root))
        ]
    from benchab.qualite.references import verify

    references = verify(root, historical=False, anchors=True, cache=True)
    erreurs_references = [
        {
            "cause": e,
            "action": (
                "python3 outils/maintenance/verifier_reference.py --sans-historique -"
                "-ancres ; corriger la source indiquée"
            ),
        }
        for e in references["errors"]
    ]
    return {
        **plan,
        "environnement": environnement(root),
        "coherence": globales,
        "references": {
            "statut": "PASS" if references["ok"] else "FAIL",
            "erreurs": erreurs_references,
            "controles": references["checks"],
        },
        "statut": "BLOQUE" if plan["blocages"] or globales or erreurs_references else "PASS",
        "limite": "diagnostic local ; réserves métier et état physique distincts",
    }


def empreintes(root, nom, ident):
    """Hache projet, outils et sources courantes ; SHA/ABSENT sans unité, lecture seule, aucun réglage changé."""
    root = atelier_prive(root)
    p, q, f, d = preparation(root, nom, ident)
    paths = {f, p / "qualite.json"}
    for n in ("README.md", "ETAT.md", "BESOIN.md", "organisation/organisation.json", "organisation/cache.json"):
        x = publication.relatif(p, n)
        if x.is_file():
            paths.add(x)
    for x in (p / "src").rglob("*.py"):
        if x.is_symlink():
            raise ValueError("source symbolique")
        paths.add(x)
    for field in ("plaques_3mf", "tranche_3mf"):
        if d.get(field, {}).get("fichier"):
            paths.add(source(root, p, d[field]["fichier"]))
    for rel in d.get("controles", {}):
        paths.add(source(root, p, rel))
    for field in ("demande", "sources", "modeles"):
        for rel in d.get(field, {}):
            paths.add(source(root, p, rel))
    out = {str(x.relative_to(Path(root))): sha(x) if x.is_file() else "ABSENT" for x in paths}
    for folder in ("candidats", "src"):
        for x in publication.relatif(p, folder).rglob("*"):
            if x.is_symlink():
                raise ValueError("ressource de vue symbolique")
            if x.is_file() and x.suffix in (".json", ".stl", ".step", ".glb", ".3mf", ".py"):
                paths.add(x)
    for rel in ("preuves/placement.json", "preuves/geometrie.json", *q["suivi"].get("preuves_utiles", [])):
        x = source(root, p, rel)
        if x.is_file():
            paths.add(x)
    for field in ("manifeste", "stl_assemblage", "stl_impression"):
        if not q.get(field):
            continue
        x = publication.relatif(p, q[field])
        if x.is_file():
            paths.add(x)
        elif x.is_dir():
            for f in x.rglob("*"):
                if f.is_symlink():
                    raise ValueError("géométrie symbolique")
                if f.is_file():
                    paths.add(f)
    out.update({str(x.relative_to(root)): sha(x) if x.is_file() else "ABSENT" for x in paths})
    registry = Path(root) / "donnees/connaissances/registre.json"
    if registry.is_file():
        out[str(registry.relative_to(Path(root)))] = sha(registry)
        for item in json.loads(registry.read_text()).get("connaissances", []):
            for s in item.get("sources", []):
                rel = s.get("chemin", "").split("#")[0]
                if not rel:
                    continue
                x = publication.relatif(Path(root), rel)
                out[rel] = sha(x) if x.is_file() else "ABSENT"
    for folder in ("organisation", "qualite", "visualisation", "fabrication", "connaissances", "projets"):
        for x in (ICI.parent / folder).glob("*.py"):
            if not x.name.startswith("test_"):
                out["outil:" + x.relative_to(ICI.parent).as_posix()] = sha(x)
    if (root / "benchab.sqlite").is_file():
        out["projet:canonique"] = hashlib.sha256(json.dumps(q, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        import sqlite3

        from benchab.connaissances.sqlite import lire_connexion

        db = sqlite3.connect((root / "benchab.sqlite").as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            projection = lire_connexion(db)
            out["connaissances:canoniques"] = hashlib.sha256(
                json.dumps(projection, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            # Une preuve peut changer sans mise à jour de son SHA déclaré dans SQLite :
            # son état réel invalide la reprise (docs/reference/donnees.md).
            for item in projection["connaissances"]:
                for preuve in item.get("sources", []):
                    if not isinstance(preuve, dict):
                        continue
                    rel = preuve.get("chemin", "").split("#", 1)[0]
                    if not rel or "://" in rel:
                        continue
                    x = publication.relatif(root, rel)
                    out["connaissance-source:" + rel] = sha(x) if x.is_file() else "ABSENT"
        finally:
            db.close()
    out["interpreteur"] = sys.version + "|" + platform.system() + "|" + platform.machine()
    return out


def dossier_sortie(root, path):
    """Crée un dossier de sortie hors dépôt; chemin sans unité, refuse liens et sorties interdites."""
    path = _sans_lien(Path(path).absolute())
    root = atelier_prive(root)
    if path.is_relative_to(root) and not (
        (root / "benchab.sqlite").is_file() and path.is_relative_to(root / "mesures")
    ):
        raise ValueError("sortie dans le dépôt refusée ; choisir un espace personnel hors dépôt")
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def verrou_execution(sortie):
    from benchab.visualisation.stockage_commentaires import ouvrir_dossier

    directory = ouvrir_dossier(sortie)
    try:
        fd = os.open(".execution.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 384, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(fd, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("exécution déjà active ; verrou non forcé")
        yield


def sauver(path, data):
    path = _sans_lien(Path(path).absolute())
    from benchab.visualisation.stockage_commentaires import ouvrir_dossier

    directory = ouvrir_dossier(path.parent)
    temp = ".etat-" + uuid.uuid4().hex + ".tmp"
    try:
        body = json.dumps(data, ensure_ascii=False, indent=1, allow_nan=False).encode("utf-8")
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 384, dir_fd=directory)
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temp, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def raison_cache(entry, signature, sortie):
    raisons = []
    if entry.get("statut") != "PASS":
        raisons.append("étape absente, interrompue ou en échec")
    if entry.get("entrees") != signature:
        raisons.append("entrées, paramètres ou outils modifiés")
    outputs = entry.get("sorties", {})
    if not outputs:
        raisons.append("sorties non attestées")
    for rel, h in outputs.items():
        p = publication.relatif(sortie, rel)
        if not p.is_file() or sha(p) != h:
            raisons.append("sortie absente ou altérée : " + rel)
    return raisons


def reprise(root, nom, sortie, ident=None):
    plan = planifier(root, nom, ident)
    path = _sans_lien(Path(sortie).absolute()) / "execution.json"
    old = lire_etat(path, nom)
    sig = empreintes(root, nom, plan["preparation"]) if not plan["blocages"] else None
    sig = {**(sig or {}), "preparation": plan["preparation"]}
    return {
        **plan,
        "etapes": [
            {
                "id": n,
                "etat": "A_REFAIRE"
                if (reasons := raison_cache(old.get("etapes", {}).get(n, {}), sig, path.parent))
                else "REUTILISABLE",
                "raisons": reasons,
            }
            for n in plan["etapes"]
        ],
    }


def executer(root, nom, sortie, ident=None):
    from benchab.organisation.synthese_atelier import bilan, connaissances, generer_vues

    plan = planifier(root, nom, ident)
    if plan["blocages"]:
        return plan
    out = dossier_sortie(root, sortie)
    with verrou_execution(out):
        statefile = out / "execution.json"
        state = lire_etat(statefile, nom)
        if state.get("projet") != nom:
            raise ValueError("espace de reprise associé à un autre projet")
        sig = {**empreintes(root, nom, plan["preparation"]), "preparation": plan["preparation"]}
        funcs = {
            "environnement": lambda: environnement(root),
            "preparation": lambda: controle_preparation(root, nom, plan["preparation"]),
            "resume": lambda: bilan(root, nom, plan["preparation"]),
            "connaissances": lambda: connaissances(root, nom),
            "cache": lambda: cache_projet(root, nom),
        }
        for name in plan["etapes"]:
            old = state["etapes"].get(name, {})
            raisons = raison_cache(old, sig, out)
            if not raisons:
                continue
            state["etapes"][name] = {"statut": "EN_COURS", "entrees": sig, "raisons": raisons}
            sauver(statefile, state)
            try:
                if name == "vues":
                    bundle = out / ("vues-" + uuid.uuid4().hex)
                    data = generer_vues(root, nom, bundle, plan["preparation"])
                    outputs = {str(f.relative_to(out)): sha(f) for f in bundle.rglob("*") if f.is_file()}
                else:
                    data = funcs[name]()
                    outputs = {}
                if empreintes(root, nom, plan["preparation"]) != {k: v for k, v in sig.items() if k != "preparation"}:
                    raise ValueError("entrées modifiées pendant le calcul")
                target = out / (name + ".json")
                sauver(target, data)
                outputs[target.name] = sha(target)
                status = (
                    "FAIL"
                    if data.get("statut") in ("FAIL", "BLOQUE", "INDISPONIBLE")
                    or (name == "vues" and (not any((x.get("statut") == "DISPONIBLE" for x in data.get("vues", [])))))
                    else "PASS"
                )
                state["etapes"][name] = {
                    "statut": status,
                    "entrees": sig,
                    "sorties": outputs,
                    "raisons": raisons,
                    "resultat": data.get("statut", "CONSULTE"),
                }
                sauver(statefile, state)
                if status == "FAIL":
                    break
            except BaseException as exc:
                state["etapes"][name].update(
                    statut="INTERROMPU" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "FAIL",
                    erreur=type(exc).__name__,
                )
                sauver(statefile, state)
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                break
        state["statut"] = (
            "PASS" if all((state["etapes"].get(n, {}).get("statut") == "PASS" for n in plan["etapes"])) else "BLOQUE"
        )
        sauver(statefile, state)
        return state


def ouvrir_slicer(root, nom, sortie, ident=None, ouvrir=False):
    """Prépare une ouverture explicite du slicer; PASS/WARN/FAIL documentaire, aucune impression."""
    root = atelier_prive(root)
    signature = empreintes(root, nom, ident)
    ctl = controle_preparation(root, nom, ident)
    if ctl["statut"] != "PASS":
        return ctl
    p, q, f, d = preparation(root, nom, ident)
    src = source(root, p, d["tranche_3mf"]["fichier"])
    if not ouvrir:
        return {**ctl, "action": "ouverture non demandée ; ajouter --ouvrir pour créer et ouvrir une copie de travail"}
    moteur = informations(executable(), empreinte=False)
    if not moteur["present"]:
        raise ValueError("slicer absent : aucune installation automatique")
    out = dossier_sortie(root, sortie)
    dest = out / (nom + "-" + d["id"] + "-" + uuid.uuid4().hex[:8] + ".3mf")
    with PublicationNeuve([src], [dest]) as pub:
        shutil.copyfile(src, pub.temporaire(dest))
        if sha(pub.temporaire(dest)) != d["tranche_3mf"]["sha256"]:
            raise ValueError("source modifiée pendant copie")
        pub.publier()
    if empreintes(root, nom, ident) != signature:
        raise ValueError("sources ou contrôles modifiés avant ouverture")
    try:
        result = demander_ouverture(dest, prive=root, sha_attendu=d["tranche_3mf"]["sha256"])
    except subprocess.TimeoutExpired:
        return {
            **ctl,
            "statut": "INCONNU",
            "copie": str(dest),
            "ouverture": "INCONNUE",
            "limite": "délai dépassé ; vérifier la fenêtre du slicer, aucune relance automatique",
        }
    return {
        **ctl,
        "statut": "PASS" if result["code"] == 0 else "FAIL",
        "copie": str(dest),
        "ouverture": "DEMANDEE" if result["code"] == 0 else "REFUSEE",
        "limite": "requête d’ouverture ; fenêtre et résultat physique non déduits",
    }


def tests_impact(root, changements, executer_tests=False):
    from benchab.qualite.impact import executer, planifier

    plan = planifier(root, changements)
    return executer(root, plan) if executer_tests else plan


def main(argv=None, *, prive=None):
    """Exécute une action sur l'atelier explicite; chemins en unités de fichier, sans créer l'atelier."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "commande", choices=["diagnostic", "plan", "preparer", "executer", "reprise", "ouvrir-slicer", "tests-impact"]
    )
    parser.add_argument("--creer", action="store_true")
    parser.add_argument("--projet")
    parser.add_argument("--preparation")
    parser.add_argument("--racine", type=Path, default=atelier_prive(prive))
    parser.add_argument("--sortie", type=Path)
    parser.add_argument("--ouvrir", action="store_true")
    parser.add_argument("--executer-tests", action="store_true")
    parser.add_argument("--changements", nargs="*", default=[])
    a = parser.parse_args(argv)
    a.racine = atelier_prive(a.racine)
    if a.commande != "tests-impact" and (not a.projet):
        parser.error("--projet requis")
    out = a.sortie or a.racine / "mesures/executions" / str(a.projet)
    try:
        if a.commande == "preparer":
            data = creer_preparation(a.racine, a.projet, out, a.creer)
            if a.creer and data["statut"] == "PASS":
                data = executer(a.racine, a.projet, out, data["preparation"])
        elif a.commande == "tests-impact":
            data = tests_impact(a.racine, a.changements, a.executer_tests)
        elif a.commande == "ouvrir-slicer":
            data = ouvrir_slicer(a.racine, a.projet, out, a.preparation, a.ouvrir)
        elif a.commande in ("reprise", "executer"):
            data = globals()[a.commande](a.racine, a.projet, out, a.preparation)
        else:
            data = (diagnostiquer if a.commande == "diagnostic" else planifier)(a.racine, a.projet, a.preparation)
        statut = data.get("statut")
        if statut in {"PASS", "WARN", "FAIL", "BLOQUE", "INCONNU", "NON VÉRIFIÉ"}:
            data["code"] = code_statut(statut)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return data.get("code", 0)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        print(
            json.dumps(
                {
                    "statut": "BLOQUE",
                    "code": code_statut("BLOQUE"),
                    "erreur": "document JSON trop profond" if isinstance(exc, RecursionError) else str(exc),
                },
                ensure_ascii=False,
            )
        )
        return code_statut("BLOQUE")


if __name__ == "__main__":
    raise SystemExit(main())
