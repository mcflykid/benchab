"""Différentiel exact des réglages, contrôlé après sérialisation avant toute livraison."""

import hashlib
import re
from decimal import Decimal

from benchab import reglages
from benchab.archives_3mf import Archive3MF, decoder_xml

_MAX_CONFIG = 8 * 1024 * 1024


def instantane(chemin, *, avec_types=False):
    """Lit l'état plat en unités natives ; avec_types renvoie aussi les définitions par portée XML réelle.

    Sans écriture ; une chaîne de nom d'objet ne peut pas forger la portée d'une métadonnée.
    """
    # Même garde de chemin, d'index et de budget pour les lectures avant/après (#122).
    with Archive3MF(chemin, taille_max_xml=_MAX_CONFIG) as archive:
        noms = set(archive.noms())
        projet = archive.reglages() if "Metadata/project_settings.config" in noms else {}
        modele = archive.xml("Metadata/model_settings.config") if "Metadata/model_settings.config" in noms else None
        plages = archive.xml("Metadata/layer_config_ranges.xml") if "Metadata/layer_config_ranges.xml" in noms else None
        profil = None
        membre = "Metadata/layer_heights_profile.txt"
        if membre in noms:
            with archive.ouvrir_flux(membre) as flux:
                profil = hashlib.file_digest(flux, "sha256").hexdigest()
    etat, types = _instantane_config(projet, modele, plages, True)
    if profil is not None:
        # Ce format n'est pas interprété : figer son empreinte, sans permission de le transformer
        # (docs/reference/integrite-3mf.md, profils de couches).
        cle = "membre_non_interprete/Metadata/layer_heights_profile.txt"
        etat[cle] = profil
        types[cle] = {"type": "inconnu", "longueur": None}
    return (etat, types) if avec_types else etat


def instantane_config(projet, modele=None, *, plages_couches=None, avec_types=False):
    """Décrit le projet et le XML prévus avant écriture ; unités natives, aucun réglage transformé.

    Retourne l'état et facultativement ses types ; les inconnus restent NON VÉRIFIÉS.
    """
    racine_modele = None if modele is None else decoder_xml(modele, limite_octets=_MAX_CONFIG)
    racine_plages = None if plages_couches is None else decoder_xml(plages_couches, limite_octets=_MAX_CONFIG)
    return _instantane_config(projet, racine_modele, racine_plages, avec_types)


def _instantane_config(projet, root, plages_couches, avec_types):
    etat = {"projet/" + cle: valeur for cle, valeur in reglages.Reglages(projet).items()}
    types = {"projet/" + cle: reglages.definition(cle, valeur=valeur) for cle, valeur in projet.items()}
    if plages_couches is not None:
        _lire_plages(plages_couches, etat, types)
    if root is None:
        return (etat, types) if avec_types else etat

    def lire(noeud, chemin_noeud, *, partie=False):
        if partie and "subtype" in noeud.attrib:
            p = chemin_noeud + "/@subtype"
            if p in etat:
                raise ValueError("Intégrité : attribut de volume ambigu")
            etat[p] = noeud.attrib["subtype"]
            types[p] = reglages.definition("subtype", valeur=etat[p], portee="structure_part")
        for meta in noeud.findall("metadata"):
            cle = meta.get("key")
            if cle is None:
                raise ValueError("Intégrité : clé de métadonnée absente")
            p = chemin_noeud + "/" + cle
            if p in etat:
                raise ValueError("Intégrité : clé ambiguë ou dupliquée")
            etat[p] = meta.get("value", meta.text or "")
            types[p] = reglages.definition(
                cle, valeur=etat[p], portee="origine_part" if partie else "reglage"
            )
        for index, part in enumerate(noeud.findall("part")):
            lire(part, chemin_noeud + "/part/" + str(index), partie=True)

    vus = set()
    objets = {}
    for index, obj in enumerate(root.findall("object")):
        ident = obj.find("metadata[@key='name']")
        nom = ident.get("value") if ident is not None else obj.get("id", str(index))
        if nom in vus:
            raise ValueError("Intégrité : objets homonymes, correspondance explicite requise")
        vus.add(nom)
        if (identifiant := obj.get("id")) is not None:
            if identifiant in objets:
                raise ValueError("Intégrité : identifiants d’objet dupliqués")
            objets[identifiant] = nom
        lire(obj, "objet/" + nom)
    for index, plaque in enumerate(root.findall("plate")):
        lire(plaque, "plaque/" + str(index))
        cle_presence = f"structure/plaque/{index}/@present"
        etat[cle_presence] = True
        types[cle_presence] = {"type": "structure", "longueur": None}
        for rang, instance in enumerate(plaque.findall("model_instance")):
            cle = f"structure/plaque/{index}/instance/{rang}"
            etat[cle] = _structure(instance, objets)
            types[cle] = {"type": "structure", "longueur": None}
    for index, assemblage in enumerate(root.findall("assemble")):
        # Le moteur écrit parfois un conteneur vide. Il ne porte alors aucun
        # placement. Voir docs/reference/integrite-3mf.md.
        if len(assemblage) or assemblage.attrib or (assemblage.text or "").strip():
            cle = f"structure/assemblage/{index}"
            etat[cle] = _structure(assemblage, objets)
            types[cle] = {"type": "structure", "longueur": None}
    return (etat, types) if avec_types else etat


