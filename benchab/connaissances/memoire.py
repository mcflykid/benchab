"""Recherche et capitalisation déterministes ; aucune propriété physique inférée."""

import argparse
import datetime
import hashlib
import json
import os
import re
import select
import sqlite3
import subprocess
import sys
import tarfile
import time
import unicodedata
from pathlib import Path

from benchab.chemins import atelier_prive

TYPES = ("impression", "defaut", "correction", "mesure", "decision", "audit", "recherche")
STATUTS = ("ACTIF", "CAPITALISÉ", "HISTORIQUE", "REDONDANT", "À EXAMINER")
MAX_BLOB_GIT_OCTETS = 256 * 1024 * 1024
DELAI_BLOB_GIT_SECONDES = 10


def normaliser(s):
    return "".join(c for c in unicodedata.normalize("NFKD", str(s).casefold()) if not unicodedata.combining(c))


def sha(p):
    h = hashlib.sha256()
    with Path(p).open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def charger(root):
    from benchab.connaissances import sqlite

    if sqlite.native(root):
        return sqlite.charger(root)
    return json.loads((Path(root) / "donnees/connaissances/registre.json").read_text())


def sauver(root, data):
    from benchab.connaissances import sqlite

    if sqlite.native(root):
        return sqlite.sauver(root, data)
    path = Path(root) / "donnees/connaissances/registre.json"
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path.read_text() == payload:
        return
    from benchab.qualite.ecritures import modifier_json

    # Relire sous verrou, refus si la base a changé depuis la lecture de l'appelant.
    def update(current):
        if current["connaissances"] != data["connaissances"]:
            raise ValueError("connaissances modifiées concurremment")
        for rel, record in data["journaux"].items():
            prior = current.get("journaux", {}).get(rel, {})
            if prior.get("sha256") == record["sha256"] and not record["connaissances"]:
                record["statut"] = prior.get("statut", record["statut"])
        current["journaux"] = data["journaux"]

    modifier_json(path, update)


def chercher(root, texte="", filtres=None, donnees=None):
    filtres = filtres or {}
    result = []
    for item in (donnees if donnees is not None else charger(root))["connaissances"]:
        if item.get("validite") == "remplacee" and not filtres.get("historique"):
            continue
        hay = normaliser(json.dumps(item, ensure_ascii=False))
        if any(normaliser(word) not in hay for word in texte.split()):
            continue
        if any(
            normaliser(v) not in normaliser(json.dumps(item.get(k, ""), ensure_ascii=False))
            for k, v in filtres.items()
            if k != "historique"
        ):
            continue
        result.append(item)
    return result


def creer_journal(root, type_, sujet, projet="", date=None):
    root = Path(root).resolve()
    date = date or datetime.date.today().isoformat()
    datetime.date.fromisoformat(date)
    if type_ not in TYPES or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", sujet):
        raise ValueError("type ou sujet invalide")
    folder = root / "suivi/journal" / date[:7]
    if any(p.is_symlink() for p in (root / "suivi", root / "suivi/journal", folder)):
        raise ValueError("journal symbolique refusé")
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{date}-{type_}-{sujet}.md"
    text = (
        f"# {type_.capitalize()} — {sujet}\n\nDate : {date}\nType : {type_}\nSujet : {sujet}\n"
        f"Projet concerné : {projet or 'sans projet'}\nStatut : À COMPLÉTER\nNiveau de preuve : À VÉRIFIER\n"
        "\n## Contexte\n\n## Observation ou problème\n\n## Résultat\n\n## Connaissance réutilisable\n"
        "\n## Limites\n\n## Sources et preuves\n\n## Action de capitalisation\n\n"
        "`benchab connaissances memoire capitaliser` propose les rattachements ; "
        "aucune validation physique automatique.\n"
    )
    # Numéro stable alloué exclusivement, jamais écraser une entrée existante.
    for i in range(1, 1000):
        target = p if i == 1 else p.with_stem(p.stem + f"-{i:02d}")
        try:
            with target.open("x") as f:
                f.write(text)
            return target
        except FileExistsError:
            continue
    raise ValueError("trop d’événements pour ce sujet")


