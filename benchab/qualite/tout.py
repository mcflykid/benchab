"""Pipeline local déclaratif. Arrêt au premier échec, état et différences automatiques.
Usage : python tout.py qualite.json [--incremental|--verifier]
Aucune commande d'imprimante ; les commandes déclarées sont celles du projet.
"""

import argparse
import ast
import datetime
import hashlib
import importlib.metadata
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

from benchab.chemins import atelier_prive, est_temporaire_hors_depot, sans_lien
from benchab.machine.configuration import charger as configuration_machine
from benchab.processus import executer
from benchab.qualite.validation import agregation_statut, protege, sha, verifier

DELAI_ETAPE_SECONDES = 1800

def executer_commande(cmd, base, log, stage):
    """Exécute argv dans un groupe borné ; renvoie le code de contrôle et journalise toute la sortie.

    Entrées : argv, répertoire, journal et délai en secondes ; sortie : code entier
    (0 PASS, autre valeur FAIL). Aucun shell n'est lancé et les descendants sont arrêtés
    au délai déclaré. Voir docs/reference/controles.md.
    """
    delai = stage.get("delai_secondes")
    if delai is None:
        # Un pipeline ancien sans délai reste exécutable mais borné
        # (docs/reference/controles.md).
        delai = DELAI_ETAPE_SECONDES
    if (
        isinstance(delai, bool) or not isinstance(delai, (int, float)) or not math.isfinite(delai) or delai <= 0
    ):
        raise ValueError("delai_secondes doit être un nombre positif et fini : " + stage["id"])
    # Le wrapper commun crée un nouveau groupe POSIX et conserve les captures complètes
    # sur échec/délai ; le pipeline garde un journal lisible à son emplacement habituel.
    resultat = executer(
        cmd,
        cwd=base,
        diagnostics=log.parent / "processus",
        delai=delai,
        shell=False,
        conserver_capture=True,
        fusion_stderr=True,
    )
    with log.open("wb") as sortie:
        if resultat.diagnostic:
            captures = Path(resultat.diagnostic).parent
            for nom in ("stdout.log", "stderr.log"):
                capture = captures / nom
                if capture.is_file():
                    with capture.open("rb") as source:
                        shutil.copyfileobj(source, sortie)
        if resultat.code_controle == 124:
            sortie.write(
                f"\nDélai de l’étape dépassé ({delai:g} s) ; vérifier les éventuels processus descendants\n".encode()
            )
    return resultat.code_controle


