"""Barrières communes : preuves courantes, couverture, fichiers produits et gel.
Lecture seule. Aucune décision humaine n'est inventée par cet outil.
"""

import json
from pathlib import Path

from benchab.qualite.empreintes import sha256_fichier as sha


def statuts(q):
    """Statuts de contrôle récursifs normalisés ; aucune décision physique."""
    found = []
    if isinstance(q, dict):
        if "schema_controle" in q:
            # Le résultat est indépendant de l’état du projet et des métadonnées.
            if q.get("schema_controle") != 1:
                return ["NON VÉRIFIÉ"]
            requis = (
                "execution",
                "resultat",
                "applicabilite",
                "niveau_preuve",
                "origine",
                "entree_sha256",
                "outil_version",
                "date",
                "conditions_validite",
                "decision_humaine",
                "gravite",
            )
            if any(k not in q for k in requis):
                return ["NON VÉRIFIÉ"]
            try:
                from datetime import datetime

                date = datetime.fromisoformat(q["date"])
                if date.tzinfo is None:
                    raise ValueError("date sans zone")
                construit = rapport_controle(
                    execution=q["execution"],
                    resultat=q["resultat"],
                    gravite=q["gravite"],
                    applicabilite=q["applicabilite"],
                    preuve=q["niveau_preuve"],
                    origine=q["origine"],
                    entree_sha256=q["entree_sha256"],
                    outil_version=q["outil_version"],
                    conditions=q["conditions_validite"],
                    decision=q["decision_humaine"],
                )
                if any(q[k] != construit[k] for k in ("execution", "resultat", "decision_humaine")):
                    return ["NON VÉRIFIÉ"]
            except (ValueError, TypeError, KeyError):
                return ["NON VÉRIFIÉ"]
            if q["execution"] == "ERREUR_EXECUTION":
                return ["ERROR"]
            if (
                q["execution"] != "PASS"
                or q["applicabilite"] != "APPLICABLE"
                or q["decision_humaine"] == "EN_ATTENTE_HUMAINE"
            ):
                return ["NON VÉRIFIÉ"]
            return [q["resultat"]] if q["resultat"] in ("PASS", "FAIL", "WARNING") else ["NON VÉRIFIÉ"]
        for k, v in q.items():
            if k in ("pass", "pass_", "conforme", "tout_conforme", "tous_conformes", "ok") and v is False:
                found.append("FAIL")
            elif k in ("status", "statut", "statut_global"):
                value = str(v).upper().strip()
                found.append("WARNING" if value == "WARN" else value)
            else:
                found.extend(statuts(v))
    elif isinstance(q, list):
        for v in q:
            found.extend(statuts(v))
    return found


def agregation_statut(q):
    """FAIL > NON VÉRIFIÉ > WARNING > PASS. Absence/inconnu/non applicable non justifié = NON VÉRIFIÉ."""
    values = statuts(q)
    if any(v in ("FAIL", "ERROR") for v in values):
        return "FAIL"
    if not values or any(v not in ("PASS", "WARNING") for v in values):
        return "NON VÉRIFIÉ"
    return "WARNING" if "WARNING" in values else "PASS"


def mauvais(q, autorises=("PASS",)):
    allowed = {"WARNING" if str(s).upper() == "WARN" else str(s).upper() for s in autorises}
    values = statuts(q)
    return not values or any(v not in allowed or v not in ("PASS", "WARNING") for v in values)