def analyser_journaux(root):
    root = Path(root)
    data = charger(root)
    previous = data.get("journaux", {})
    out = {}
    propositions = []
    for p in sorted((root / "suivi/journal").rglob("*.md")):
        if any(x in {"preparations", "sources", "sauvegardes", "sources-code"} for x in p.relative_to(root).parts):
            continue
        rel = p.relative_to(root).as_posix()
        h = sha(p)
        text = p.read_text()
        prev = previous.get(rel, {})
        refs = [
            i["id"]
            for i in data["connaissances"]
            if any(s.get("chemin", "").split("#")[0] == rel for s in i.get("sources", []))
        ]
        title = next((s.lstrip("# ").strip() for s in text.splitlines() if s.startswith("#")), p.stem)
        status = prev.get("statut", "À EXAMINER") if prev.get("sha256") == h else "À EXAMINER"
        if refs and status != "CAPITALISÉ":
            status = "ACTIF"
        # Conserver la qualification antérieure uniquement si la source est identique.
        declarations = []
        if re.search(r"^Type : impression$", text, re.M):
            for row in text.splitlines():
                if not row.startswith("|"):
                    continue
                cells = [v.strip() for v in row.strip("| ").split("|")]
                if len(cells) >= 2 and cells[0].isdigit() and normaliser(cells[1]) in ("reussie", "defaut", "arretee"):
                    declarations.append(
                        {"plaque": cells[0], "resultat": cells[1], "niveau_preuve": "OBSERVATION_UTILISATEUR"}
                    )
        out[rel] = {"titre": title, "sha256": h, "statut": status, "connaissances": refs}
        if prev.get("sha256") == h:
            for key in ("qualification", "revue"):
                if key in prev:
                    out[rel][key] = prev[key]
        if declarations:
            out[rel]["resultats_declares"] = declarations

        if prev.get("sha256") != h:
            targets = [
                i["id"]
                for i in data["connaissances"]
                if any(w in normaliser(title) for w in normaliser(i["titre"]).split() if len(w) > 5)
            ]
            propositions.append(
                {
                    "journal": rel,
                    "statut": "À VALIDER",
                    "rattachements_possibles": targets[:5],
                    "faits_declares": declarations,
                    "raison": (
                        "nouvelle source ou source modifiée ; rapprochement lexical, "
                        "pas une propriété mécanique validée"
                    ),
                }
            )
    return out, propositions


def _charger_lecture_seule(root):
    """Lit SQLite en mode ro sans déclencher la reprise du registre historique."""
    from benchab.connaissances import sqlite as adaptation
    from benchab.donnees.base import APPLICATION_ID
    from benchab.qualite.ecritures import _sans_lien

    root = _sans_lien(Path(root).expanduser().absolute())
    base = root / "benchab.sqlite"
    if base.exists() or base.is_symlink():
        base = _sans_lien(base).resolve(strict=True)
        connexion = sqlite3.connect(base.as_uri() + "?mode=ro", uri=True)
        connexion.row_factory = sqlite3.Row
        try:
            connexion.execute("PRAGMA query_only=ON")
            if connexion.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise ValueError("La base mémoire n'est pas une base BENCHAB.")
            donnees = adaptation.lire_connexion(connexion)
            index_present = connexion.execute("SELECT 1 FROM decisions WHERE id=?", (adaptation.INDEX,)).fetchone()
            registre = root / "donnees/connaissances/registre.json"
            if not index_present and registre.is_file():
                donnees["erreurs_lecture"] = [
                    "Registre historique présent mais non repris : contrôle strictement en lecture seule"
                ]
            return donnees
        finally:
            connexion.close()
    registre = _sans_lien(root / "donnees/connaissances/registre.json")
    if registre.is_file():
        return json.loads(registre.read_text(encoding="utf-8"))
    raise FileNotFoundError("Base canonique ou registre mémoire absent")


def _chemin_source(root, relatif):
    """Résout une source relative dans la racine de connaissance, en refusant liens et traversée."""
    from benchab.qualite.ecritures import _sans_lien

    path = Path(relatif)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("source hors racine privée")
    cible = root / path
    if any(part.is_symlink() for part in (cible, *cible.parents) if part != root.parent):
        raise ValueError("source symbolique refusée")
    return _sans_lien(cible).resolve(strict=False)