def ecrire_si_change(path, contenu):
    data = contenu.encode()
    if path.exists() and path.read_bytes() == data:
        return
    fd, name = tempfile.mkstemp(prefix=".qualite-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def evenement(record):
    # Durées et utilisation du cache ne changent pas une preuve.
    return {
        k: ([{a: b for a, b in step.items() if a not in ("secondes", "cache")} for step in v] if k == "steps" else v)
        for k, v in record.items()
        if k != "date"
    }


def etat(base, cfg, steps, previous, statut, anomalies):
    out = base / "preuves/qualite"
    out.mkdir(parents=True, exist_ok=True)
    mp = base / cfg["manifeste"]
    h = sha(mp) if mp.exists() else None
    models = {}
    chiffres = {}
    if mp.exists():
        try:
            models = {n: q.get("stl_sha256") for n, q in json.loads(mp.read_text()).get("models", {}).items()}
        except Exception:
            pass
    t = base / cfg.get("rapport_tranchage", "preuves/tranchage.json")
    if t.exists():
        try:
            chiffres["tranchage"] = json.loads(t.read_text()).get("plaques")
        except Exception:
            pass
    now = datetime.datetime.now().isoformat(timespec="seconds")
    record = dict(
        date=now,
        statut=statut,
        assembly_manifest_sha256=h,
        steps=steps,
        anomalies=anomalies,
        modeles=models,
        chiffres=chiffres,
    )
    changed = [
        n for n in set(models) | set(previous.get("modeles", {})) if models.get(n) != previous.get("modeles", {}).get(n)
    ]
    oldstatus = {s["id"]: s["statut"] for s in previous.get("steps", [])}
    changes = [
        s["id"] + ": " + oldstatus.get(s["id"], "absent") + " → " + s["statut"]
        for s in steps
        if oldstatus.get(s["id"]) != s["statut"]
    ]
    diff = [
        "# Différences depuis la relance précédente",
        "",
        f"Manifeste : {previous.get('assembly_manifest_sha256', 'absent')} → {h}",
        f"Pièces modifiées : {', '.join(sorted(changed)) or 'aucune'}",
    ] + changes
    if chiffres != previous.get("chiffres", {}):
        diff += ["Durées / masses : " + json.dumps(chiffres, ensure_ascii=False)]
    ecrire_si_change(out / "DIFFERENCES.md", "\n".join(diff) + "\n")
    lines = [
        "# Synthèse — " + statut,
        f"Date : {now}",
        f"Manifeste : {h or 'absent'}",
        "Décision : NO GO tant que la validation numérique et les accords requis ne sont pas terminés.",
    ]
    lines += [s["id"] + " : " + s["statut"] + (" (cache contrôlé)" if s.get("cache") else "") for s in steps][:10]
    lines += ["Anomalie : " + a for a in anomalies[:3]]
    lines += [
        "Prochaine étape : "
        + (
            "corriger le premier échec, puis relancer make tout"
            if statut == "FAIL"
            else "attendre le choix ou la relecture demandée"
            if statut == "ATTENTE HUMAINE"
            else "consulter les preuves et la décision du projet"
        )
    ]
    ecrire_si_change(out / "SYNTHESE.md", "\n".join(lines[:20]) + "\n")
    ecrire_si_change(
        base / "ETAT.md",
        "\n".join(lines[:20]) + "\n\nÉtat régénéré par benchab qualite tout ; voir preuves/qualite/DIFFERENCES.md.\n",
    )
    ecrire_si_change(out / "dernier-etat.json", json.dumps(record, indent=2, allow_nan=False))
    if statut != "RUNNING" and evenement(record) != evenement(previous):
        hist = out / "executions"
        hist.mkdir(exist_ok=True)
        # UUID + microsecondes, jamais un écrasement à la seconde.
        ident = datetime.datetime.now().strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex
        with (hist / (ident + ".json")).open("x") as f:
            json.dump(record, f, ensure_ascii=False, indent=2, allow_nan=False)
        if statut == "FAIL":
            fails = out / "echecs"
            fails.mkdir(exist_ok=True)
            for step in steps:
                log = out / (step["id"] + ".log")
                if step.get("statut") == "FAIL" and log.is_file():
                    dest = fails / (sha(log) + ".log")
                    if not dest.exists():
                        with dest.open("xb") as f:
                            with log.open("rb") as source:
                                shutil.copyfileobj(source, f)

    return record


class ContexteCache:
    """Mémoisation strictement limitée à une exécution, signatures avec ctime."""

    def __init__(self):
        self.hashes = {}
        self.versions = None
        self.octets_lus = 0
        self.lectures = 0

    def empreinte(self, p):
        p = Path(p)
        st = p.stat()
        sig = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        old = self.hashes.get(p)
        if old and old[0] == sig:
            return old[1]
        value = sha(p)
        self.hashes[p] = (sig, value)
        self.octets_lus += st.st_size
        self.lectures += 1
        return value


def empreinte(base, stage, cfg=None, contexte=None):
    """Défaut conservateur ; contrat cache explicite pour les étapes indépendantes.

    cache.dependances : code et données supplémentaires (globs relatifs au dépôt),
    cache.paquets : distributions pertinentes, cache.environnement : variables,
    cache.configuration : clés de premier niveau pertinentes (défaut toute cfg).
    Le commandant doit déclarer les imports dynamiques ; aucun auto-scope silencieux.
    """
    base = Path(base)
    contexte = contexte or ContexteCache()
    package = Path(__file__).resolve().parents[1]
    root = package.parent
    spec = stage.get("cache")
    if spec is not None:
        if not isinstance(spec, dict) or set(spec) - {"dependances", "paquets", "environnement", "configuration"}:
            raise ValueError("contrat cache invalide")
        if not {"dependances", "paquets", "environnement"} <= spec.keys():
            raise ValueError("contrat cache incomplet")
        if any(not isinstance(v, list) or any(not isinstance(x, str) for x in v) for v in spec.values()):
            raise ValueError("listes de cache requises")
        if any(Path(p).is_absolute() or ".." in Path(p).parts for p in spec["dependances"]):
            raise ValueError("dépendance hors dépôt")
    files = {p.resolve() for pat in stage.get("entrees", []) for p in base.glob(pat) if p.is_file()}
    if spec is None:
        files.update(
            p
            for p in package.rglob("*")
            if p.suffix in (".py", ".toml", ".json", ".html", ".js")
            and not any(x.startswith(".") for x in p.relative_to(package).parts)
            and not p.name.startswith("test_")
        )
    else:
        files.update(p.resolve() for pat in spec["dependances"] for p in root.glob(pat) if p.is_file())
    scripts = set()
    commande = stage.get("commande", [])
    if "-m" in commande:
        index = commande.index("-m")
        if index + 1 < len(commande):
            module = commande[index + 1]
            if module.startswith("benchab."):
                cible = package.joinpath(*module.split(".")[1:]).with_suffix(".py")
                if cible.is_file():
                    scripts.add(cible.resolve())
                    files.add(cible.resolve())
    for arg in stage.get("commande", []):
        value = sys.executable if arg == "{python}" else str(base) if arg == "{projet}" else arg
        p = Path(value)
        p = p if p.is_absolute() else base / p
        try:
            if p.is_file():
                files.add(p.resolve())
                if p.suffix == ".py":
                    scripts.add(p.resolve())
            executable = shutil.which(value)
            if executable:
                files.add(Path(executable).resolve())
        except OSError:
            pass
    # Imports locaux usuels du script, y compris paramètres voisins : une
    # liste d'entrées trop courte ne doit pas cacher leur modification.
    pending = list(scripts | ({p for p in files if p.suffix == ".py"} if spec is not None else set()))
    seen = set()
    while pending:
        script = pending.pop()
        if script in seen:
            continue
        seen.add(script)
        try:
            tree = ast.parse(script.read_text())
        except (OSError, UnicodeError, SyntaxError):
            continue
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
                names.extend(node.module + "." + alias.name for alias in node.names)
        for name in names:
            candidates = [
                script.parent / (name.replace(".", "/") + ".py"),
                script.parent / name.replace(".", "/") / "__init__.py",
            ]
            if name.startswith("benchab."):
                relatif = package.joinpath(*name.split(".")[1:])
                candidates.extend([relatif.with_suffix(".py"), relatif / "__init__.py"])
            for candidate in candidates:
                if candidate.is_file() and candidate.resolve() not in files:
                    candidate = candidate.resolve()
                    files.add(candidate)
                    pending.append(candidate)
    if spec is None:
        if contexte.versions is None:
            contexte.versions = {d.metadata.get("Name", ""): d.version for d in importlib.metadata.distributions()}
        versions = contexte.versions
        environment = dict(os.environ)
        configuration = cfg
    else:
        versions = {}
        for name in spec["paquets"]:
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                versions[name] = None
        environment = {k: os.environ.get(k) for k in spec["environnement"]}
        configuration = {k: (cfg or {}).get(k) for k in spec["configuration"]} if "configuration" in spec else cfg
    data = {
        "machine": configuration_machine(),
        "fichiers": {str(p): contexte.empreinte(p) for p in sorted(files)},
        "stage": stage,
        "configuration": configuration,
        "python": sys.version,
        "executable": sys.executable,
        "dependances": versions,
        "environnement": environment,
    }
    # Aucun contenu d'environnement ni secret n'est écrit : seulement ce condensat.
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def statut_preuves(base, proofs):
    states = []
    for name in proofs:
        p = base / name
        if p.suffix == ".json":
            try:
                q = json.loads(p.read_text())
            except (OSError, ValueError):
                return "FAIL"
            # Les manifestes de données ne sont pas des rapports de contrôle.
            from benchab.qualite.validation import statuts

            if statuts(q):
                states.append({"statut": agregation_statut(q)})
    return agregation_statut(states) if states else "PASS"


def _verifier_destinations_etat(base, racine):
    # Les états sont réécrits atomiquement ; vérifier aussi chaque feuille évite
    # qu'un lien préexistant redirige leur lecture ou remplacement (docs/reference/chemins-et-atelier.md).
    for relatif in (
        "ETAT.md",
        "preuves/qualite/DIFFERENCES.md",
        "preuves/qualite/SYNTHESE.md",
        "preuves/qualite/dernier-etat.json",
        "preuves/qualite/executions",
        "preuves/qualite/echecs",
    ):
        sans_lien(base / relatif, racine=racine)


def run(path, incremental=False, only_verify=False):
    """Exécute le pipeline déclaré dans une racine privée ou temporaire approuvée.

    Entrées : configuration, options incrémentales ; sortie : code 0 PASS, 1 FAIL,
    2 attente humaine. Les chemins traversant un lien sont refusés et les identifiants
    d'étape restent des noms simples. Voir docs/reference/chemins-et-atelier.md.
    """
    demande = Path(path).expanduser()
    if not demande.is_absolute():
        demande = Path.cwd() / demande
    # Normaliser les alias /tmp et refuser tout lien avant de choisir une racine
    # de confiance ; le contrôle final restreint ensuite l'accès à l'atelier privé.
    candidate = sans_lien(demande, racine=Path(demande.anchor))
    racines = [atelier_prive()]
    if est_temporaire_hors_depot(candidate):
        racines.extend(sans_lien(r, racine=Path(r.anchor)) for r in (Path("/tmp"), Path(tempfile.gettempdir())))
    confiance = next((racine for racine in racines if candidate.is_relative_to(racine)), None)
    if confiance is None:
        raise ValueError("La configuration du pipeline doit rester dans l’atelier privé ou un temporaire reconnu")
    path = sans_lien(candidate, racine=confiance, strict=True)
    base = sans_lien(path.parent, racine=confiance, strict=True)
    out = sans_lien(base / "preuves/qualite", racine=confiance)
    cfg = json.loads(path.read_text())
    oldp = sans_lien(out / "dernier-etat.json", racine=confiance)
    old = json.loads(oldp.read_text()) if oldp.exists() else {}
    steps = []

    def enregistrer_etat(*args):
        _verifier_destinations_etat(base, confiance)
        return etat(*args)

    if only_verify:
        items = verifier(base, cfg)
        bad = [q for q in items if q["statut"] not in ("PASS", "WARNING")]
        print(json.dumps(dict(statut="FAIL" if bad else agregation_statut(items), controles=items), indent=2))
        return 1 if bad else 0
    if protege(sans_lien(base / "ETAT.md", racine=confiance)):
        raise RuntimeError("Le projet contient un ETAT figé : utiliser une nouvelle version")
    pipeline = cfg.get("pipeline", [])
    if not pipeline:
        enregistrer_etat(base, cfg, steps, old, "FAIL", ["pipeline non déclaré"])
        return 1
    ids = []
    for stage in pipeline:
        ident = stage.get("id") if isinstance(stage, dict) else None
        if (
            not isinstance(ident, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", ident) is None
            or ident in {".", ".."}
        ):
            raise ValueError("Identifiant d’étape invalide : un nom simple est requis")
        ids.append(ident)
    if len(ids) != len(set(ids)):
        raise ValueError("Identifiants d’étape dupliqués")
    # Le dossier de preuves peut contenir d'anciens sous-dossiers : aucun lien
    # interne ne doit détourner les journaux ou états (docs/reference/controles.md).
    out = sans_lien(out, racine=confiance)
    out.mkdir(parents=True, exist_ok=True)
    cache = {s["id"]: s for s in old.get("steps", [])}
    contexte = ContexteCache()
    try:
        for stage in pipeline:
            ident = stage["id"]
            entry = empreinte(base, stage, cfg, contexte)
            prior = cache.get(ident, {})
            proofs = stage.get("preuves", [])
            can_skip = (
                incremental
                and bool(proofs)
                and bool(stage.get("entrees"))
                and prior.get("entrees_sha256") == entry
                and prior.get("statut") == "PASS"
                and all((base / p).exists() and prior.get("preuves_sha256", {}).get(p) == sha(base / p) for p in proofs)
            )
            step = dict(id=ident, statut="RUNNING", entrees_sha256=entry)
            steps.append(step)
            enregistrer_etat(base, cfg, steps, old, "RUNNING", [])
            print("DEBUT", ident, flush=True)
            if can_skip:
                step.update(
                    statut="PASS", cache=True, code=prior.get("code", 0), preuves_sha256=prior["preuves_sha256"]
                )
                continue
            cmd = [
                sys.executable if a == "{python}" else str(base) if a == "{projet}" else a for a in stage["commande"]
            ]
            t = time.monotonic()
            journal = sans_lien(out / (ident + ".log"), racine=confiance)
            sans_lien(out / "processus", racine=confiance)
            code = executer_commande(cmd, base, journal, stage)
            step.update(
                statut="PASS" if code == 0 else "FAIL",
                code=code,
                secondes=round(time.monotonic() - t, 2),
                preuves_sha256={p: sha(base / p) for p in proofs if (base / p).exists()},
            )
            if code == 0:
                step["statut"] = statut_preuves(base, proofs)
            missing = [p for p in proofs if not (base / p).exists()]
            if missing:
                step["statut"] = "FAIL"
            print("FIN", ident, step["statut"], flush=True)
            if step["statut"] not in ("PASS", "WARNING"):
                enregistrer_etat(
                    base,
                    cfg,
                    steps,
                    old,
                    "FAIL",
                    [ident + ": voir " + str(out / (ident + ".log"))] + missing,
                )
                return 1
        numeric_items = verifier(base, cfg, humain=False)
        numeric = [q for q in numeric_items if q["statut"] not in ("PASS", "WARNING")]
        if numeric:
            enregistrer_etat(
                base, cfg, steps, old, "FAIL", [q["controle"] + ": " + q["detail"] for q in numeric]
            )
            return 1
        human = [q for q in verifier(base, cfg, humain=True) if q["statut"] not in ("PASS", "WARNING")]
        enregistrer_etat(
            base,
            cfg,
            steps,
            old,
            "ATTENTE HUMAINE"
            if human
            else "WARNING"
            if any(s["statut"] == "WARNING" for s in steps + numeric_items)
            else "PASS",
            [q["controle"] + ": " + q["detail"] for q in human],
        )
        return 2 if human else 0
    except Exception as e:
        enregistrer_etat(base, cfg, steps, old, "FAIL", [repr(e)])
        raise


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("configuration")
    p.add_argument("--incremental", action="store_true")
    p.add_argument("--verifier", action="store_true")
    a = p.parse_args(argv)
    return run(a.configuration, a.incremental, a.verifier)


if __name__ == "__main__":
    raise SystemExit(main())
