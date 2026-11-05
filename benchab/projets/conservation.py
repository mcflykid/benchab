"""Simulation privée de conservation et de propositions d’intermédiaires."""

import json
from pathlib import Path

from benchab.projets.protections import protections
from benchab.projets.retours import etat_projet
from benchab.qualite.empreintes import sha256_fichier

_DOSSIERS_SOURCE = (
    "sources", "src", "native_src", "historique-code", "references", "reference", "docs", "organisation"
)
_EXTENSIONS_IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}
_TECHNIQUES = {"__pycache__", "node_modules", ".git", ".venv", ".venv-dfam"}


def _projet(base, projet):
    """Valide un projet direct de l’espace privé et refuse tout lien symbolique."""
    chemin = Path(projet)
    if not chemin.is_absolute():
        chemin = base.prive / chemin
    chemin = chemin.absolute()
    racine = (base.prive / "projets").resolve()
    if chemin.is_symlink() or not chemin.is_dir():
        raise ValueError("Projet privé direct requis, sans lien symbolique.")
    courant = chemin
    while courant != racine and courant != courant.parent:
        if courant.is_symlink():
            raise ValueError("Lien symbolique dans le chemin du projet refusé.")
        courant = courant.parent
    if courant != racine or chemin.parent.resolve() != racine:
        raise ValueError("Projet privé direct sous projets/ requis.")
    for entree in chemin.rglob("*"):
        if entree.is_symlink():
            raise ValueError("Lien symbolique dans le projet refusé : " + entree.relative_to(chemin).as_posix())
    return chemin


def _relatif(projet, valeur):
    """Valide un chemin interne relatif et sans traversée."""
    relatif = Path(str(valeur))
    if relatif.is_absolute() or not relatif.parts or ".." in relatif.parts:
        raise ValueError("Chemin relatif de projet requis : " + str(valeur))
    cible = projet / relatif
    if not cible.resolve().is_relative_to(projet.resolve()):
        raise ValueError("Chemin sortant du projet refusé : " + str(valeur))
    return cible


def _ajouter(roles, chemin, role, reference=None):
    """Ajoute un rôle et, si fourni, l’identifiant de sa référence."""
    roles.setdefault(chemin, set()).add(role)
    if reference:
        roles[chemin].add("référence:" + str(reference))