def _empreinte_blob_git(racine, objet, env):
    from benchab.processus import _terminer

    commande = ["git", "-C", str(racine), "cat-file", "blob", objet]
    fin = time.monotonic() + DELAI_BLOB_GIT_SECONDES
    digest = hashlib.sha256()
    taille = 0
    with subprocess.Popen(commande, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          env=env, start_new_session=True) as processus:
        try:
            while True:
                restant = fin - time.monotonic()
                # Attendre les octets avec le même délai global : read() seul
                # attendrait indéfiniment un Git bloqué (docs/reference/donnees.md).
                if restant <= 0 or not select.select([processus.stdout], [], [], restant)[0]:
                    raise subprocess.TimeoutExpired(commande, DELAI_BLOB_GIT_SECONDES)
                bloc = os.read(processus.stdout.fileno(), 1024 * 1024)
                if not bloc:
                    break
                taille += len(bloc)
                if taille > MAX_BLOB_GIT_OCTETS:
                    raise ValueError("blob Git dépasse la limite de lecture")
                digest.update(bloc)
            return digest.hexdigest(), processus.wait(timeout=max(0.001, fin - time.monotonic()))
        finally:
            _terminer(processus, 0.25)


def _verifier_fichier_source(root, item_id, source, erreurs, non_verifies):
    """Contrôle SHA fichier/blob; dépassement de 256 MiB FAIL, SHA absent NON VÉRIFIÉ, sans validation physique."""
    if source.get("reference_paquet"):
        from benchab.donnees.reference import verifier_source

        if not verifier_source(source):
            erreurs.append(item_id + " : référence livrée absente ou modifiée")
        return
    rel = source.get("chemin", "").split("#", 1)[0]
    try:
        if source.get("revision_originale"):
            # Git « -e » ne vérifie que l'existence; le blob exact est haché en flux pour respecter les SHA historiques
            # sans charger un modèle entier en mémoire (docs/reference/donnees.md).
            env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_OPTIONAL_LOCKS="0")
            revision = source["revision_originale"]
            if (
                not isinstance(revision, str)
                or not re.fullmatch(r"[a-fA-F0-9]{40,64}", revision)
                or Path(rel).is_absolute()
                or ".." in Path(rel).parts
            ):
                erreurs.append(item_id + " : identifiant de provenance Git invalide " + rel)
                return
            try:
                digest, code = _empreinte_blob_git(root, revision + ":" + rel, env)
            except subprocess.TimeoutExpired:
                erreurs.append(item_id + " : délai de lecture du blob Git dépassé " + rel)
                return
            except (OSError, ValueError) as exc:
                erreurs.append(item_id + " : lecture du blob Git impossible " + rel + " : " + str(exc))
                return
            if code:
                erreurs.append(item_id + " : provenance Git absente " + rel)
            elif source.get("sha256") and digest != source["sha256"]:
                erreurs.append(item_id + " : blob Git modifié " + rel)
            elif not source.get("sha256"):
                non_verifies.append(item_id + " : blob Git existant sans SHA de contenu " + rel)
            return
        cible = _chemin_source(root, rel)
        if not cible.is_file():
            erreurs.append(item_id + " : source absente " + rel)
        elif source.get("sha256") and sha(cible) != source["sha256"]:
            erreurs.append(item_id + " : source modifiée " + rel)
        elif not source.get("sha256"):
            non_verifies.append(item_id + " : source sans SHA " + rel)
        if cible.is_file() and source.get("membre"):
            with tarfile.open(cible, "r:*") as archive:
                member = archive.getmember(source["membre"])
                if not member.isfile():
                    raise ValueError("membre de source non régulier")
                stream = archive.extractfile(member)
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != source.get("sha256_membre"):
                    erreurs.append(item_id + " : membre absent ou modifié " + source["membre"])
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        erreurs.append(item_id + " : source invalide " + rel + " : " + type(exc).__name__)