def verifier(base, cfg=None, humain=True):
    base = Path(base).resolve()
    cfg = cfg or json.loads((base / "qualite.json").read_text())
    out = []

    def add(n, ok, detail="", statut=None):
        out.append(dict(controle=n, statut=statut or ("PASS" if ok else "FAIL"), detail=detail))

    mp = base / cfg["manifeste"]
    if not mp.exists():
        add("manifeste", False, "absent")
        return out
    try:
        M = json.loads(mp.read_text())
    except Exception as e:
        add("manifeste lisible", False, str(e))
        return out
    h = sha(mp)
    names = {p["name"] for p in M["parts"] if p.get("group") != "hors-assemblage"}
    for n, q in {**M["models"], **M.get("options", {})}.items():
        for key, folder in (("stl_sha256", "stl_assemblage"), ("print_sha256", "stl_impression")):
            f = base / cfg[folder] / (n + ".stl")
            add("presence " + folder + "/" + n, f.is_file())
            if not f.is_file():
                continue
            try:
                actual = sha(f)
            except OSError as e:
                add("lisibilite " + folder + "/" + n, False, str(e))
                continue
            add("lisibilite " + folder + "/" + n, True)
            add(
                "integrite " + folder + "/" + n,
                bool(q.get(key)) and actual == q[key],
                "empreinte attendue absente" if not q.get(key) else "",
                statut="NON VÉRIFIÉ" if not q.get(key) else None,
            )
    for n, expected in M.get("sources_sha256", {}).items():
        f = base / "src" / n
        add("source " + n, f.exists() and sha(f) == expected)
    specs = cfg.get("preuves_obligatoires", [])
    add("preuves declarees", bool(specs), "qualite.json : preuves_obligatoires")
    for spec in specs:
        spec = {"fichier": spec} if isinstance(spec, str) else spec
        p = base / spec["fichier"]
        if not p.exists():
            add("preuve " + spec["fichier"], False, "absente")
            continue
        try:
            q = json.loads(p.read_text())
        except Exception as e:
            add("preuve " + spec["fichier"], False, str(e))
            continue
        if not isinstance(q, dict):
            add("preuve " + spec["fichier"], False, "objet JSON attendu")
            continue
        add(
            "manifeste " + spec["fichier"],
            q.get("assembly_manifest_sha256") == h,
            "preuve perimee ou sans empreinte" if q.get("assembly_manifest_sha256") != h else "",
        )
        status = q.get("status", q.get("statut_global", q.get("statut")))
        allowed = spec.get("statuts", ["PASS"])
        accepted = status in allowed and not mauvais(q, allowed)
        add(
            "statut " + spec["fichier"],
            accepted,
            str(status),
            statut="WARNING" if accepted and agregation_statut(q) == "WARNING" else None,
        )
        if spec.get("montage_complet"):
            seen = set(q.get("couverture_occurrences", []))
            add(
                "couverture montage100%",
                seen == names,
                f"absentes={sorted(names - seen)}; inconnues={sorted(seen - names)}",
            )
    # Generated STL files must correspond to declared models; G-code stays in preuves/.
    allowed = set(cfg.get("candidats_fichiers", []))
    for folder in (cfg["stl_assemblage"], cfg["stl_impression"]):
        for n in {**M["models"], **M.get("options", {})}:
            allowed.add(str(Path(folder) / f"{n}.stl"))
    if cfg.get("stl_nominal"):
        for n in {**M["models"], **M.get("options", {})}:
            allowed.add(str(Path(cfg["stl_nominal"]) / f"{n}.stl"))
    for sub in cfg.get("manifestes_derives", []):
        p = base / sub
        if p.exists():
            q = json.loads(p.read_text())
            add("manifeste derive " + sub, q.get("assembly_manifest_sha256") == h)
            for n in q.get("models", {}):
                allowed.add(str(p.parent / f"{n}.stl"))
    unknown = []
    for p in (base / "candidats").rglob("*"):
        if p.is_file() and str(p.relative_to(base)) not in allowed:
            unknown.append(str(p.relative_to(base)))
    add("aucun candidat orphelin", not unknown, ", ".join(unknown[:30]))
    if humain:
        for key in ("choix_esthetique", "relecture_humaine"):
            s = cfg.get(key, {})
            if s.get("requise"):
                f = base / s.get("preuve", "__absent__")
                valid = False
                if f.exists():
                    q = json.loads(f.read_text())
                    valid = (
                        q.get("statut") == "APPROUVE"
                        and q.get("assembly_manifest_sha256") == h
                        and bool(q.get("source_utilisateur"))
                    )
                add(key, valid, "accord utilisateur/relecture absent ou perime" if not valid else "")
    return out


