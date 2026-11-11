"""Adaptateur du registre historique vers la base canonique de l'atelier."""

import json
import sqlite3
from pathlib import Path

from benchab.donnees.base import APPLICATION_ID, Base
from benchab.donnees.reference import source_livree
from benchab.qualite.ecritures import _sans_lien

INDEX = "memoire:index-journaux"
CONTRATS_REPRISE = "savoir:contrats_sources_reprises"


def _correspondance_copie(source, copie):
    """Exige l'identité exacte chemin + révision/SHA ; un chemin seul n'identifie pas une version."""
    chemin = source.get("chemin", "").split("#", 1)[0]
    source_revision = source.get("revision_originale", source.get("revision_source_originale"))
    source_sha = source.get("sha256")
    copie_chemin = copie.get("source_originale")
    copie_revision = copie.get("revision_originale", copie.get("revision_source_originale"))
    copie_sha = copie.get(
        "sha256_originale",
        copie.get(
            "sha256_source_originale", copie.get("sha_source_originale", copie.get("sha256_source_attendu"))
        ),
    )
    if copie_chemin != chemin:
        return False
    if source.get("origine_sha") == "copie_source_capturee_import":
        return copie.get("sha256_actuel") == source.get("sha256_capturee_import")
    return (source_revision is None or copie_revision == source_revision) and (
        source_sha is None or copie_sha == source_sha
    )


def _projeter_sources(sources_brutes, copies_brutes):
    """Préserve les sources versionnées; copie d'archive discordante reste hors source courante."""
    erreurs = []
    if not isinstance(sources_brutes, list):
        return [], [], ["liste sources originale mal formée"]
    if not isinstance(copies_brutes, list):
        return [s if isinstance(s, dict) else {"chemin": s} for s in sources_brutes], [], [
            "liste sources_copies mal formée"
        ]
    originales, courantes = [], []
    for valeur in sources_brutes:
        source = dict(valeur) if isinstance(valeur, dict) else {"chemin": valeur}
        chemin = source.get("chemin", "").split("#", 1)[0]
        meme_chemin = [
            copie for copie in copies_brutes
            if isinstance(copie, dict)
            and copie.get("source_originale") == chemin
        ]
        candidates = [
            copie for copie in meme_chemin
            if _correspondance_copie(source, copie)
        ]
        # Une source historique textuelle sans SHA/révision se résout seulement si un seul reçu de copie existe.
        if (
            source.get("sha256") is None
            and source.get("revision_originale") is None
            and source.get("origine_sha") != "copie_source_capturee_import"
        ):
            candidates = meme_chemin
        if len(candidates) > 1:
            erreurs.append("plusieurs versions de copie pour " + str(chemin))
            originales.append(source)
            courantes.append(source)
            continue
        if not candidates:
            for copie in meme_chemin:
                attendu = copie.get(
                    "sha256_source_attendu",
                    copie.get("sha256_originale", copie.get("sha256_source_originale")),
                )
                if (source.get("sha256") and attendu != source["sha256"]) or (
                    attendu is not None and copie.get("sha256_actuel") != attendu
                ):
                    copie["ecart_sha_historique"] = {
                        "sha256_attendu": attendu,
                        "sha256_source": source.get("sha256"),
                        "sha256_actuel": copie.get("sha256_actuel"),
                    }
            originales.append(source)
            courantes.append(source)
            continue
        copie = candidates[0]
        original_sha = copie.get(
            "sha256_originale",
            copie.get(
                "sha256_source_originale", copie.get("sha_source_originale", copie.get("sha256_source_attendu"))
            ),
        )
        revision = copie.get("revision_originale", copie.get("revision_source_originale"))
        actuel_sha = copie.get("sha256_actuel")
        decalage = (
            (source.get("sha256") is not None and original_sha != source["sha256"])
            or (source.get("revision_originale") is not None and revision != source["revision_originale"])
            or (original_sha is not None and actuel_sha != original_sha)
        )
        if decalage:
            # La copie fautive ne remplace pas le blob historique; sa divergence reste une limite rapportable
            # (docs/reference/donnees.md).
            copie["ecart_sha_historique"] = {
                "sha256_attendu": original_sha,
                "sha256_source": source.get("sha256"),
                "sha256_actuel": actuel_sha,
            }
        if source.get("sha256") is None and original_sha is not None:
            source["sha256"] = original_sha
            source["origine_sha"] = "source_attendue"
        elif source.get("sha256") is None and actuel_sha is not None:
            source["origine_sha"] = "copie_source_capturee_import"
            source["sha256_capturee_import"] = actuel_sha
        if source.get("revision_originale") is None and revision is not None:
            source["revision_originale"] = revision
        if (
            source.get("sha256") is None
            and source.get("revision_originale") is None
            and source.get("origine_sha") != "copie_source_capturee_import"
        ):
            erreurs.append("copie sans identité historique SHA/révision pour " + str(chemin))
            originales.append(source)
            courantes.append(source)
            continue
        originales.append(source)
        if decalage:
            courantes.append(source)
        else:
            courant = dict(source)
            courant["chemin"] = copie.get("destination")
            courant["sha256"] = copie.get("sha256_actuel")
            if "revision_originale" in courant:
                courant["revision_source_originale"] = courant.pop("revision_originale")
            courantes.append(courant)
    return originales, courantes, erreurs