def controler(root):
    """Retourne PASS/FAIL/NON VÉRIFIÉ et sépare intégrité des extraits et fidélité historique, sans écriture."""
    from benchab.connaissances import provenance
    from benchab.connaissances.etudes import verifier as verifier_etudes

    erreurs = []
    non_verifies = []
    try:
        root = Path(root).expanduser().absolute()
        if any(part.is_symlink() for part in (root, *root.parents)):
            raise ValueError("Racine mémoire liée refusée")
        root = root.resolve(strict=True)
        donnees = _charger_lecture_seule(root)
    except (OSError, ValueError, sqlite3.Error) as exc:
        erreur = "Lecture mémoire impossible : " + type(exc).__name__
        return {
            "statut": "FAIL",
            "integrite_extraits": {"statut": "FAIL", "erreurs": [erreur]},
            "fidelite_historique": {"statut": "NON VÉRIFIÉ", "sources": []},
            "sources": {"statut": "FAIL", "erreurs": [erreur]},
            "erreurs": [erreur],
        }
    if not isinstance(donnees, dict) or not isinstance(donnees.get("connaissances"), list):
        erreur = "Projection mémoire mal formée"
        return {
            "statut": "FAIL",
            "integrite_extraits": {"statut": "FAIL", "erreurs": [erreur]},
            "fidelite_historique": {"statut": "NON VÉRIFIÉ", "sources": []},
            "sources": {"statut": "FAIL", "erreurs": [erreur]},
            "erreurs": [erreur],
        }
    erreurs.extend(verifier_etudes(donnees))
    erreurs.extend(donnees.get("erreurs_lecture", []))
    ids = set()
    for item in donnees.get("connaissances", []):
        if not isinstance(item, dict):
            erreurs.append("enregistrement de connaissance mal formé")
            continue
        identifiant = item.get("id")
        if not isinstance(identifiant, str) or not identifiant or identifiant in ids:
            erreurs.append("identifiant de connaissance absent ou dupliqué")
        else:
            ids.add(identifiant)
        for cle in ("titre", "domaine", "conditions", "exclusions", "niveau_preuve", "sources", "validite"):
            if not item.get(cle):
                erreurs.append(str(identifiant) + " : champ absent " + cle)

    conversion = provenance.controler_contrats(donnees, root)
    erreurs.extend(conversion["erreurs"])
    conversions_valides = set()
    if conversion["statut"] == "PASS":
        for ligne in conversion["conversions"]:
            if ligne["integrite_extraits"] == "PASS":
                try:
                    conversions_valides.add(provenance._source(ligne["source_originale"]))
                except (KeyError, TypeError, ValueError):
                    continue
    erreurs_sources = []
    copies_converties = []
    for item in donnees.get("connaissances", []):
        identifiant = str(item.get("id", "?"))
        copies = item.get("sources_copies", [])
        erreurs_copies = item.get("sources_copies_erreurs", [])
        erreurs_sources.extend(identifiant + " : " + e for e in erreurs_copies)
        originales = item.get("sources_originales")
        if not isinstance(originales, list):
            originales = item.get("sources", [])
        if not isinstance(originales, list):
            erreurs_sources.append(identifiant + " : liste de sources mal formée")
            continue
        copiees = set()
        for copie in copies:
            if not isinstance(copie, dict):
                erreurs_sources.append(identifiant + " : déclaration de copie mal formée")
                continue
            try:
                candidats = [
                    s for s in originales
                    if isinstance(s, dict)
                    and not s.get("reference_paquet")
                    and provenance._source(s)[0].split("#", 1)[0] == copie.get("source_originale")
                ]
                candidats = [s for s in candidats if _copie_correspond(copie, s)]
                if len(candidats) != 1:
                    raise ValueError("copie non rattachée à une version unique source" )
                source_copie = candidats[0]
                identite = provenance._source(source_copie)
                if identite in copiees:
                    raise ValueError("copie source déclarée en double")
                copiees.add(identite)
                conversion_exacte = (
                    conversion["statut"] == "PASS"
                    and identite in conversions_valides
                    and identite[0].startswith("suivi/journal/")
                )
                if copie.get("statut") == "convertie_en_faits" and not conversion_exacte:
                    raise ValueError("marque convertie_en_faits sans contrat valide pour cette version source")
                if conversion_exacte:
                    # L'extrait porte la version déclarée; une copie d'archive discordante ne devient pas une preuve
                    # de cette version (docs/reference/donnees.md).
                    copie["statut"] = "convertie_en_faits"
                    copies_converties.append({
                        "source_originale": copie.get("source_originale"),
                        "destination": copie.get("destination"),
                        "statut": "convertie_en_faits",
                        "integrite_extraits": "PASS",
                    })
                    if copie.get("ecart_sha_historique"):
                        non_verifies.append(identifiant + " : copie d'archive différente du SHA historique déclaré")
                    continue
                if copie.get("ecart_sha_historique"):
                    raise ValueError("copie différente du SHA de la version source déclarée")
                sha_actuel = copie.get("sha256_actuel")
                if not isinstance(sha_actuel, str) or not re.fullmatch(r"[a-f0-9]{64}", sha_actuel):
                    raise ValueError("SHA actuel de copie invalide")
                destination = _chemin_source(root, copie.get("destination", ""))
                if not destination.is_file() or sha(destination) != sha_actuel:
                    raise ValueError("copie absente ou SHA actuel différent")
            except (OSError, TypeError, ValueError) as exc:
                erreurs_sources.append(identifiant + " : copie invalide : " + str(exc))
        identites_sources = set()
        for source in originales:
            if isinstance(source, str):
                source = {"chemin": source}
            if not isinstance(source, dict):
                erreurs_sources.append(identifiant + " : source mal formée")
                continue
            if source.get("reference_paquet"):
                _verifier_fichier_source(root, identifiant, source, erreurs_sources, non_verifies)
                continue
            try:
                cle_source = provenance._source(source)
            except ValueError as exc:
                erreurs_sources.append(identifiant + " : source sans identité exacte : " + str(exc))
                continue
            if cle_source in identites_sources:
                erreurs_sources.append(identifiant + " : source déclarée en double")
                continue
            identites_sources.add(cle_source)
            if cle_source in conversion["sources_converties"] or cle_source in copiees:
                if source.get("origine_sha") == "copie_source_capturee_import":
                    non_verifies.append(identifiant + " : SHA capturée à l'import; source originale NON VÉRIFIÉE")
                continue
            _verifier_fichier_source(root, identifiant, source, erreurs_sources, non_verifies)
    erreurs.extend(erreurs_sources)
    erreurs_journaux = []
    for relatif, info in donnees.get("journaux", {}).items():
        if not isinstance(info, dict) or info.get("statut") not in STATUTS:
            erreurs_journaux.append("statut de journal invalide : " + str(relatif))
            continue
        if (relatif, info.get("sha256")) in conversion["journaux_ecartes"]:
            non_verifies.append(relatif + " : original explicitement écarté; fidélité historique NON VÉRIFIÉE")
            continue
        if (relatif, info.get("sha256")) in conversion["journaux_convertis"]:
            continue
        try:
            cible = _chemin_source(root, relatif)
            if not cible.is_file() or sha(cible) != info.get("sha256"):
                erreurs_journaux.append("capitalisation périmée : " + relatif)
        except (OSError, ValueError) as exc:
            erreurs_journaux.append("capitalisation invalide : " + relatif + " : " + type(exc).__name__)
    erreurs.extend(erreurs_journaux)
    stat_source = "FAIL" if erreurs_sources or erreurs_journaux else "NON VÉRIFIÉ" if non_verifies else "PASS"
    int_extraits = {
        "statut": conversion["statut"],
        "erreurs": conversion["erreurs"],
        "nombre_conversions": len(conversion["conversions"]),
    }
    fidelity = {
        "statut": conversion["fidelite_historique"],
        "sources": conversion["conversions"],
        "index_ecartes": conversion["index_ecartes"],
        "raison": "Aucune empreinte actuelle ne prouve rétroactivement le contenu historique original.",
    }
    non_verifie = conversion["fidelite_historique"] == "NON VÉRIFIÉ" or bool(non_verifies)
    statut = "FAIL" if erreurs else "NON VÉRIFIÉ" if non_verifie else "PASS"
    return {
        "statut": statut,
        "integrite_extraits": int_extraits,
        "fidelite_historique": fidelity,
        "sources": {
            "statut": stat_source, "erreurs": erreurs_sources + erreurs_journaux,
            "non_verifies": non_verifies, "copies_converties": copies_converties,
        },
        "erreurs": erreurs,
    }


