"""Lecture 3MF Core/Production, identifiants propres à chaque fichier .model.

Les identifiants peuvent être identiques dans deux modèles du même ZIP. Les
confondre transforme une référence de composant valide en boucle vide.
Aucune extraction ni ouverture d'une ressource extérieure à l'archive.
"""

import posixpath
from pathlib import PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree as E
from zipfile import BadZipFile, ZipFile

import numpy as np
import trimesh

NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
UNITES = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}


def lire(source, max_faces=3_000_000):
    try:
        return _lire(source, max_faces)
    except (E.ParseError, BadZipFile, KeyError, UnicodeError) as exc:
        raise ValueError("Archive 3MF illisible ou incomplète") from exc


def _lire(source, max_faces):
    modeles, resultats = {}, []
    total_faces = 0
    with ZipFile(source) as archive:
        noms = set(archive.namelist())
        if len(noms) != len(archive.namelist()):
            raise ValueError("Membres 3MF dupliqués : archive ambiguë")
        if (
            sum(
                i.file_size
                for i in archive.infolist()
                if i.filename.endswith((".model", ".rels")) or i.filename == "Metadata/model_settings.config"
            )
            > 512 * 1024 * 1024
        ):
            raise ValueError("Budget XML 3MF de 512 Mio dépassé")
        types_parties, ignores = {}, []
        if "Metadata/model_settings.config" in noms:
            config = E.fromstring(archive.read("Metadata/model_settings.config"))
            for obj in config.findall("object"):
                for part in obj.findall("part"):
                    types_parties[(obj.get("id"), part.get("id"))] = part.get("subtype", "normal_part")

        def chemin(courant, reference):
            ref = unquote(reference).replace("\\", "/")
            p = posixpath.normpath(ref.lstrip("/") if ref.startswith("/") else str(PurePosixPath(courant).parent / ref))
            if p.startswith("../") or p not in noms:
                raise ValueError("Composant 3MF absent ou hors archive")
            return p

        def modele(nom):
            if nom not in modeles:
                info = archive.getinfo(nom)
                if info.file_size > 512 * 1024 * 1024:
                    raise ValueError("Modèle XML 3MF trop volumineux")
                root = E.fromstring(archive.read(nom))
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

        def parcourir(nom, ident, transform, pile=(), racine=None):
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
                resultats.append((obj.get("name", f"Objet{len(resultats) + 1}"), m))
            for component in obj.findall(NS + "components/" + NS + "component"):
                path = next((v for k, v in component.attrib.items() if k.endswith("}path") or k == "path"), None)
                enfant = chemin(nom, path) if path else nom
                if modele(enfant)[2] != facteur:
                    raise ValueError("Unités mixtes entre modèles 3MF : conversion explicite requise")
                parcourir(
                    enfant, component.attrib["objectid"], transform @ matrice(component, facteur), pile + (cle,), racine
                )

        # Relation OPC prioritaire, plutôt qu'une hypothèse sur la casse du nom.
        principal = None
        if "_rels/.rels" in noms:
            rels = E.fromstring(archive.read("_rels/.rels"))
            for rel in rels:
                if rel.get("Type", "").endswith("/3dmodel") and rel.get("TargetMode") != "External":
                    principal = chemin("racine", rel.attrib["Target"])
                    break
        if principal is None:
            principal = next((n for n in sorted(noms) if n.lower() == "3d/3dmodel.model"), None)
        if principal is None:
            raise ValueError("Modèle principal 3MF absent")
        root, _, facteur = modele(principal)
        for item in root.findall(NS + "build/" + NS + "item"):
            if item.get("printable", "1") == "0":
                continue
            path = next((v for k, v in item.attrib.items() if k.endswith("}path")), None)
            parcourir(
                chemin(principal, path) if path else principal,
                item.attrib["objectid"],
                matrice(item, facteur),
                racine=item.attrib["objectid"],
            )
    if not resultats:
        raise ValueError("Aucun objet imprimable dans le 3MF")
    scene = trimesh.Scene()
    for i, (nom, mesh) in enumerate(resultats):
        scene.add_geometry(mesh, node_name=f"Objet{i + 1}", geom_name=f"Objet{i + 1}", metadata={"nom_source": nom})
    scene.metadata["units"] = "mm"
    scene.metadata["unites_fichier"] = root.get("unit", "millimeter")
    scene.metadata["modificateurs_non_geometriques"] = ignores
    return scene