def native(root):
    """Indique si un fichier de base est présent, sans qualifier son contenu."""
    return (Path(root) / "benchab.sqlite").exists()


def lire_connexion(connexion):
    """Projette une connexion SQLite ouverte en lecture.

    Statuts NON VÉRIFIÉ conservés; aucune unité transformée ni écriture.
    """
    contrat_ligne = connexion.execute("SELECT donnees_json FROM decisions WHERE id=?", (CONTRATS_REPRISE,)).fetchone()
    contrat = json.loads(contrat_ligne[0]) if contrat_ligne else None
    connaissances = []
    for ligne in connexion.execute("SELECT * FROM connaissances ORDER BY id"):
        donnees = json.loads(ligne["donnees_json"])
        if not isinstance(donnees, dict):
            raise ValueError("Métadonnées de connaissance non structurées : " + ligne["id"])
        original = donnees.get("registre_original")
        if isinstance(original, dict):
            if contrat is not None:
                original = {
                    **original,
                    "id_canonique": ligne["id"],
                    "texte_canonique": ligne["texte"],
                    "source_canonique": ligne["source"],
                    "categorie_canonique": ligne["categorie"],
                    "date_source_canonique": ligne["date_source"],
                    "preuve_canonique": donnees.get("preuve", original.get("preuve")),
                }
            copies_brutes = donnees.get("sources_copies", [])
            sources_brutes = original.get("sources_originales", original.get("sources", []))
            sources_originales, sources, erreurs_copies = _projeter_sources(sources_brutes, copies_brutes)
            copies = copies_brutes if isinstance(copies_brutes, list) else []
            if not copies and not erreurs_copies:
                connaissances.append(original)
                continue
            original = {
                **original,
                "sources": sources,
                "sources_originales": sources_originales,
                "sources_copies": copies,
                "sources_copies_erreurs": erreurs_copies,
            }
            connaissances.append(original)
            continue
        copies_brutes = donnees.get("sources_copies", [])
        # Les configurations archivées ont une déclaration d'origine séparée
        # du chemin canonique de leur copie (docs/reference/donnees.md).
        defaut_sources = [donnees.get("source_originale", ligne["source"])]
        sources_brutes = donnees.get("sources", defaut_sources)
        sources_originales, sources, erreurs_copies = _projeter_sources(sources_brutes, copies_brutes)
        livree = source_livree(ligne) if ligne["public"] else None
        if livree:
            sources = [livree]
            sources_originales = [livree]
            copies = []
            erreurs_copies = []
        else:
            copies = copies_brutes if isinstance(copies_brutes, list) else []
        entree = {
            **donnees, "id": ligne["id"], "titre": ligne["texte"][:120], "texte": ligne["texte"],
            "domaine": donnees.get("domaine", ligne["categorie"]),
            "niveau_preuve": donnees.get("niveau_preuve", ligne["categorie"].upper()),
            "conditions": donnees.get("conditions", "Applicabilité non qualifiée ; relire les sources citées."),
            "exclusions": donnees.get("exclusions", "Aucune généralisation automatique à une autre pièce."),
            "validite": donnees.get("validite", "a_examiner"), "sources": sources,
        }
        if copies or erreurs_copies or contrat is not None:
            entree.update(
                sources_originales=sources_originales,
                sources_copies=copies,
                sources_copies_erreurs=erreurs_copies,
                source_canonique=ligne["source"],
            )
        if contrat is not None:
            entree.update(
                id_canonique=ligne["id"], texte_canonique=ligne["texte"],
                categorie_canonique=ligne["categorie"], date_source_canonique=ligne["date_source"],
                preuve_canonique=donnees.get("preuve"),
            )
        connaissances.append(entree)
    ligne = connexion.execute("SELECT donnees_json FROM decisions WHERE id=?", (INDEX,)).fetchone()
    index = json.loads(ligne[0]) if ligne else {}
    return {
        "connaissances": connaissances,
        "journaux": index.get("journaux", {}),
        "contrat_sources_reprises": contrat,
    }


