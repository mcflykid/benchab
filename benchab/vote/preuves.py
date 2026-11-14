"""Qualification des dossiers explicites ; la validation générique ne couvre pas le conseil."""

import datetime
import hashlib
import json
import re
from pathlib import Path

from benchab.qualite import validation as _validation


def texte(v):
    """Indique si la valeur est une chaîne contenant du texte après retrait des espaces."""
    return isinstance(v, str) and bool(v.strip())


def textes(v):
    """Indique si la valeur est une liste composée uniquement de textes non vides."""
    return isinstance(v, list) and all((texte(x) for x in v))


def statut_rapport(v):
    """Statuts récursifs communs ; un code 0 seul ne prouve pas un contrôle."""
    etats = _validation.statuts(v)
    bons = {"PASS", "OK", "SUCCÈS", "SUCCES", "SUCCESS", "VALIDE", "VALIDÉ", "CONFORME"}

    def echecs(x):
        if isinstance(x, list):
            return any((echecs(y) for y in x))
        if isinstance(x, dict):
            for k, y in x.items():
                if k in ("errors", "erreurs", "verifications_manquantes") and y:
                    return True
                if k in ("returncode", "code_retour", "exit_code") and (type(y) is not int or y != 0):
                    return True
                if echecs(y):
                    return True
        return False

    if echecs(v) or any((s not in bons for s in etats)):
        return "non conforme"
    return "lisible" if etats else "sans statut reconnu"