def _structure(noeud, objets):
    """Décrit les liaisons XML dans l’ordre, avec les identités stables des objets connus."""
    def reference(identifiant):
        # Les témoins réels renumérotent les objets au tranchage. Comparer
        # leurs noms uniques conserve l’affectation, même après renumérotation.
        return {"nom": objets[identifiant]} if identifiant in objets else {"id": identifiant}

    attributs = dict(noeud.attrib)
    if "object_id" in attributs:
        attributs["object_id"] = reference(attributs["object_id"])
    texte = (noeud.text or "").strip()
    if noeud.tag == "metadata" and attributs.get("key") == "object_id":
        if "value" in attributs:
            attributs["value"] = reference(attributs["value"])
        elif texte:
            texte = reference(texte)
    enfants = list(noeud)
    if enfants and all(e.tag == "metadata" for e in enfants):
        enfants.sort(key=lambda e: e.get("key", ""))
    return {
        "tag": noeud.tag, "attributs": attributs, "texte": texte,
        "enfants": [_structure(enfant, objets) for enfant in enfants],
    }


def _lire_plages(racine, etat, types):
    if racine.tag != "objects":
        raise ValueError("Intégrité : racine de plages de couches inconnue")
    etat["couches/@present"] = "true"
    types["couches/@present"] = reglages.definition("@present", portee="plage_couches")
    for indice, objet in enumerate(racine):
        if objet.tag != "object":
            raise ValueError("Intégrité : élément de plages de couches inconnu")
        prefixe = f"couches/objet/{indice}"
        for cle, valeur in objet.attrib.items():
            chemin = prefixe + "/@" + cle
            etat[chemin] = valeur
            types[chemin] = reglages.definition("@" + cle, portee="plage_couches")
        for rang, plage in enumerate(objet):
            if plage.tag != "range":
                raise ValueError("Intégrité : plage de couches inconnue")
            base = prefixe + f"/plage/{rang}/"
            for cle, valeur in plage.attrib.items():
                etat[base + "@" + cle] = valeur
                types[base + "@" + cle] = reglages.definition("@" + cle, portee="plage_couches")
            for option in plage:
                if option.tag != "option" or set(option.attrib) != {"opt_key"} or list(option):
                    raise ValueError("Intégrité : option de plage non interprétable")
                cle = option.attrib["opt_key"]
                chemin = base + cle
                if chemin in etat:
                    raise ValueError("Intégrité : option de plage dupliquée")
                etat[chemin] = option.text or ""
                types[chemin] = reglages.definition(cle, valeur=etat[chemin])


