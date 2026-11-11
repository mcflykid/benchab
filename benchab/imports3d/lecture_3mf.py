"""Lecture 3MF Core/Production, identifiants propres à chaque fichier .model.

Les identifiants peuvent être identiques dans deux modèles du même ZIP. Les
confondre transforme une référence de composant valide en boucle vide.
Aucune extraction ni ouverture d'une ressource extérieure à l'archive.
"""

import posixpath
from pathlib import PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree as E
from zipfile import BadZipFile

import numpy as np
import trimesh

from benchab.archives_3mf import Archive3MF

NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
PROD = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"
REL_MODELE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
NS_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
UNITES = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}
_MAX_XML = 512 * 1024 * 1024


def lire(source, max_faces=3_000_000, *, identites=False):
    """Lit une scène 3MF en mm, bornée en faces ; optionnellement conserve identifiants et transforms source.

    `identites=True` exige un StartPart OPC unique et les relations de partie qui
    justifient chaque `p:path` ; le mode générique garde ses replis historiques.
    Aucun réglage n’est lu ni modifié et aucun membre n’est extrait sur disque.
    """
    try:
        return _lire(source, max_faces, identites=identites)
    except (E.ParseError, BadZipFile, KeyError, UnicodeError) as exc:
        raise ValueError("Archive 3MF illisible ou incomplète") from exc