def _copie_correspond(copie, source):
    """Compare les identifiants de version historiques d'une copie, jamais le seul chemin."""
    chemin = source.get("chemin", "").split("#", 1)[0]
    revision = source.get("revision_originale", source.get("revision_source_originale"))
    digest = source.get("sha256")
    if copie.get("source_originale") != chemin:
        return False
    copie_revision = copie.get("revision_originale", copie.get("revision_source_originale"))
    copie_digest = copie.get(
        "sha256_originale",
        copie.get(
            "sha256_source_originale",
            copie.get("sha_source_originale", copie.get("sha256_source_attendu")),
        ),
    )
    if source.get("origine_sha") == "copie_source_capturee_import":
        return copie.get("sha256_actuel") == source.get("sha256_capturee_import")
    if revision is None and digest is None:
        return False
    return (revision is None or revision == copie_revision) and (digest is None or digest == copie_digest)


def verifier(root):
    """Compatibilité liste d'erreurs ; lecture seule, sans migration SQLite ni affirmation de fidélité physique."""
    rapport = controler(root)
    return rapport["erreurs"]


def contradictions(root):
    """Conflits déterministes seulement sur des faits structurés comparables."""
    facts = {}
    out = []
    for item in charger(root)["connaissances"]:
        for key, value in item.get("faits", {}).items():
            group = (item.get("comparabilite"), key)
            if not group[0]:
                continue
            old = facts.setdefault(group, (value, item["id"]))
            if old[0] != value:
                out.append(
                    {
                        "champ": key,
                        "ids": [old[1], item["id"]],
                        "statut": "À ARBITRER",
                        "raison": "valeurs différentes pour la même comparabilité déclarée",
                    }
                )
    return out