def difference(avant, apres):
    """Calcule les différences exactes (unités natives), création/suppression comprises ; ne les autorise jamais."""
    return [
        {
            "chemin": p,
            "present_avant": p in avant,
            "present_apres": p in apres,
            "avant": avant.get(p),
            "apres": apres.get(p),
        }
        for p in sorted(avant.keys() | apres.keys())
        if p not in avant or p not in apres or avant[p] != apres[p]
    ]


def _equivalence_moteur(ecart):
    """Reconnaît uniquement une réécriture décimale exacte du débit natif en mm³/s."""
    avant, apres = ecart["avant"], ecart["apres"]
    return (
        ecart["chemin"] == "projet/filament_max_volumetric_speed"
        and isinstance(avant, list) and isinstance(apres, list)
        and bool(avant) and len(avant) == len(apres)
        and all(
            isinstance(a, str) and isinstance(b, str)
            and re.fullmatch(r"[0-9]{1,16}(?:[.][0-9]{1,16})?", a)
            and re.fullmatch(r"[0-9]{1,16}(?:[.][0-9]{1,16})?", b)
            and Decimal(a) == Decimal(b)
            for a, b in zip(avant, apres)
        )
    )


def verifier(source, destination, permis=(), *, relecture_moteur=False):
    """Vérifie le plan d'écarts en unités natives et renvoie PASS ou lève sur FAIL, sans écrire.

    En relecture moteur, isole les représentations équivalentes du débit en mm³/s.
    Une différence explicitement déclarée reste comparée exactement au plan.
    """
    avant, types_avant = instantane(source, avec_types=True)
    apres, types_apres = instantane(destination, avec_types=True)
    reel = difference(avant, apres)
    for d in reel:
        definitions = [
            types[d["chemin"]]
            for types, present in ((types_avant, d["present_avant"]), (types_apres, d["present_apres"]))
            if present
        ]
        # Un réglage nouvellement documenté peut rester réservé à la lecture (#71).
        if any(definition.get("lecture_seule") for definition in definitions):
            raise ValueError("Intégrité FAIL : réglage en lecture seule modifié, même déclaré : " + d["chemin"])
        if any(definition["type"] == "inconnu" for definition in definitions):
            raise ValueError("Intégrité FAIL : réglage de type inconnu modifié, même déclaré : " + d["chemin"])
    attendu = sorted(list(permis), key=lambda d: d["chemin"])
    if len({d["chemin"] for d in attendu}) != len(attendu):
        raise ValueError("Intégrité FAIL : déclarations de différences dupliquées")
    equivalences = [
        ecart for ecart in reel if relecture_moteur and ecart not in attendu and _equivalence_moteur(ecart)
    ]
    ecarts_metier = [ecart for ecart in reel if ecart not in equivalences]
    if ecarts_metier != attendu:
        manquants = sorted({d["chemin"] for d in attendu} - {d["chemin"] for d in ecarts_metier})
        extras = sorted({d["chemin"] for d in ecarts_metier} - {d["chemin"] for d in attendu})
        raise ValueError(
            "Intégrité 3MF FAIL : différence non déclarée, valeur inattendue ou différence prévue absente ; "
            f"extras={extras}, absentes={manquants}"
        )
    inconnus = sorted(chemin for chemin, definition in types_apres.items() if definition["type"] == "inconnu")
    return {
        "statut": "PASS", "differences": reel, "reouverture": True, "validation_physique": False,
        **({"equivalences_numeriques": equivalences} if relecture_moteur else {}),
        "reglages_non_verifies": [{"chemin": chemin, "statut": "NON VÉRIFIÉ"} for chemin in inconnus],
        "portee": "PASS atteste les différences exactes, pas la sémantique des clés inconnues conservées.",
    }


def autorisation(chemin, avant, apres, *, present_avant=True, present_apres=True):
    """Déclare avant écriture une différence exacte en unités natives ; aucun joker, aucune autorisation physique."""
    return {
        "chemin": chemin,
        "present_avant": present_avant,
        "present_apres": present_apres,
        "avant": avant,
        "apres": apres,
    }