def verifier(racine, data, categorie, niveaux, administrateurs, *, empreintes=None):
    """Vérifie les déclarations et leurs artefacts ; leur authenticité reste à examiner par l'utilisateur."""
    requis = categorie in ("B", "C")
    erreurs = []
    fichiers_verifies = {}
    if data.get("verifications_manquantes"):
        erreurs.append("vérifications déclarées manquantes : " + str(data["verifications_manquantes"]))

    def resultat():
        for fichier, (empreinte, signature) in fichiers_verifies.items():
            try:
                info = fichier.stat()
                actuelle = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                with fichier.open("rb") as flux:
                    stable = hashlib.file_digest(flux, "sha256").hexdigest() == empreinte
                if actuelle != signature or not stable:
                    raise ValueError("empreinte ou identité modifiée")
            except (OSError, ValueError):
                erreurs.append("preuve ou entrée modifiée pendant la qualification : " + fichier.name)
        return {
            "requis": requis,
            "categorie_risque": categorie,
            "statut": "Vérification nécessaire"
            if erreurs
            else "documenté"
            if "dossier_preuves" in data
            else "non requis",
            "manquants": list(dict.fromkeys(erreurs)),
        }

    if "dossier_preuves" not in data:
        if requis:
            erreurs.append("dossier_preuves obligatoire pour une décision B/C")
        return resultat()
    d = data["dossier_preuves"]
    if not isinstance(d, dict):
        erreurs.append("dossier_preuves non interprétable")
        return resultat()
    if not texte(d.get("objectif")):
        erreurs.append("objectif exact manquant")
    for champ in ("contraintes", "risques", "inconnues"):
        if not textes(d.get(champ)):
            erreurs.append(champ + " : liste explicite requise")
    couts = d.get("couts")
    if not isinstance(couts, dict) or not couts or (not all((texte(k) and texte(v) for k, v in couts.items()))):
        erreurs.append("coûts ou justification de non-applicabilité manquants")
    if d.get("verifications_manquantes"):
        erreurs.append("vérifications manquantes dans le dossier")
    racine = Path(racine).resolve()

    def fichier(source, empreinte, label):
        if not texte(source) or Path(source).is_absolute():
            erreurs.append(label + " : chemin relatif au projet requis")
            return None
        try:
            f = (racine / source).resolve()
            if not f.is_relative_to(racine) or not f.is_file():
                erreurs.append(label + " : fichier absent ou hors du projet")
                return None
            if not isinstance(empreinte, str) or not re.fullmatch("[0-9a-fA-F]{64}", empreinte):
                erreurs.append(label + " : SHA-256 manquant ou invalide")
                return None
            h = hashlib.sha256()
            info = f.stat()
            signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            with f.open("rb") as flux:
                for bloc in iter(lambda: flux.read(1 << 20), b""):
                    h.update(bloc)
            if h.hexdigest() != empreinte.lower():
                erreurs.append(label + " : empreinte périmée")
                return None
            fichiers_verifies[f] = (empreinte.lower(), signature)
            if empreintes is not None:
                empreintes[f] = empreinte.lower()
            return f
        except (OSError, ValueError, RuntimeError):
            erreurs.append(label + " : lecture impossible")
            return None

    entrees = d.get("entrees")
    if not isinstance(entrees, dict):
        erreurs.append("empreintes des entrées non déclarées")
    elif not entrees and (not texte(d.get("sans_entrees"))):
        erreurs.append("absence de fichier cible à justifier")
    else:
        for source, h in entrees.items():
            fichier(source, h, "entrée " + str(source))

    def objets(champ):
        v = d.get(champ)
        if not isinstance(v, list):
            erreurs.append(champ + " : liste requise")
            return []
        if not all((isinstance(x, dict) for x in v)):
            erreurs.append(champ + " : objet non interprétable")
        return [x for x in v if isinstance(x, dict)]

    def indexer(objets, label):
        index = {}
        for obj in objets:
            ident = obj.get("id")
            if not texte(ident) or ident in index:
                erreurs.append(label + " : identifiant absent ou dupliqué")
            else:
                index[ident] = obj
        return index

    preuves = indexer(objets("preuves"), "preuve")
    etats_sources = {}
    for ident, p in preuves.items():
        label = "preuve " + ident
        if not texte(p.get("origine")) or not texte(p.get("conclusion")):
            erreurs.append(label + " : origine ou conclusion manquante")
        try:
            date = datetime.date.fromisoformat(p.get("date", ""))
            if date > datetime.date.today():
                raise ValueError("date future")
        except (ValueError, TypeError):
            erreurs.append(label + " : date ISO absente, invalide ou future")
        if p.get("niveau") not in niveaux:
            erreurs.append(label + " : nature de preuve inconnue")
        if p.get("statut") not in ("PASS", "WARN", "FAIL", "INCONNU"):
            erreurs.append(label + " : statut inconnu")
        f = fichier(p.get("source"), p.get("sha256"), label)
        if f and f.suffix.lower() == ".json":
            try:
                p_statut = statut_rapport(json.loads(f.read_text()))
            except (OSError, ValueError, UnicodeError):
                p_statut = "illisible"
            etats_sources[ident] = p_statut
        elif f:
            etats_sources[ident] = "non JSON, statut déclaré"
        else:
            etats_sources[ident] = "absent ou périmé"
        if etats_sources[ident] == "illisible":
            erreurs.append(label + " : rapport JSON illisible")
        if p.get("statut") == "PASS" and etats_sources[ident] in ("non conforme", "sans statut reconnu"):
            erreurs.append(label + " : déclaration PASS contredite ou non vérifiable dans le rapport JSON")
    exigences = indexer(objets("verifications_requises"), "vérification")
    if not exigences:
        erreurs.append("aucune vérification indispensable déclarée")
    for ident, v in exigences.items():
        label = "vérification " + ident
        if not texte(v.get("description")):
            erreurs.append(label + " : description manquante")
        acceptes = v.get("niveaux_acceptes")
        if (
            not textes(acceptes)
            or not acceptes
            or any((x not in niveaux or x in ("probable", "inconnu") for x in acceptes))
        ):
            erreurs.append(label + " : natures de preuves admissibles non justifiées")
            acceptes = []
        ref = v.get("preuve")
        p = preuves.get(ref) if isinstance(ref, str) else None
        if not p:
            erreurs.append(label + " : preuve indispensable absente")
            continue
        if (
            p.get("statut") != "PASS"
            or p.get("niveau") not in acceptes
            or etats_sources.get(ref) not in ("lisible", "non JSON, statut déclaré")
        ):
            erreurs.append(label + " : preuve en échec, douteuse, périmée ou de nature insuffisante")
    criteres = d.get("criteres")
    if not textes(criteres) or not criteres:
        erreurs.append("critères de comparaison manquants")
        criteres = []
    solutions = indexer(objets("solutions"), "solution")
    roles = []
    for ident, s in solutions.items():
        role = s.get("role")
        roles.append(role)
        if role not in ("proposition", "alternative", "statu_quo") or not texte(s.get("description")):
            erreurs.append("solution " + ident + " : rôle ou description manquant")
        ev = s.get("evaluation")
        if not isinstance(ev, dict) or any((not texte(ev.get(c)) for c in criteres)):
            erreurs.append("solution " + ident + " : comparaison incomplète")
        refs = s.get("preuves", [])
        if not textes(refs) or any((ref not in preuves for ref in refs)):
            erreurs.append("solution " + ident + " : référence de preuve inconnue")
    if roles.count("proposition") != 1 or roles.count("statu_quo") != 1:
        erreurs.append("une proposition et un statu quo explicites requis")
    if "alternative" not in roles and (not texte(d.get("absence_alternative"))):
        erreurs.append("alternative crédible ou absence justifiée requise")
    if not isinstance(d.get("choix"), str) or d["choix"] not in solutions:
        erreurs.append("solution recommandée absente ou inconnue")
    if isinstance(data.get("options"), list):
        for option in data["options"]:
            if option not in solutions:
                erreurs.append("option de la question absente de la comparaison : " + str(option))
    objections = objets("objections")
    auteurs = set()
    for obj in objections:
        a = obj.get("auteur")
        if not isinstance(a, str) or a not in administrateurs:
            erreurs.append("auteur d’objection inconnu")
        else:
            auteurs.add(a)
        if any(
            (not texte(obj.get(c)) for c in ("probleme", "consequence", "fondement", "test", "correction", "reexamen"))
        ):
            erreurs.append("objection constructive incomplète")
    if not objections and (not texte(d.get("absence_objection"))):
        erreurs.append("objections ou absence justifiée à renseigner")
    for a, v in data.get("votes", {}).items():
        if v.get("vote") in ("contre", "abstention") and a not in auteurs:
            erreurs.append(a + " : objection structurée manquante")
    return resultat()