def resoudre_chemin(root, texte):
    """Retrouver les reçus figés sans réécrire leurs chemins d’observation."""
    p = Path(root) / "docs/architecture.json"
    if not p.is_file():
        return []
    d = json.loads(p.read_text())
    rows = []
    if d.get("historique"):
        h = Path(root) / d["historique"]
        if not h.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError("historique hors dépôt")
        if not h.is_file() or sha(h) != d.get("sha256_historique"):
            raise ValueError("historique absent ou modifié")
        d = {**d, **json.loads(h.read_text())}
    for item in d.get("migrations", []):
        if texte and texte in item["avant"]:
            rows.append({"avant": item["avant"], "apres": item["apres"], "statut": "MIGRÉ"})
    for item in d.get("absorptions", []):
        if texte and texte in item["avant"]:
            rows.append(
                {
                    "avant": item["avant"],
                    "apres": item.get("destination"),
                    "statut": "ABSORBÉ",
                    "raison": item["raison"],
                }
            )
    return rows


def main(argv=None, *, racine=None):
    """Recherche/capitalise dans l’atelier choisi ; dates ISO et statuts déclarés, jamais de validation
    physique inventée."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--racine", type=Path, default=atelier_prive(racine))
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("chercher")
    q.add_argument("texte", nargs="?", default="")
    for key in ("filament", "materiau", "projet", "geometrie", "domaine", "niveau_preuve", "date_observation"):
        q.add_argument("--" + key.replace("_", "-"))
    q.add_argument("--historique", action="store_true")
    q = sub.add_parser("sources")
    q.add_argument("texte", nargs="?", default="")
    q.add_argument("--limite", type=int, default=20)
    sub.add_parser("verifier")
    c = sub.add_parser("capitaliser")
    c.add_argument("--appliquer", action="store_true")
    j = sub.add_parser("journal")
    j.add_argument("type", choices=TYPES)
    j.add_argument("sujet")
    j.add_argument("--projet", default="")
    a = p.parse_args(argv)
    try:
        if a.cmd == "sources":
            if not 1 <= a.limite <= 1000:
                raise ValueError("limite comprise entre 1 et 1000")
            sources = charger(a.racine).get("documents_sources", {})
            rows = [x for x in sources.get("membres", []) if normaliser(a.texte) in normaliser(x["membre"])]
            result = {
                "archive": sources.get("archive"),
                "correspondances": len(rows),
                "entrees": rows[: a.limite],
                "verification": sources.get("verification"),
                "chemins_migres": resoudre_chemin(a.racine, a.texte),
            }
        elif a.cmd == "chercher":
            filters = {
                k: getattr(a, k)
                for k in ("filament", "materiau", "projet", "geometrie", "domaine", "niveau_preuve", "date_observation")
                if getattr(a, k)
            }
            if a.historique:
                filters["historique"] = True
            result = chercher(a.racine, a.texte, filters)
        elif a.cmd == "journal":
            result = {"journal": str(creer_journal(a.racine, a.type, a.sujet, a.projet))}
        elif a.cmd == "verifier":
            errors = verifier(a.racine)
            print(json.dumps({"ok": not errors, "erreurs": errors}, ensure_ascii=False, indent=2))
            return int(bool(errors))
        else:
            records, proposals = analyser_journaux(a.racine)
            if a.appliquer:
                data = charger(a.racine)
                data["journaux"] = records
                sauver(a.racine, data)
            result = {
                "journaux": len(records),
                "propositions": proposals,
                "contradictions": contradictions(a.racine),
                "application": "empreintes et rattachements explicites seulement" if a.appliquer else "simulation",
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as e:
        print("REFUSÉ : " + str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
