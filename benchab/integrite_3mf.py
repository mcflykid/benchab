"""Différentiel exact des réglages, contrôlé après sérialisation avant toute livraison."""

from xml.etree import ElementTree as ET
from zipfile import ZipFile

from benchab import reglages


def instantane(chemin, *, avec_types=False):
    """Lit l'état plat en unités natives ; avec_types renvoie aussi les définitions par portée XML réelle.

    Sans écriture ; une chaîne de nom d'objet ne peut pas forger la portée d'une métadonnée.
    """
    with ZipFile(chemin) as archive:
        noms = archive.namelist()
        if len(noms) != len(set(noms)):
            raise ValueError("Intégrité : membres 3MF dupliqués")
        projet = {}
        if "Metadata/project_settings.config" in noms:
            projet = reglages.decoder(archive.read("Metadata/project_settings.config"))
        modele = None
        if "Metadata/model_settings.config" in noms:
            if archive.getinfo("Metadata/model_settings.config").file_size > 8 * 1024 * 1024:
                raise ValueError("Intégrité : modèle de réglages trop volumineux")
            modele = archive.read("Metadata/model_settings.config")
        plages = None
        if "Metadata/layer_config_ranges.xml" in noms:
            if archive.getinfo("Metadata/layer_config_ranges.xml").file_size > 8 * 1024 * 1024:
                raise ValueError("Intégrité : plages de couches trop volumineuses")
            plages = archive.read("Metadata/layer_config_ranges.xml")
        profil = None
        if "Metadata/layer_heights_profile.txt" in noms:
            import hashlib

            with archive.open("Metadata/layer_heights_profile.txt") as flux:
                profil = hashlib.file_digest(flux, "sha256").hexdigest()
    etat, types = instantane_config(projet, modele, plages_couches=plages, avec_types=True)
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
    etat = {"projet/" + cle: valeur for cle, valeur in reglages.Reglages(projet).items()}
    types = {"projet/" + cle: reglages.definition(cle, valeur=valeur) for cle, valeur in projet.items()}
    if plages_couches is not None:
        _lire_plages(plages_couches, etat, types)
    if modele is None:
        return (etat, types) if avec_types else etat
    root = ET.fromstring(modele)

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
    for index, obj in enumerate(root.findall("object")):
        ident = obj.find("metadata[@key='name']")
        nom = ident.get("value") if ident is not None else obj.get("id", str(index))
        if nom in vus:
            raise ValueError("Intégrité : objets homonymes, correspondance explicite requise")
        vus.add(nom)
        lire(obj, "objet/" + nom)
    for index, plaque in enumerate(root.findall("plate")):
        lire(plaque, "plaque/" + str(index))
    return (etat, types) if avec_types else etat


def _lire_plages(contenu, etat, types):
    racine = ET.fromstring(contenu)
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


def verifier(source, destination, permis=()):
    """Relit les archives et exige le plan exact de différences ; retourne PASS, lève sur FAIL, aucun fichier livré."""
    avant, types_avant = instantane(source, avec_types=True)
    apres, types_apres = instantane(destination, avec_types=True)
    reel = difference(avant, apres)
    for d in reel:
        if any(
            types[d["chemin"]]["type"] == "inconnu"
            for types, present in [(types_avant, d["present_avant"]), (types_apres, d["present_apres"])]
            if present
        ):
            raise ValueError("Intégrité FAIL : réglage de type inconnu modifié, même déclaré : " + d["chemin"])
    attendu = sorted(list(permis), key=lambda d: d["chemin"])
    if len({d["chemin"] for d in attendu}) != len(attendu):
        raise ValueError("Intégrité FAIL : déclarations de différences dupliquées")
    if reel != attendu:
        manquants = sorted({d["chemin"] for d in attendu} - {d["chemin"] for d in reel})
        extras = sorted({d["chemin"] for d in reel} - {d["chemin"] for d in attendu})
        raise ValueError(
            "Intégrité 3MF FAIL : différence non déclarée, valeur inattendue ou différence prévue absente ; "
            f"extras={extras}, absentes={manquants}"
        )
    inconnus = sorted(chemin for chemin, definition in types_apres.items() if definition["type"] == "inconnu")
    return {
        "statut": "PASS", "differences": reel, "reouverture": True, "validation_physique": False,
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