def rendre(rec):
    """Retourne les lignes Markdown du dossier de preuves ou une liste vide s’il manque."""
    q = rec.get("qualite_decision")
    if not q:
        return []
    lignes = ["- Dossier de preuves : " + q["statut"], *["- Vérification nécessaire : " + m for m in q["manquants"]]]
    d = rec.get("dossier_preuves")
    if not isinstance(d, dict):
        return lignes
    for champ in (
        "objectif",
        "contraintes",
        "couts",
        "risques",
        "inconnues",
        "choix",
        "absence_alternative",
        "absence_objection",
    ):
        if champ in d:
            lignes.append("- " + champ + " : " + str(d[champ]))
    for p in d.get("preuves", []) if isinstance(d.get("preuves"), list) else []:
        if isinstance(p, dict):
            lignes.append(
                "- Preuve "
                + str(p.get("id", "?"))
                + " : "
                + str(p.get("niveau", "?"))
                + " ; "
                + str(p.get("date", "?"))
                + " ; "
                + str(p.get("origine", "?"))
                + " ; "
                + str(p.get("source", "?"))
                + " ; "
                + str(p.get("statut", "?"))
                + " ; "
                + str(p.get("conclusion", "?"))
            )
    for s in d.get("solutions", []) if isinstance(d.get("solutions"), list) else []:
        if isinstance(s, dict):
            lignes.append(
                "- Solution "
                + str(s.get("id", "?"))
                + " ("
                + str(s.get("role", "?"))
                + ") : "
                + str(s.get("description", "?"))
                + " ; "
                + str(s.get("evaluation", "?"))
            )
    for o in d.get("objections", []) if isinstance(d.get("objections"), list) else []:
        if isinstance(o, dict):
            lignes.append("- Objection structurée : " + str(o))
    return lignes
