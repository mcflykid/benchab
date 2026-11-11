"""Résout les matériaux Core déclarés pour un modèle direct, sans conversion."""

import re

NS_CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
_MAX_RESSOURCES = 4096
_MAX_BASES = 4096
_SRGB = re.compile(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?")


def _indice(brut, *, identifiant=False):
    """Lit un entier Core borné, sans assimiler une chaîne ambiguë à un identifiant."""
    if not isinstance(brut, str) or re.fullmatch(r"[0-9]{1,10}", brut) is None:
        raise ValueError("Indice ou identifiant de ressource Core invalide.")
    nombre = int(brut)
    if not int(identifiant) <= nombre < 2**31:
        raise ValueError("Indice ou identifiant de ressource Core hors limites.")
    return nombre


def _groupe_core(ressource):
    """Lit une liste Core ordonnée de noms et couleurs d’affichage sRGB."""
    if ressource.tag != NS_CORE + "basematerials" or set(ressource.attrib) != {"id"}:
        raise ValueError("Groupe de propriétés hors des matériaux de base Core qualifiés.")
    if not 1 <= len(ressource) <= _MAX_BASES:
        raise ValueError("Nombre de matériaux de base hors limites.")
    bases = []
    for base in ressource:
        if base.tag != NS_CORE + "base" or set(base.attrib) != {"name", "displaycolor"} or len(base):
            raise ValueError("Matériau de base hors du sous-cas Core qualifié.")
        nom, couleur = base.get("name"), base.get("displaycolor")
        if not nom or not nom.strip() or not couleur or _SRGB.fullmatch(couleur) is None:
            raise ValueError("Nom ou couleur d’affichage sRGB invalide.")
        # Core 1.3 §5.1.1 : alpha FF implicite ; le nom de matériau reste littéral.
        srgb = (couleur + ("FF" if len(couleur) == 7 else "")).upper()
        bases.append({"nom": nom, "srgb_affichage": srgb})
    return bases


def resoudre(racine, ressources, proprietes_objet, proprietes_facettes):
    """Retourne PASS ou NON VÉRIFIÉ pour des déclarations Core sans unité.

    Prend le modèle et ses ressources XML ainsi que les attributs de l’objet
    et des facettes déjà associés au même maillage direct. Aucun matériau
    imprimé, filament, profil ou réglage ne découle de la couleur d’affichage.
    """
    present = bool(proprietes_objet) or any(proprietes_facettes)
    try:
        if racine.get("requiredextensions", "").strip():
            raise ValueError("Extension requise hors du sous-cas Core.")
        if not present:
            return {"statut": "PASS", "present": False, "objet": None, "facettes": [None] * len(proprietes_facettes)}
        if len(ressources) > _MAX_RESSOURCES:
            raise ValueError("Nombre de ressources Core hors limites.")
        index = {}
        for ressource in ressources:
            identifiant = _indice(ressource.get("id"), identifiant=True)
            if identifiant in index:
                raise ValueError("Identifiant de ressource Core ambigu ou dupliqué.")
            index[identifiant] = ressource

        groupes = {}

        def lire(pid, pindex):
            if pid not in index:
                raise ValueError("Groupe de propriétés Core référencé mais absent.")
            if pid not in groupes:
                groupes[pid] = _groupe_core(index[pid])
            groupe = groupes[pid]
            if pindex >= len(groupe):
                raise ValueError("Indice de matériau Core hors du groupe déclaré.")
            return groupe[pindex]

        # Core 1.3 §4 : un objet dont les facettes déclarent des propriétés
        # doit aussi donner le groupe et l’index servant de valeur par défaut.
        pid_objet = _indice(proprietes_objet.get("pid"), identifiant=True)
        indice_objet = _indice(proprietes_objet.get("pindex"))
        defaut = lire(pid_objet, indice_objet)
        materiaux = []
        for attributs in proprietes_facettes:
            # Le groupe et l’index héritent indépendamment selon Core 1.3 §4.1.4.1.
            pid = _indice(attributs["pid"], identifiant=True) if "pid" in attributs else pid_objet
            indice = _indice(attributs["p1"]) if "p1" in attributs else indice_objet
            # Core 1.3 §4.1.4.1 ne définit pas de gradient pour basematerials.
            if any(_indice(attributs[k]) != indice for k in ("p2", "p3") if k in attributs):
                raise ValueError("Gradient de matériau de base Core interdit.")
            materiaux.append(lire(pid, indice))
        return {"statut": "PASS", "present": True, "objet": defaut, "facettes": materiaux}
    except ValueError as erreur:
        return {"statut": "NON VÉRIFIÉ", "present": present, "raison": str(erreur)}