def exiger(base, humain=True):
    items = verifier(base, humain=humain)
    bad = [q for q in items if q["statut"] not in ("PASS", "WARNING")]
    if bad:
        raise RuntimeError("Validation refusee : " + "; ".join(q["controle"] + ": " + q["detail"] for q in bad[:12]))
    return items


def protege(path):
    """Un fichier listé dans LIVRABLES.json est figé, même si son chmod est modifiable."""
    p = Path(path).resolve()
    for root in p.parents:
        f = root / "LIVRABLES.json"
        if f.exists():
            q = json.loads(f.read_text())
            listed = {str((root / a["chemin"]).resolve()) for a in q.get("fichiers", [])}
            if str(p) in listed:
                return str(root)
    return None


STATUTS_NORMALISES = frozenset(
    {"PASS", "FAIL", "WARNING", "NON_VERIFIE", "NON_APPLICABLE", "ERREUR_EXECUTION", "EN_ATTENTE_HUMAINE"}
)


def normaliser_statut(value):
    """Conversion déclarée des anciens vocabulaires ; inconnu reste non vérifié."""
    import unicodedata

    v = "".join(
        c for c in unicodedata.normalize("NFD", str(value).upper().strip()) if not unicodedata.combining(c)
    ).replace(" ", "_")
    v = {
        "WARN": "WARNING",
        "ERROR": "ERREUR_EXECUTION",
        "INCONNU": "NON_VERIFIE",
        "ATTENTE_HUMAINE": "EN_ATTENTE_HUMAINE",
    }.get(v, v)
    return v if v in STATUTS_NORMALISES else "NON_VERIFIE"


def rapport_controle(
    *,
    execution,
    resultat,
    gravite,
    applicabilite,
    preuve,
    origine,
    entree_sha256,
    outil_version,
    conditions,
    decision="NON_VERIFIE",
):
    """Contrat additif : les rapports historiques restent lisibles via statuts()."""
    import datetime
    import re

    execution = normaliser_statut(execution)
    resultat = normaliser_statut(resultat)
    decision = normaliser_statut(decision)
    if execution not in {"PASS", "ERREUR_EXECUTION", "NON_VERIFIE"}:
        raise ValueError("exécution invalide")
    if resultat not in {"PASS", "FAIL", "WARNING", "NON_VERIFIE", "NON_APPLICABLE"}:
        raise ValueError("résultat invalide")
    if gravite not in {"INFO", "MINEURE", "MAJEURE", "CRITIQUE"} or applicabilite not in {
        "APPLICABLE",
        "NON_APPLICABLE",
        "NON_VERIFIE",
    }:
        raise ValueError("gravité/applicabilité invalide")
    if preuve not in {"NUMERIQUE", "DOCUMENTAIRE", "DECLARATION_HUMAINE", "PHYSIQUE", "NON_VERIFIE"}:
        raise ValueError("preuve invalide")
    if not all(isinstance(x, str) and x.strip() for x in (origine, outil_version)) or not re.fullmatch(
        "[0-9a-f]{64}", entree_sha256
    ):
        raise ValueError("provenance incomplète")
    if (
        not isinstance(conditions, list)
        or not conditions
        or any(not isinstance(x, str) or not x.strip() for x in conditions)
    ):
        raise ValueError("conditions de validité requises")
    if (resultat == "NON_APPLICABLE") != (applicabilite == "NON_APPLICABLE"):
        raise ValueError("non applicabilité contradictoire")
    return {
        "schema_controle": 1,
        "execution": execution,
        "resultat": resultat,
        "gravite": gravite,
        "applicabilite": applicabilite,
        "niveau_preuve": preuve,
        "decision_humaine": decision,
        "origine": origine,
        "entree_sha256": entree_sha256,
        "outil_version": outil_version,
        "date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "conditions_validite": conditions,
    }