def charger(root):
    """Retourne la projection SQLite privée après vérification de l’identité BENCHAB.

    Si l’index initial manque, importe le registre JSON d’origine dans
    SQLite lorsqu’il est présent. Le fichier d’origine reste conservé.
    """
    chemin = _sans_lien(Path(root) / "benchab.sqlite").resolve(strict=True)
    connexion = sqlite3.connect(chemin.as_uri() + "?mode=ro", uri=True)
    connexion.row_factory = sqlite3.Row
    try:
        if connexion.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("La base mémoire n'est pas une base BENCHAB.")
        donnees = lire_connexion(connexion)
        initial = not connexion.execute(
            "SELECT 1 FROM decisions WHERE id=?", (INDEX,)
        ).fetchone()
    finally:
        connexion.close()
    registre = _sans_lien(Path(root) / "donnees/connaissances/registre.json")
    if initial and registre.is_file():
        reprendre_registre(root, json.loads(registre.read_text(encoding="utf-8")))
        return charger(root)
    return donnees


def reprendre_registre(root, donnees):
    """Import initial privé ; le JSON d'origine reste intact et n'est plus canonique."""
    base = Base(root, creer=False)
    try:
        with base.transaction() as connexion:
            connexion.execute("BEGIN IMMEDIATE")
            if connexion.execute(
                "SELECT 1 FROM decisions WHERE id=?", (INDEX,)
            ).fetchone():
                return
            for item in donnees["connaissances"]:
                if not isinstance(item.get("id"), str) or not item["id"].strip():
                    raise ValueError("Identifiant de connaissance historique manquant.")
                categorie = {
                    "MESURE_PHYSIQUE": "physique", "OBSERVATION_UTILISATEUR": "observation",
                    "FABRICANT": "fabricant", "EMBARQUE": "embarque", "NUMERIQUE": "numerique",
                    "DECISION": "decision",
                }.get(item.get("niveau_preuve"), "observation")
                sources = item.get("sources", [])
                source = sources[0].get("chemin", "registre historique") if sources else "registre historique"
                connexion.execute("INSERT INTO connaissances VALUES (?,?,?,?,?,?,?)", (
                    item["id"], item.get("description", item.get("titre", item["id"])), categorie,
                    source, item.get("date_observation"), 0,
                    json.dumps({"registre_original": item}, ensure_ascii=False, allow_nan=False),
                ))
            connexion.execute("INSERT INTO decisions VALUES (?,NULL,NULL,?,?,?)", (
                INDEX, "Index technique repris sans observation physique déduite", "registre historique",
                json.dumps({"type": "index_journaux", "journaux": donnees.get("journaux", {})},
                           ensure_ascii=False, allow_nan=False),
            ))
    finally:
        base.fermer()


def sauver(root, donnees):
    """Sauve une projection dans la base existante ; unités/statuts conservés, aucune initialisation ni
    archive réécrite."""
    base = Base(root, creer=False)
    try:
        with base.transaction() as connexion:
            connexion.execute("BEGIN IMMEDIATE")
            courant = lire_connexion(connexion)
            if courant["connaissances"] != donnees["connaissances"]:
                raise ValueError("connaissances modifiées concurremment")
            journaux = donnees["journaux"]
            for rel, record in journaux.items():
                prior = courant["journaux"].get(rel, {})
                if prior.get("sha256") == record["sha256"] and not record["connaissances"]:
                    record["statut"] = prior.get("statut", record["statut"])
            texte = json.dumps({"type": "index_journaux", "journaux": journaux}, ensure_ascii=False, allow_nan=False)
            if connexion.execute("SELECT 1 FROM decisions WHERE id=?", (INDEX,)).fetchone():
                connexion.execute("UPDATE decisions SET donnees_json=? WHERE id=?", (texte, INDEX))
            else:
                connexion.execute("INSERT INTO decisions VALUES (?,NULL,NULL,?,?,?)", (
                    INDEX, "Index technique des journaux, sans observation physique déduite", "memoire", texte,
                ))
    finally:
        base.fermer()
