"""Éditeur de découpe intégré à la visionneuse existante, servi sur loopback."""

import copy
import json
import uuid
from urllib.parse import unquote

from benchab.chemins import atelier_prive, sans_lien
from benchab.conception.decoupe import atelier as A
from benchab.conception.decoupe import geometrie as G
from benchab.conception.decoupe.connecteurs import CATALOGUE
from benchab.qualite.ecritures import PublicationNeuve
from benchab.qualite.empreintes import sha256_fichier
from benchab.visualisation.visionneuse import Visionneuse


def page(plan, sortie, *, prive=None):
    plan = copy.deepcopy(plan)
    sortie = A._destination(sortie, prive)
    source = plan["source"]
    if sha256_fichier(source["fichier"]) != source["sha256"]:
        raise ValueError("Source modifiée : régénérer explicitement le plan")
    try:
        resultat = A.calculer(plan)
        pieces = resultat["pieces"] | resultat["accessoires"]
        rapport = resultat["rapport"]
        original = resultat["original"]
        volumes = resultat["volumes_source"]
    except ValueError as exc:
        original, volumes, verification = A.charger(source["fichier"], source["unites_source"])
        if verification["sha256"] != source["sha256"]:
            raise ValueError("Source modifiée pendant la préparation de l'aperçu") from exc
        pieces = {"Original": original}
        rapport = {
            "statut": "FAIL",
            "detail": str(exc),
            "validation_physique": False,
            "limite": "Aperçu source ; corriger le plan avant export",
        }
    vue = Visionneuse(
        "Découpe intelligente",
        note="Proposition numérique, aucun assemblage validé physiquement.",
        source={"sha256": source["sha256"], "unites": "mm"},
    )
    vue.modele("Source_originale", original, couleur="#bbbbbb", opacite=0.15)
    vue.piece("Source originale", "Source_originale", groupe="Source", base_cachee=True, cliquable=False)
    if len(volumes) > 1:
        for i, (nom, mesh) in enumerate(volumes.items()):
            ident = "Source" + nom
            vue.modele(ident, mesh, couleur=("#f8b078", "#80b5e8", "#86d8ab")[i % 3], opacite=0.55)
            vue.piece(nom, ident, groupe="Volumes source", base_cachee=True, cliquable=False)
    for coupe in plan["coupes"]:
        if coupe.get("type", "plan") == "plan":
            try:
                coupe["boucles"] = G.boucles_section(original, coupe["origine"], coupe["normale"])
                coupe["points"] = coupe["boucles"][0]
            except ValueError:
                coupe["points"] = []
    for i, (nom, mesh) in enumerate(pieces.items()):
        vue.modele(nom, mesh, couleur=("#d4c2a0", "#6c9faa", "#bd9568")[i % 3])
        vue.piece(nom, nom, groupe="Découpe")
    vue._decoupe = {"plan": plan, "rapport": rapport, "catalogue": CATALOGUE}
    meta = sortie.with_suffix(sortie.suffix + ".decoupe.json")
    with PublicationNeuve([source["fichier"]], [sortie, meta]) as publication:
        vue.ecrire(publication.temporaire(sortie), prive=prive)
        publication.temporaire(meta).write_text(
            json.dumps({"schema": 1, "source": source, "plan": plan}, ensure_ascii=False, allow_nan=False) + "\n"
        )
        if sha256_fichier(source["fichier"]) != source["sha256"]:
            raise ValueError("Source modifiée pendant la génération de la page")
        publication.publier()
    return sortie


def agir(racine, requete):
    """Action locale bornée : le navigateur ne choisit aucun chemin de sortie."""
    from benchab.visualisation.serveur import fichier_commentaires

    racine = atelier_prive(racine)
    if not isinstance(requete, dict) or requete.get("action") not in ("recalculer", "proposer", "exporter"):
        raise ValueError("Action de découpe inconnue")
    page_relative = requete.get("page")
    if not isinstance(page_relative, str):
        raise ValueError("Page de découpe requise")
    page_relative = unquote(page_relative)
    # Même liste positive de projets/pages que les commentaires ; contrôle des
    # ancêtres avant toute résolution, pas de chemin fourni pour la publication.
    fichier_commentaires(page_relative, racine)
    origine = sans_lien(racine / page_relative.lstrip("/"), racine=racine)
    meta = sans_lien(origine.with_suffix(origine.suffix + ".decoupe.json"), racine=racine)
    reference = json.loads(meta.read_text())
    plan = requete.get("plan")
    if not isinstance(plan, dict) or plan.get("source") != reference["source"]:
        raise ValueError("Le modèle source de cette page ne peut pas être remplacé par la requête")
    projet = origine.parents[2]
    ident = uuid.uuid4().hex[:12]
    if requete["action"] == "exporter":
        resultat = A.calculer(plan)
        sortie = A.exporter(resultat, projet / "candidats/decoupe" / ident, requete.get("format", "stl"), prive=racine)
        return {
            "statut": resultat["rapport"]["statut"],
            "dossier": sortie.relative_to(racine).as_posix(),
            "validation_physique": False,
        }
    if requete["action"] == "proposer":
        besoin = plan.get("besoin_connecteurs", {})
        if not isinstance(besoin, dict):
            raise ValueError("Besoins de connecteurs : objet JSON requis")
        plan = A.nouveau_plan(
            plan["source"]["fichier"],
            plan["source"]["unites_source"],
            plan["nombre_pieces"],
            faces_visibles=plan.get("faces_visibles", []),
            separer_volumes_source=plan.get("separer_volumes_source", False),
            priorite_volumes=plan.get("priorite_volumes"),
            mobile=besoin.get("mobile", False),
            renfort=besoin.get("renfort", False),
        )
    sortie = origine.parent / f"decoupe_{ident}.html"
    page(plan, sortie, prive=racine)
    return {
        "page": "/" + sortie.relative_to(racine).as_posix(),
        "choix_enregistres": True,
        "validation_physique": False,
    }
