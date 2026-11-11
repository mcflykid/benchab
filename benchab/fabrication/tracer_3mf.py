"""Traçabilité immuable des exports 3MF privés BENCHAB."""

import argparse
import codecs
import copy
import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from benchab import integrite_3mf
from benchab.archives_3mf import Archive3MF, decoder_xml
from benchab.qualite.ecritures import PublicationNeuve, destination_neuve
from benchab.qualite.empreintes import sha256_fichier

CONTENU_TYPES = "[Content_Types].xml"
PARTIE = "Metadata/benchab-provenance.json"
TYPE_PARTIE = "application/json"
ESPACE_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
MAX_TYPES = 8 * 1024 * 1024


def _decoder_types(contenu):
    """Décode les XML de types usuels sans normaliser les octets stockés."""
    for marque, encodage in (
        (codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
        (codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    ):
        if contenu.startswith(marque):
            return marque, contenu[len(marque):].decode(encodage), encodage
    declaration = re.match(rb"\s*<\?xml[^>]*encoding=[\"']([^\"']+)", contenu[:256], re.I)
    encodage = declaration.group(1).decode("ascii") if declaration else "utf-8"
    codecs.lookup(encodage)
    return b"", contenu.decode(encodage), encodage


def _xml_borne(contenu, limite):
    """Parse un XML 3MF borné et convertit une syntaxe illisible en refus explicite."""
    try:
        return decoder_xml(contenu, limite_octets=limite)
    except ET.ParseError as erreur:
        raise ValueError("XML 3MF malformé") from erreur


def _types_avec_override(contenu):
    """Insère un Override exact avant la racine, en conservant l'ordre et le texte existant."""
    if len(contenu) > MAX_TYPES:
        raise ValueError("Content_Types.xml dépasse le budget XML")
    racine = _xml_borne(contenu, MAX_TYPES)
    if racine.tag != f"{{{ESPACE_TYPES}}}Types":
        raise ValueError("Content_Types.xml a une racine inattendue")
    if any(enfant.tag not in {f"{{{ESPACE_TYPES}}}Default", f"{{{ESPACE_TYPES}}}Override"} for enfant in racine):
        raise ValueError("Content_Types.xml contient un enfant inattendu")
    signature_types = any(
        "digital-signature" in element.get("ContentType", "").casefold()
        or "digital signature" in element.get("ContentType", "").casefold()
        for element in racine
    )
    if signature_types:
        raise ValueError("Archive signée : ajout de provenance refusé")
    for element in racine:
        if (element.tag == f"{{{ESPACE_TYPES}}}Override"
                and element.get("PartName", "").casefold() == ("/" + PARTIE).casefold()):
            raise ValueError("Archive déjà marquée par BENCHAB")
    marque, texte, encodage = _decoder_types(contenu)
    racine_texte = re.search(r"<(?P<prefix>[A-Za-z_][\w.-]*:)?Types(?=[\s>])", texte)
    if not racine_texte:
        raise ValueError("Content_Types.xml ne peut pas être modifié sans réécriture")
    prefixe = racine_texte.group("prefix") or ""
    balises = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<![^>]*>|</[^>]*>|<[^>]*>", re.S)
    fermetures = []
    for balise in balises.finditer(texte):
        if balise.group().startswith(("<!--", "<![CDATA[", "<?", "<!")):
            continue
        match_fermeture = re.fullmatch(r"</(?P<prefix>[A-Za-z_][\w.-]*:)?Types\s*>", balise.group())
        if match_fermeture:
            fermetures.append(balise)
    if len(fermetures) != 1:
        raise ValueError("Fermeture unique de la racine Types introuvable")
    prefixe_fermeture = re.match(
        r"</(?P<prefix>[A-Za-z_][\w.-]*:)?Types", fermetures[0].group()
    ).group("prefix") or ""
    if prefixe_fermeture != prefixe:
        raise ValueError("Préfixe XML de fermeture incohérent")
    insertion = (
        f'<{prefixe}Override PartName="/{PARTIE}" ContentType="{TYPE_PARTIE}"/>'
    )
    enfants_avant = [ET.tostring(enfant) for enfant in racine]
    position = fermetures[0].start()
    resultat = marque + (texte[:position] + insertion + texte[position:]).encode(encodage)
    racine_apres = _xml_borne(resultat, MAX_TYPES)
    override_attendu = [
        enfant for enfant in racine_apres
        if enfant.tag == f"{{{ESPACE_TYPES}}}Override"
        and enfant.attrib == {"PartName": "/" + PARTIE, "ContentType": TYPE_PARTIE}
    ]
    if (len(override_attendu) != 1 or len(racine_apres) != len(enfants_avant) + 1
            or [ET.tostring(enfant) for enfant in list(racine_apres)[:-1]] != enfants_avant):
        raise ValueError("Override absent ou enfants Content_Types.xml modifiés")
    return resultat


def _est_signee(noms):
    """Repère les parties de signature OPC sans essayer de les valider cryptographiquement."""
    return any(
        partie.casefold() == "_xmlsignatures" or nom.casefold().startswith("_xmlsignatures/")
        or nom.casefold().endswith((".p7s", ".p7m", ".sigs"))
        for nom in noms
        for partie in nom.split("/")
    )


def _empreinte_flux(flux):
    """Hache un membre en flux borné, sans charger le G-code complet en mémoire."""
    return hashlib.file_digest(flux, "sha256").hexdigest()


def tracer(source, sortie, *, prive=None):
    """Crée une copie 3MF privée neuve avec SHA source ; réglages et membres existants inchangés."""
    source, sortie = Path(source), Path(sortie).expanduser().absolute()
    if sortie.suffix.casefold() != ".3mf":
        raise ValueError("La sortie doit avoir l’extension .3mf")
    sortie = destination_neuve(sortie, prive=prive, temporaire=True)
    empreinte_source = sha256_fichier(source)
    with Archive3MF(source, taille_max_xml=MAX_TYPES) as archive:
        noms = archive.noms()
        if any(nom.casefold() == PARTIE.casefold() for nom in noms):
            raise ValueError("Archive déjà marquée par BENCHAB")
        if _est_signee(noms):
            raise ValueError("Archive signée : ajout de provenance refusé")
        if CONTENU_TYPES not in noms:
            raise ValueError("Content_Types.xml absent")
        types_avant = archive.lire(CONTENU_TYPES, limite_octets=MAX_TYPES)
        types_apres = _types_avec_override(types_avant)
        provenance = json.dumps(
            {"schema": 1, "outil": "BENCHAB", "source_sha256": empreinte_source},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8") + b"\n"
        with PublicationNeuve([source], [sortie]) as publication:
            temporaire = publication.temporaire(sortie)
            empreintes_sources = {CONTENU_TYPES: hashlib.sha256(types_apres).hexdigest()}
            with ZipFile(temporaire, "w") as destination:
                destination.comment = archive.commentaire
                for nom in noms:
                    info = archive.information(nom)
                    if info.flag_bits & 0x1:
                        raise ValueError("Membre chiffré : copie 3MF refusée")
                    copie_info = copy.copy(info)
                    if nom == CONTENU_TYPES:
                        with destination.open(copie_info, "w") as flux_sortie:
                            flux_sortie.write(types_apres)
                        continue
                    with archive.ouvrir_flux(nom) as flux_source, destination.open(copie_info, "w") as flux_sortie:
                        digest = hashlib.sha256()
                        relation = bytearray() if nom.casefold().endswith(".rels") else None
                        if relation is not None and archive.taille_membre(nom) > MAX_TYPES:
                            raise ValueError("Relation OPC trop volumineuse pour exclure une signature")
                        for bloc in iter(lambda: flux_source.read(1024 * 1024), b""):
                            digest.update(bloc)
                            flux_sortie.write(bloc)
                            if relation is not None:
                                relation.extend(bloc)
                        empreintes_sources[nom] = digest.hexdigest()
                        if relation is not None:
                            racine_relation = _xml_borne(bytes(relation), MAX_TYPES)
                            if any("digital-signature" in lien.get("Type", "").casefold() for lien in racine_relation):
                                raise ValueError("Archive signée : relation de signature OPC refusée")
                destination.writestr(PARTIE, provenance, compress_type=8)
            # Relecture structurelle, CRC, budget, intégrité des réglages et SHA de chaque autre partie.
            with Archive3MF(temporaire, taille_max_xml=MAX_TYPES) as archive_apres:
                noms_apres = archive_apres.noms()
                if set(noms_apres) != {*noms, PARTIE}:
                    raise ValueError("Index ZIP différent du plan de traçabilité")
                for nom in noms_apres:
                    with archive_apres.ouvrir_flux(nom) as flux:
                        obtenu = _empreinte_flux(flux)
                    attendu = hashlib.sha256(provenance).hexdigest() if nom == PARTIE else empreintes_sources[nom]
                    if obtenu != attendu:
                        raise ValueError("Membre 3MF altéré ou Override inattendu : " + nom)
            integrite = integrite_3mf.verifier(source, temporaire, permis=())
            if sha256_fichier(source) != empreinte_source:
                raise ValueError("Source 3MF modifiée pendant la copie")
            publication.publier()
    return {
        "statut": "PASS", "fichier": str(sortie), "source_sha256": empreinte_source,
        "integrite": integrite["statut"], "membres_inchanges": len(noms) - 1,
        "validation_slicer": "NON VÉRIFIÉ", "validation_physique": "NON VÉRIFIÉ",
        "portee": "PASS porte sur la copie exacte et ses réglages, pas sur sa conservation par le slicer.",
    }


def main(argv=None, *, base):
    """Commande CLI de copie 3MF privée ; aucun profil n'est modifié et le statut ne qualifie pas le slicer."""
    parser = argparse.ArgumentParser(description="Ajoute une provenance fixe à une copie privée 3MF neuve")
    parser.add_argument("source", type=Path)
    parser.add_argument("--sortie", type=Path, required=True)
    args = parser.parse_args(argv)
    sortie = args.sortie if args.sortie.is_absolute() else base.prive / args.sortie
    print(json.dumps(tracer(args.source, sortie, prive=base.prive), ensure_ascii=False, indent=2))
    return 0