def simuler(base, projet):
    """Simule la conservation d’un projet privé sans le modifier.

    Entrées : Base BENCHAB explicite et chemin/nom d’un projet direct sous
    ``base.prive/projets``. Sortie : dictionnaire JSON-compatible avec tailles
    en octets, SHA-256, rôles conservés et propositions. Statuts : chaque
    intermédiaire est ``PROPOSITION`` et requiert l’accord humain ; aucun gain
    n’est accessible. Invariant : ne crée, ne supprime, ne renomme et n’écrit
    aucun fichier, et ne déduit jamais une régénérabilité d’une recette.
    """
    projet = _projet(base, projet)
    suivi = etat_projet(base, projet)
    fichier_qualite = projet / "qualite.json"
    if fichier_qualite.is_file():
        donnees_qualite = json.loads(fichier_qualite.read_text())
        suivi = {**donnees_qualite.get("suivi", {}), **suivi}

    roles = {}
    protections_projet = protections(projet)
    fichiers = {}

    def ajouter_si_present(relatif, role, reference=None):
        cible = _relatif(projet, relatif)
        if cible.is_file():
            rel = cible.relative_to(projet).as_posix()
            _ajouter(roles, rel, role, reference)

    # Les contrats au premier niveau restent conservés ; les sources et
    # références sont des dépendances, conformément à docs/reference/donnees.md.
    for entree in projet.iterdir():
        if entree.is_file() and (entree.suffix in {".md", ".json"} or entree.name in {"Makefile", ".gitignore"}):
            ajouter_si_present(entree.name, "contrat ou documentation racine")
    for dossier in _DOSSIERS_SOURCE:
        racine = projet / dossier
        if racine.is_dir():
            for entree in racine.rglob("*"):
                if entree.is_file() and not any(part in _TECHNIQUES for part in entree.parts):
                    ajouter_si_present(entree.relative_to(projet).as_posix(), "source ou référence nécessaire")

    # Le manifeste explicite les modèles courants ; sa présence ne prouve
    # jamais qu’un autre maillage est régénérable (docs/guides/retour-impression.md).
    manifeste = projet / "candidats/assemblage.json"
    if manifeste.is_file():
        ajouter_si_present("candidats/assemblage.json", "manifeste courant")
        donnees_manifeste = json.loads(manifeste.read_text())
        noms = set(donnees_manifeste.get("pieces", {})) | set(donnees_manifeste.get("models", {}))
        for nom in noms:
            for extension in (".stl", ".step"):
                ajouter_si_present("candidats/" + str(nom) + extension, "modèle courant déclaré")
    for nom in ("candidats/plaques.3mf", "candidats/tranche.3mf"):
        ajouter_si_present(nom, "fabrication courante non imprimée")

    for relatif, empreinte in protections_projet.items():
        ajouter_si_present(relatif, "protégé")
        cible = _relatif(projet, relatif)
        if cible.is_file() and empreinte:
            actuelle = sha256_fichier(cible)
            if actuelle != empreinte:
                raise ValueError("Fichier protégé altéré : " + relatif)

    for nom_liste, role in (
        ("sources_necessaires", "source explicitement nécessaire"),
        ("preuves_utiles", "preuve utile"),
    ):
        for relatif in suivi.get(nom_liste, []):
            ajouter_si_present(relatif, role)
    for reference_type, role in (
        ("references_imprimees", "référence imprimée active"),
        ("references_historiques", "référence imprimée historique"),
    ):
        for reference in suivi.get(reference_type, []):
            for item in reference.get("fichiers", []):
                ajouter_si_present(item["chemin"], role, reference.get("id"))
    for relatif in suivi.get("travail_en_cours", {}).get("fichiers", []):
        ajouter_si_present(relatif, "travail courant")

    # Les documents d’envoi et images d’observation restent liés aux retours humains.
    for evenement in suivi.get("evenements", []):
        for relatif in evenement.get("preuves", []):
            ajouter_si_present(relatif, "preuve de retour humain")
        for document in evenement.get("documents", []):
            ajouter_si_present(document["chemin"], "document de retour humain")
        for envoi in evenement.get("envois_identifies", []) + evenement.get("envois_candidats_preparation", []):
            ajouter_si_present(envoi["chemin"], "envoi associé à un retour humain")
    for entree in projet.rglob("*"):
        if entree.is_file() and entree.suffix.lower() in _EXTENSIONS_IMAGES:
            ajouter_si_present(entree.relative_to(projet).as_posix(), "image d’observation potentielle")

    # Chaque fichier régulier est empreinté une fois pour expliquer les doublons
    # sans assimiler égalité de contenu et possibilité de retrait.
    for entree in projet.rglob("*"):
        if not entree.is_file():
            continue
        if any(part in _TECHNIQUES or part.startswith(".venv") for part in entree.relative_to(projet).parts):
            continue
        relatif = entree.relative_to(projet).as_posix()
        if entree.name in {".DS_Store"} or entree.name.startswith("._"):
            continue
        fichiers[relatif] = {
            "chemin": relatif,
            "taille_octets": entree.stat().st_size,
            "sha256": sha256_fichier(entree),
        }

    groupes = {}
    for item in fichiers.values():
        groupes.setdefault(item["sha256"], []).append(item["chemin"])
    doublons = [
        {"sha256": empreinte, "chemins": sorted(chemins)}
        for empreinte, chemins in sorted(groupes.items())
        if len(chemins) > 1
    ]

    retenus = []
    for relatif, roles_fichier in sorted(roles.items()):
        if relatif in fichiers:
            retenus.append({**fichiers[relatif], "roles": sorted(roles_fichier)})

    intermediaires = []
    for relatif, item in sorted(fichiers.items()):
        if relatif in roles:
            continue
        # Un intermédiaire peut exister pendant une préparation active ; son
        # retrait reste une décision humaine (docs/guides/retour-impression.md).
        intermediaires.append(
            {
                **item,
                "statut_proposition": "PROPOSITION",
                "accord_requis": True,
                "gain_accessible_octets": 0,
                "reproductibilite": "NON VÉRIFIÉ",
            }
        )

    return {
        "simulation": True,
        "suppression_possible": False,
        "projet": projet.name,
        "fichiers_retenus": retenus,
        "intermediaires": intermediaires,
        "doublons": doublons,
        "protections": sorted(protections_projet),
        "references_actives": [item.get("id") for item in suivi.get("references_imprimees", [])],
        "references_imprimees": suivi.get("references_imprimees", []),
        "references_historiques": [item.get("id") for item in suivi.get("references_historiques", [])],
        "note": "Aucun fichier retiré ; une proposition ne démontre pas une régénérabilité.",
    }
