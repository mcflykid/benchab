"""Contrôle des conversions d'archives historiques en faits structurés, sans inférer leur fidélité passée."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

CONTRAT_ID = "savoir:contrats_sources_reprises"
TYPE_CONTRAT = "source_convertie_en_faits"


def _source(source: dict) -> tuple[str, str | None, str | None]:
    """Identifie la source par chemin/révision/SHA, ou un membre d'archive par son SHA propre.

    Sans unité; ne confond jamais l'empreinte d'un membre avec celle de son archive.
    """
    chemin = source.get("chemin")
    if not isinstance(chemin, str) or not chemin:
        raise ValueError("chemin de source historique absent")
    revision = source.get("revision_originale", source.get("revision_source_originale"))
    digest = source.get("sha256")
    # Une archive tierce non redistribuable peut rester une référence documentaire par membre
    # sans livrer son code ni inventer une empreinte historique globale (docs/reference/donnees.md).
    if digest is None and source.get("membre") is not None:
        membre = source["membre"]
        if (
            not isinstance(membre, str) or not membre or "#" in chemin
            or Path(membre).is_absolute() or ".." in Path(membre).parts
        ):
            raise ValueError("membre de source historique invalide")
        digest = source.get("sha256_membre")
        if digest is None:
            raise ValueError("SHA du membre historique absent")
        chemin += "#membre=" + membre
    if digest is None and source.get("origine_sha") == "copie_source_capturee_import":
        digest = source.get("sha256_capturee_import")
    if revision is not None and (not isinstance(revision, str) or not revision):
        raise ValueError("révision historique invalide")
    if digest is not None and not re.fullmatch(r"[a-f0-9]{64}", str(digest)):
        raise ValueError("SHA historique invalide")
    return chemin, revision, digest


def representation_fait(item: dict) -> dict:
    """Projette les champs d'un fait en données canoniques.

    Inclut catégorie/date/source canoniques et preuve d'origine; unités inchangées, sans validation physique.
    """
    sources = item.get("sources_originales", item.get("sources", []))
    source_canonique = item.get("source_canonique")
    if source_canonique is None and sources:
        premiere = sources[0]
        source_canonique = premiere.get("chemin") if isinstance(premiere, dict) else premiere
    return {
        "id": item.get("id_canonique", item.get("id")),
        "titre": item.get("titre"),
        "texte": item.get("texte_canonique", item.get("texte", item.get("description"))),
        "description": item.get("description"),
        "niveau_preuve": item.get("niveau_preuve"),
        "categorie_canonique": item.get("categorie_canonique"),
        "preuve_canonique": item.get("preuve_canonique", item.get("preuve")),
        "date_source_canonique": item.get("date_source_canonique"),
        "conditions": item.get("conditions"),
        "exclusions": item.get("exclusions"),
        "unites": item.get("unites", item.get("unités", item.get("unites_mesure"))),
        "faits": item.get("faits"),
        "provenance": {"sources": sources, "source_canonique": source_canonique},
    }


def empreinte_fait(item: dict) -> str:
    """Renvoie le SHA-256 hexadécimal de la projection UTF-8 compacte.

    Unités inchangées; aucune validation physique inférée.
    """
    contenu = json.dumps(
        representation_fait(item), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(contenu.encode("utf-8")).hexdigest()


def _chemin_prive(root: Path, relatif: str) -> Path:
    """Résout un reçu exclusivement sous la racine privée, sans traversée ni lien symbolique."""
    candidat_rel = Path(relatif)
    if candidat_rel.is_absolute() or not relatif or ".." in candidat_rel.parts:
        raise ValueError("reçu hors racine privée")
    candidat = root / candidat_rel
    if any(part.is_symlink() for part in (candidat, *candidat.parents) if part != root.parent):
        raise ValueError("reçu lié refusé")
    cible = candidat.resolve(strict=True)
    if not cible.is_relative_to(root.resolve(strict=True)) or not cible.is_file():
        raise ValueError("reçu absent ou hors racine privée")
    return cible


def controler_contrats(donnees: dict, root: Path) -> dict:
    """Contrôle les contrats privés; sortie PASS/FAIL et fidélité NON VÉRIFIÉ, chemins relatifs sans unité."""
    # Le hash lie la synthèse à une déclaration historique précise; aucune absence d'original ne devient une preuve
    # de fidélité (docs/reference/donnees.md).
    contrat = donnees.get("contrat_sources_reprises")
    erreurs = []
    conversions = []
    index_ecartes = []
    couverts = set()
    couvertures_journaux = set()
    journaux_ecartes = set()
    if contrat is None:
        return {
            "statut": "PASS",
            "erreurs": erreurs,
            "conversions": conversions,
            "index_ecartes": index_ecartes,
            "sources_converties": couverts,
            "journaux_convertis": couvertures_journaux,
            "journaux_ecartes": journaux_ecartes,
            "fidelite_historique": "NON APPLICABLE",
        }
    if not isinstance(contrat, dict) or contrat.get("schema") != 1 or contrat.get("type") != TYPE_CONTRAT:
        return {
            "statut": "FAIL",
            "erreurs": ["Contrat de reprise absent, mal formé ou version inconnue"],
            "conversions": conversions,
            "index_ecartes": index_ecartes,
            "sources_converties": couverts,
            "journaux_convertis": couvertures_journaux,
            "journaux_ecartes": journaux_ecartes,
            "fidelite_historique": "NON VÉRIFIÉ",
        }
    lignes = contrat.get("conversions")
    if not isinstance(lignes, list):
        lignes = []
        erreurs.append("Liste conversions absente du contrat")
    index = {}
    for item in donnees.get("connaissances", []):
        if not isinstance(item, dict):
            erreurs.append("connaissance mal formée dans les faits repris")
            continue
        identifiant = item.get("id")
        if not isinstance(identifiant, str) or not identifiant:
            erreurs.append("identifiant de fait absent ou invalide")
            continue
        if identifiant in index:
            erreurs.append("identifiant de fait dupliqué : " + str(identifiant))
        else:
            index[identifiant] = item
    cles_sources = set()
    for numero, ligne in enumerate(lignes):
        erreurs_avant = len(erreurs)
        etiquette = f"conversion[{numero}]"
        if not isinstance(ligne, dict):
            erreurs.append(etiquette + " mal formée")
            continue
        try:
            identite = _source(ligne.get("source_originale", {}))
        except (AttributeError, ValueError) as exc:
            erreurs.append(etiquette + " source invalide : " + str(exc))
            continue
        if identite[2] is None:
            erreurs.append(etiquette + " sans SHA original déclaré")
        if identite in cles_sources:
            erreurs.append(etiquette + " source dupliquée")
        cles_sources.add(identite)
        if not isinstance(ligne.get("motif"), str) or not ligne["motif"].strip():
            erreurs.append(etiquette + " motif de conversion absent")
        recu = ligne.get("recu_conversion")
        try:
            if not isinstance(recu, dict) or not re.fullmatch(r"[a-f0-9]{64}", str(recu.get("sha256", ""))):
                raise ValueError("reçu ou SHA du reçu absent")
            recu_path = _chemin_prive(root, recu.get("chemin", ""))
            if hashlib.sha256(recu_path.read_bytes()).hexdigest() != recu["sha256"]:
                raise ValueError("SHA du reçu de conversion différent")
        except (OSError, ValueError, TypeError) as exc:
            erreurs.append(etiquette + " reçu invalide : " + str(exc))
        extraits = ligne.get("faits")
        if not isinstance(extraits, list) or not extraits:
            erreurs.append(etiquette + " liste des faits absente")
            continue
        declarations = []
        for item in index.values():
            sources = item.get("sources_originales", item.get("sources", []))
            if isinstance(sources, list):
                sources_vues = set()
                for source in sources:
                    if isinstance(source, dict):
                        try:
                            cle_source = _source(source)
                            if cle_source in sources_vues:
                                erreurs.append(str(item.get("id")) + " : déclaration de source dupliquée")
                            sources_vues.add(cle_source)
                            if cle_source == identite:
                                declarations.append(item.get("id"))
                        except ValueError:
                            continue
        ids_ligne = []
        for entree in extraits:
            if not isinstance(entree, dict):
                erreurs.append(etiquette + " référence de fait mal formée")
                continue
            identifiant = entree.get("id")
            if not isinstance(identifiant, str) or identifiant not in index:
                erreurs.append(etiquette + " fait absent : " + str(identifiant))
                continue
            if identifiant in ids_ligne:
                erreurs.append(etiquette + " fait dupliqué : " + identifiant)
            ids_ligne.append(identifiant)
            if identifiant not in declarations:
                erreurs.append(etiquette + " rattachement source/fait différent : " + identifiant)
            digest = entree.get("sha256_representation")
            try:
                actuel = empreinte_fait(index[identifiant])
            except (TypeError, ValueError) as exc:
                erreurs.append(etiquette + " représentation non canonisable : " + identifiant + " : " + str(exc))
                continue
            if digest != actuel:
                erreurs.append(etiquette + " empreinte de représentation différente : " + identifiant)
        if set(ids_ligne) != set(declarations):
            erreurs.append(etiquette + " liste des faits incomplète ou non exhaustive pour cette source")
        couverts.add(identite)
        if identite[0].split("#", 1)[0].startswith("suivi/journal/"):
            couvertures_journaux.add((identite[0].split("#", 1)[0], identite[2]))
        conversions.append({
            "source_originale": {"chemin": identite[0], "revision_originale": identite[1], "sha256": identite[2]},
            "integrite_extraits": "FAIL" if len(erreurs) > erreurs_avant else "PASS",
            "fidelite_historique": "NON VÉRIFIÉ",
            "conservation_originale": "NON CONSERVÉ PAR CETTE CONVERSION",
        })
    dispositions = contrat.get("index_ecartes", [])
    if not isinstance(dispositions, list):
        erreurs.append("Liste index_ecartes absente ou mal formée")
        dispositions = []
    journaux = donnees.get("journaux", {})
    identites_ecartees = set()
    for numero, disposition in enumerate(dispositions):
        erreurs_avant = len(erreurs)
        etiquette = f"index_ecarte[{numero}]"
        if not isinstance(disposition, dict):
            erreurs.append(etiquette + " mal formée")
            continue
        try:
            identite = _source(disposition.get("source_originale", {}))
        except (AttributeError, ValueError) as exc:
            erreurs.append(etiquette + " source invalide : " + str(exc))
            continue
        chemin, _revision, digest = identite
        path = Path(chemin)
        if (
            digest is None or not chemin.startswith("suivi/journal/")
            or path.is_absolute() or ".." in path.parts or _revision is not None
        ):
            erreurs.append(etiquette + " exige un chemin de journal privé et un SHA exact")
        if identite in identites_ecartees or identite in cles_sources:
            erreurs.append(etiquette + " source déjà déclarée")
        identites_ecartees.add(identite)
        if not isinstance(disposition.get("motif"), str) or not disposition["motif"].strip():
            erreurs.append(etiquette + " motif d'écartement absent")
        try:
            recu = disposition.get("recu_conversion")
            if not isinstance(recu, dict) or not re.fullmatch(r"[a-f0-9]{64}", str(recu.get("sha256", ""))):
                raise ValueError("reçu ou SHA du reçu absent")
            recu_path = _chemin_prive(root, recu.get("chemin", ""))
            if hashlib.sha256(recu_path.read_bytes()).hexdigest() != recu["sha256"]:
                raise ValueError("SHA du reçu d'écartement différent")
        except (OSError, ValueError, TypeError) as exc:
            erreurs.append(etiquette + " reçu invalide : " + str(exc))
        entree_index = journaux.get(chemin) if isinstance(journaux, dict) else None
        if (
            not isinstance(entree_index, dict)
            or entree_index.get("sha256") != digest
            or entree_index.get("connaissances") != []
            or entree_index.get("statut") != "HISTORIQUE"
        ):
            erreurs.append(etiquette + " ne correspond pas à un index HISTORIQUE vide au SHA déclaré")
        faits_lies = []
        for item in index.values():
            sources = item.get("sources_originales", item.get("sources", []))
            if not isinstance(sources, list):
                continue
            for source in sources:
                if isinstance(source, str):
                    source = {"chemin": source}
                if not isinstance(source, dict):
                    continue
                try:
                    if _source(source) == identite:
                        faits_lies.append(item.get("id"))
                except ValueError:
                    continue
        if faits_lies:
            erreurs.append(etiquette + " source rattachée à des faits courants : " + ", ".join(faits_lies))
        index_ecartes.append({
            "source_originale": {"chemin": chemin, "revision_originale": _revision, "sha256": digest},
            "integrite_index": "FAIL" if len(erreurs) > erreurs_avant else "PASS",
            "fidelite_historique": "NON VÉRIFIÉ",
            "conservation_originale": "ÉCARTÉ PAR DISPOSITION EXPLICITE",
        })
        journaux_ecartes.add((chemin, digest))
    return {
        "statut": "FAIL" if erreurs else "PASS",
        "erreurs": erreurs,
        "conversions": conversions,
        "index_ecartes": index_ecartes,
        "sources_converties": couverts,
        "journaux_convertis": couvertures_journaux,
        "journaux_ecartes": journaux_ecartes,
        "fidelite_historique": "NON VÉRIFIÉ" if conversions or index_ecartes else "NON APPLICABLE",
    }