def _lire(source, max_faces, *, identites=False):
    modeles, resultats = {}, []
    total_faces = 0
    # Les modèles géométriques gardent leur budget XML propre, sans assouplir l'index commun (#122).
    with Archive3MF(source, taille_max_xml=_MAX_XML) as archive:
        noms = set(archive.noms())
        if (
            sum(
                archive.taille_membre(nom)
                for nom in noms
                if nom.endswith((".model", ".rels")) or nom == "Metadata/model_settings.config"
            )
            > _MAX_XML
        ):
            raise ValueError("Budget XML 3MF de 512 Mio dépassé")
        types_parties, ignores = {}, []
        if "Metadata/model_settings.config" in noms:
            config = archive.xml("Metadata/model_settings.config")
            for obj in config.findall("object"):
                for part in obj.findall("part"):
                    types_parties[(obj.get("id"), part.get("id"))] = part.get("subtype", "normal_part")

        def chemin(courant, reference):
            ref = unquote(reference).replace("\\", "/")
            p = posixpath.normpath(ref.lstrip("/") if ref.startswith("/") else str(PurePosixPath(courant).parent / ref))
            if p.startswith("../") or p not in noms:
                raise ValueError("Composant 3MF absent ou hors archive")
            return p

        def relations(nom):
            """Lit les relations OPC d’une partie ; les cibles restent dans l’index ZIP vérifié."""
            dossier, base = posixpath.split(nom)
            nom_rels = posixpath.join(dossier, "_rels", base + ".rels")
            if nom_rels not in noms:
                return []
            racine_rels = archive.xml(nom_rels)
            if racine_rels.tag != NS_REL + "Relationships":
                raise ValueError("Racine de relations OPC invalide : " + nom_rels)
            resultat, identifiants = [], set()
            for relation in racine_rels:
                if relation.tag != NS_REL + "Relationship":
                    raise ValueError("Entrée de relations OPC inconnue : " + nom_rels)
                identifiant = relation.get("Id")
                cible = relation.get("Target")
                mode = relation.get("TargetMode", "Internal")
                if not identifiant or identifiant in identifiants or not cible or mode not in {"Internal", "External"}:
                    raise ValueError("Relation OPC mal formée : " + nom_rels)
                identifiants.add(identifiant)
                resultat.append((relation.get("Type", ""), mode, cible))
            return resultat

        def verifier_relation_partie(source_modele, cible_modele):
            """Exige le lien OPC interne qui fonde un p:path Production."""
            # p:path désigne une partie liée, pas seulement un nom présent dans le ZIP.
            correspondances = []
            for type_relation, mode, cible in relations(source_modele):
                if type_relation != REL_MODELE or mode != "Internal":
                    continue
                if chemin(source_modele, cible) == cible_modele:
                    correspondances.append(cible)
            if len(correspondances) != 1:
                raise ValueError("Relation OPC du modèle source au p:path cible absente ou ambiguë")

        def modele(nom):
            if nom not in modeles:
                root = archive.xml(nom)
                unite = root.get("unit", "millimeter")
                if unite not in UNITES:
                    raise ValueError("Unité 3MF inconnue")
                objets = {}
                for obj in root.findall(NS + "resources/" + NS + "object"):
                    ident = obj.attrib["id"]
                    if ident in objets:
                        raise ValueError("Identifiant 3MF dupliqué dans le même modèle")
                    objets[ident] = obj
                modeles[nom] = root, objets, UNITES[unite]
            return modeles[nom]

        def matrice(element, facteur):
            texte = element.get("transform")
            if texte is None:
                return np.eye(4)
            nombres = np.asarray([float(v) for v in texte.split()], float)
            if nombres.shape != (12,) or not np.isfinite(nombres).all():
                raise ValueError("Transformation 3MF invalide")
            m = np.eye(4)
            m[:3] = nombres.reshape(4, 3).T
            m[:3, 3] *= facteur
            return m

        def chemin_element(element, *, build=False):
            """Renvoie l’unique p:path production ; refuse les extensions ambiguës pour une preuve d’identité."""
            if not identites:
                # Le lecteur historique accepte un path nu sur un composant, pas sur une instance de build.
                return next((v for k, v in element.attrib.items() if k.endswith("}path") or (
                    not build and k == "path"
                )), None)
            attributs = [(cle, valeur) for cle, valeur in element.attrib.items() if cle.rsplit("}", 1)[-1] == "path"]
            if len(attributs) > 1 or any(cle != PROD + "path" for cle, _ in attributs):
                raise ValueError("Attribut p:path absent du namespace attendu ou ambigu")
            if attributs and not attributs[0][1].strip():
                raise ValueError("Attribut p:path vide")
            return attributs[0][1] if attributs else None

        def printable_element(item):
            """Refuse les indicateurs imprimables mal formés avant d’ignorer une instance."""
            attributs = [(cle, valeur) for cle, valeur in item.attrib.items() if cle.rsplit("}", 1)[-1] == "printable"]
            if identites and (len(attributs) > 1 or any(cle != "printable" for cle, _ in attributs)):
                raise ValueError("Attribut printable non qualifié ou ambigu")
            valeur = item.get("printable", "1")
            if identites and valeur not in {"0", "1"}:
                raise ValueError("Valeur printable non qualifiée")
            return valeur

        def parcourir(nom, ident, transform, pile=(), racine=None, trace=(), build_item=None):
            nonlocal total_faces
            cle = nom, ident
            if cle in pile or len(pile) >= 32:
                raise ValueError("Cycle ou profondeur excessive des composants 3MF")
            _, objets, facteur = modele(nom)
            if ident not in objets:
                raise ValueError("Objet 3MF référencé absent")
            obj = objets[ident]
            mesh = obj.find(NS + "mesh")
            if mesh is not None:
                genre = types_parties.get((racine, ident), "normal_part")
                if genre in {"negative_part", "negative_volume"}:
                    raise ValueError(
                        "Volume négatif de slicer : exporter d'abord la géométrie résultante explicitement"
                    )
                if genre in {"modifier_part", "parameter_modifier", "support_blocker", "support_enforcer"}:
                    ignores.append({"objet": racine, "partie": ident, "type": genre})
                    return
                if genre != "normal_part":
                    raise ValueError("Type de partie 3MF inconnu : " + genre)
                vertices = [
                    [float(v.get(k)) for k in ("x", "y", "z")] for v in mesh.findall(NS + "vertices/" + NS + "vertex")
                ]
                faces = [
                    [int(t.get(k)) for k in ("v1", "v2", "v3")]
                    for t in mesh.findall(NS + "triangles/" + NS + "triangle")
                ]
                total_faces += len(faces)
                if total_faces > max_faces or len(resultats) >= 10000:
                    raise ValueError("Budget de géométrie 3MF dépassé")
                if not vertices or not faces or not np.isfinite(vertices).all():
                    raise ValueError("Maillage 3MF vide ou non fini")
                if min(min(f) for f in faces) < 0 or max(max(f) for f in faces) >= len(vertices):
                    raise ValueError("Indice de triangle 3MF invalide")
                m = trimesh.Trimesh(np.asarray(vertices) * facteur, faces, process=False).apply_transform(transform)
                identite = {
                    "build_item": build_item,
                    "modele_principal": racine_modele,
                    "objet_id_racine": racine,
                    "modele_maillage": nom,
                    "objet_id_maillage": ident,
                    "unite_source": modele(nom)[0].get("unit", "millimeter"),
                    "transformations": list(trace),
                } if identites else None
                resultats.append((obj.get("name", f"Objet{len(resultats) + 1}"), m, identite))
            for component in obj.findall(NS + "components/" + NS + "component"):
                path = chemin_element(component)
                enfant = chemin(nom, path) if path else nom
                if identites and path:
                    verifier_relation_partie(nom, enfant)
                if modele(enfant)[2] != facteur:
                    raise ValueError("Unités mixtes entre modèles 3MF : conversion explicite requise")
                matrice_composant = matrice(component, facteur)
                trace_enfant = (*trace, {
                    "type": "composant",
                    "modele": nom,
                    "objet_id": ident,
                    "modele_cible": enfant,
                    "objet_id_cible": component.attrib["objectid"],
                    "transform_3mf": component.get("transform"),
                    "translation_mm": matrice_composant[:3, 3].tolist(),
                    "matrice_mm": matrice_composant.tolist(),
                }) if identites else ()
                parcourir(
                    enfant, component.attrib["objectid"], transform @ matrice_composant, pile + (cle,), racine,
                    trace_enfant, build_item,
                )

        # Relation OPC prioritaire, plutôt qu'une hypothèse sur la casse du nom.
        principal = None
        if "_rels/.rels" in noms:
            rels = archive.xml("_rels/.rels")
            if identites:
                # Une preuve d’identité ne peut pas remplacer le StartPart OPC par le repli historique.
                if rels.tag != NS_REL + "Relationships":
                    raise ValueError("StartPart OPC absent ou relation racine invalide")
                relations_racine, identifiants = [], set()
                for relation in rels:
                    if relation.tag != NS_REL + "Relationship":
                        raise ValueError("Entrée de relation racine OPC inconnue")
                    identifiant = relation.get("Id")
                    cible = relation.get("Target")
                    mode = relation.get("TargetMode", "Internal")
                    type_relation = relation.get("Type", "")
                    if (
                        not identifiant or identifiant in identifiants or not cible
                        or mode not in {"Internal", "External"}
                    ):
                        raise ValueError("Relation racine OPC mal formée")
                    identifiants.add(identifiant)
                    if type_relation.endswith("/3dmodel"):
                        relations_racine.append((type_relation, mode, cible))
                if (
                    len(relations_racine) != 1
                    or relations_racine[0][0] != REL_MODELE
                    or relations_racine[0][1] != "Internal"
                ):
                    raise ValueError("StartPart OPC absent ou ambigu")
                principal = chemin("racine", relations_racine[0][2])
            else:
                for rel in rels:
                    if rel.get("Type", "").endswith("/3dmodel") and rel.get("TargetMode") != "External":
                        principal = chemin("racine", rel.attrib["Target"])
                        break
        elif identites:
            raise ValueError("StartPart OPC absent")
        if principal is None:
            repli = [n for n in sorted(noms) if n.lower() == "3d/3dmodel.model"]
            if identites and len(repli) > 1:
                raise ValueError("Modèles principaux de repli ambigus")
            principal = repli[0] if repli else None
        if principal is None:
            raise ValueError("Modèle principal 3MF absent")
        racine_modele = principal
        root, _, facteur = modele(principal)
        for build_item, item in enumerate(root.findall(NS + "build/" + NS + "item"), 1):
            if printable_element(item) == "0":
                continue
            path = chemin_element(item, build=True)
            modele_item = chemin(principal, path) if path else principal
            if identites and path:
                verifier_relation_partie(principal, modele_item)
            if identites and modele(modele_item)[2] != facteur:
                raise ValueError("Unités mixtes entre modèles 3MF : conversion explicite requise")
            matrice_item = matrice(item, facteur)
            trace_item = ({
                "type": "instance",
                "modele": principal,
                "modele_cible": modele_item,
                "objet_id": item.get("objectid"),
                "build_item": build_item,
                "transform_3mf": item.get("transform"),
                "translation_mm": matrice_item[:3, 3].tolist(),
                "matrice_mm": matrice_item.tolist(),
            },) if identites else ()
            parcourir(
                modele_item,
                item.attrib["objectid"],
                matrice_item,
                racine=item.attrib["objectid"],
                trace=trace_item,
                build_item=build_item,
            )
    if not resultats:
        raise ValueError("Aucun objet imprimable dans le 3MF")
    scene = trimesh.Scene()
    for i, (nom, mesh, identite) in enumerate(resultats):
        scene.add_geometry(
            mesh,
            node_name=f"Objet{i + 1}",
            geom_name=f"Objet{i + 1}",
            metadata={"nom_source": nom, **({"identite_import": identite} if identites else {})},
        )
    scene.metadata["units"] = "mm"
    scene.metadata["unites_fichier"] = root.get("unit", "millimeter")
    scene.metadata["modificateurs_non_geometriques"] = ignores
    # Les rapports de placement doivent relier les bornes déjà transformées aux IDs et matrices de la source (#105).
    if identites:
        scene.metadata["identites_import"] = [identite for _, _, identite in resultats]
    return scene
