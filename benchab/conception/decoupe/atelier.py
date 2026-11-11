"""Un seul calcul pour la CLI et la visionneuse ; sorties neuves privées."""

import copy
import json
import re
from pathlib import Path

import manifold3d as mf
import numpy as np
import trimesh

from benchab.conception.decoupe import UNITES, _vers_trimesh
from benchab.conception.decoupe import connecteurs as C
from benchab.conception.decoupe import geometrie as G
from benchab.controles.orientations_supports import risque_couches
from benchab.controles.plaques_interface import analyser as analyser_plaques
from benchab.controles.regles import verifier_nommage
from benchab.fabrication import anycubic
from benchab.imports3d import maillage_indexe
from benchab.imports3d.lecture_3mf import lire as lire_3mf
from benchab.qualite.ecritures import PublicationNeuve, renommer_exclusif
from benchab.qualite.empreintes import sha256_fichier


def charger(source, unites):
    if unites not in UNITES:
        raise ValueError("Unités source explicites exigées : mm, cm ou m")
    source = Path(source).expanduser().resolve(strict=True)
    sha = sha256_fichier(source)
    est_3mf = source.suffix.lower() == ".3mf"
    if est_3mf and unites != "mm":
        raise ValueError("Les unités d'un 3MF sont lues dans le fichier et normalisées en mm ; choisir --unites mm")
    scene = lire_3mf(source) if est_3mf else trimesh.load(source, force="scene", process=False)
    volumes = {}
    normalisations = []
    total_faces = 0
    for node in sorted(scene.graph.nodes_geometry):
        transform, name = scene.graph[node]
        mesh = scene.geometry[name].copy().apply_transform(transform).apply_scale(UNITES[unites])
        if not np.isfinite(mesh.vertices).all():
            raise ValueError("Coordonnée non finie dans la source ; aucune réparation implicite")
        if not mesh.is_volume:
            # STL ne porte pas d'indices partagés ; recoller des coordonnées
            # strictement identiques ne déplace aucun triangle. Ne pas employer
            # le merge arrondi ou la réparation automatique de Trimesh.
            vertices, inverse = np.unique(np.asarray(mesh.vertices), axis=0, return_inverse=True)
            mesh = trimesh.Trimesh(vertices, inverse[np.asarray(mesh.faces)], process=False)
            normalisations.append(
                {
                    "noeud": str(node),
                    "operation": "soudure de coordonnées strictement identiques",
                    "positions_modifiees": False,
                }
            )
        total_faces += len(mesh.faces)
        if total_faces > G.MAX_FACES:
            raise ValueError("Budget total de trois millions de faces dépassé")
        # Un STL peut contenir plusieurs solides fermés qui se chevauchent.
        # Les conserver comme sources distinctes avant l'union géométrique.
        for composante in mesh.split(only_watertight=False, repair=False):
            G.solide(composante)
            volumes[f"Volume{len(volumes) + 1}"] = composante
    if not volumes:
        raise ValueError("Aucun volume fermé chargé")
    union = mf.Manifold.batch_boolean([G.solide(p) for p in volumes.values()], mf.OpType.Add)
    if sha256_fichier(source) != sha:
        raise ValueError("Source modifiée pendant sa lecture")
    return (
        _vers_trimesh(union),
        volumes,
        {
            "fichier": str(source),
            "sha256": sha,
            "unites_source": unites,
            "unites_calcul": "mm",
            "unites_fichier": scene.metadata.get("unites_fichier", "non déclarées : argument utilisateur"),
            "modificateurs_non_geometriques": scene.metadata.get("modificateurs_non_geometriques", []),
            "normalisations_lecture": normalisations,
        },
    )


def separer_identifiables(volumes, priorite=None):
    noms = list(volumes)
    corps = {n: G.solide(m) for n, m in volumes.items()}
    recouvrements = []
    for i, a in enumerate(noms):
        for b in noms[i + 1 :]:
            v = float((corps[a] ^ corps[b]).volume())
            if v > 1e-5:
                recouvrements.append({"a": a, "b": b, "volume_mm3": v})
    if recouvrements and priorite is None:
        return {}, {
            "statut": "NON VÉRIFIÉ",
            "recouvrements": recouvrements,
            "raison": "Volumes source identifiables mais matière partagée : déclarer une priorité d'attribution",
        }
    if priorite is not None and (len(priorite) != len(noms) or set(priorite) != set(noms)):
        raise ValueError("La priorité doit citer chaque volume source exactement une fois")
    resultat, precedents = {}, None
    for nom in priorite or noms:
        p = corps[nom] if precedents is None else corps[nom] - precedents
        if p.is_empty():
            raise ValueError("Volume entièrement inclus dans un précédent : frontière ambiguë")
        resultat[nom] = _vers_trimesh(p)
        precedents = corps[nom] if precedents is None else precedents + corps[nom]
    return resultat, {
        "statut": "WARN" if recouvrements else "PASS",
        "recouvrements": recouvrements,
        "priorite_explicite": priorite,
        "raison": "Attribution explicite de la matière partagée" if recouvrements else "Volumes source disjoints",
    }


def nouveau_plan(source, unites="mm", nombre=2, **options):
    for cle in ("mobile", "renfort", "separer_volumes_source"):
        if cle in options and not isinstance(options[cle], bool):
            raise ValueError(cle + " doit être un booléen")
    mesh, volumes, provenance = charger(source, unites)
    proposition = G.proposer(mesh, nombre, faces_visibles=options.get("faces_visibles"))
    composantes = G.separer_volumes(mesh)
    separation = bool(options.get("separer_volumes_source"))
    priorite = options.get("priorite_volumes")
    if separation:
        parties_source, controle_source = separer_identifiables(volumes, priorite)
        if not parties_source:
            raise ValueError(controle_source["raison"])
        composantes = list(parties_source.values())
    n_coupes = max(0, nombre - len(composantes))
    coupes = []
    for c in proposition["coupes"][:n_coupes]:
        polygones, _ = G.section(mesh, np.asarray(c["origine"]), np.asarray(c["normale"]))
        conseil = C.conseiller(
            mobile=options.get("mobile", False),
            renfort=options.get("renfort", False),
            section_mm2=sum(p.area for p in polygones),
        )
        coupes.append(
            {
                **c,
                "connecteur": {"type": conseil["type"]},
                "conseil_connecteur": conseil,
                "points": G.boucle_section(mesh, c["origine"], c["normale"]),
                "fermee": True,
            }
        )
    _, frontieres = separer_identifiables(volumes, priorite if separation else None)
    plan = {
        "schema": 1,
        "source": provenance,
        "nombre_pieces": nombre,
        "coupes": coupes,
        "proposition": proposition,
        "volumes_source": list(volumes),
        "separer_volumes_source": separation,
        "priorite_volumes": priorite,
        "frontieres_source": frontieres,
        "faces_visibles": options.get("faces_visibles", []),
        "validation_physique": False,
        "besoin_connecteurs": {"mobile": bool(options.get("mobile")), "renfort": bool(options.get("renfort"))},
    }
    # Les choix automatiques sont contrôlés avant de proposer le plan. Le
    # recalcul ultérieur d'un plan édité ne modifie jamais son choix explicite.
    return calculer(plan, choisir_connecteurs=True)["plan"]


def calculer(plan, *, choisir_connecteurs=False):
    plan = copy.deepcopy(plan)
    if plan.get("schema") != 1 or not isinstance(plan.get("source"), dict):
        raise ValueError("Plan de découpe schema1 exigé")
    provenance = plan["source"]
    if any(not isinstance(provenance.get(k), str) for k in ("fichier", "sha256", "unites_source")):
        raise ValueError("Source : fichier, SHA-256 et unités textuels exigés")
    if sha256_fichier(provenance["fichier"]) != provenance["sha256"]:
        raise ValueError("Source différente de celle du plan : aucun recalcul silencieux")
    mesh, volumes, source = charger(provenance["fichier"], provenance["unites_source"])
    if source["sha256"] != provenance["sha256"]:
        raise ValueError("Source différente de celle du plan : aucun recalcul silencieux")
    nombre = plan.get("nombre_pieces")
    if type(nombre) is not int or not 1 <= nombre <= 12:
        raise ValueError("Nombre de pièces entier entre 1 et 12 requis")
    if not isinstance(plan.get("coupes"), list) or len(plan["coupes"]) > 11:
        raise ValueError("Liste de 0 à 11 coupes requise")
    for coupe in plan["coupes"]:
        if not isinstance(coupe, dict) or not isinstance(coupe.get("connecteur", {}), (dict, type(None))):
            raise ValueError("Chaque coupe et son connecteur doivent être des objets JSON")
        type_ = coupe.get("type", "plan")
        if type_ == "plan":
            if not {"origine", "normale"} <= set(coupe):
                raise ValueError("Une coupe plane exige origine et normale")
        elif type_ == "courbe":
            if "points" not in coupe:
                raise ValueError("Une coupe courbe exige ses points de contrôle")
        else:
            raise ValueError("Type de coupe inconnu : plan ou courbe")
    if not isinstance(plan.get("separer_volumes_source", False), bool):
        raise ValueError("separer_volumes_source doit être un booléen")
    if plan.get("separer_volumes_source"):
        parties, frontieres = separer_identifiables(volumes, plan.get("priorite_volumes"))
        if not parties:
            raise ValueError(frontieres["raison"])
    else:
        parties = {f"Piece{i + 1}": p for i, p in enumerate(G.separer_volumes(mesh))}
        frontieres = {"statut": "PASS", "raison": "Composantes topologiques disjointes du solide"}
    if len(parties) > nombre:
        raise ValueError(f"Le modèle possède déjà {len(parties)} volumes disjoints ; augmenter le nombre de pièces")
    extras, rapports, prochain = {}, [], len(parties) + 1
    for index, coupe in enumerate(plan["coupes"]):
        # Une coupe modifie un seul volume explicitement désigné ou le plus grand
        # qu'elle traverse. Une coupe qui ne traverse rien est toujours refusée.
        candidats = (
            [coupe["piece_cible"]]
            if coupe.get("piece_cible")
            else sorted(parties, key=lambda k: (-parties[k].volume, k))
        )
        selection = None
        for nom in candidats:
            if nom not in parties:
                raise ValueError("Pièce cible inconnue : " + str(nom))
            try:
                morceaux, bilan = G.couper(parties[nom], coupe)
            except ValueError:
                if coupe.get("piece_cible"):
                    raise
                continue
            selection = nom, morceaux, bilan
            break
        if selection is None:
            raise ValueError(f"Coupe {index + 1} : aucune pièce traversée")
        nom, morceaux, bilan = selection
        origine_piece = parties[nom]
        options_connecteur = coupe.get("connecteur") or {"type": "douille"}
        adaptation = choisir_connecteurs and not any(plan.get("besoin_connecteurs", {}).values())
        erreur = None
        try:
            nouveaux, axes, rapport = C.appliquer_joints(origine_piece, morceaux, coupe, options_connecteur)
            if adaptation and rapport["statut"] == "FAIL":
                erreur = "; ".join(c["controle"] for c in rapport["controles"] if c["statut"] == "FAIL")
        except ValueError as exc:
            if not adaptation:
                raise
            erreur = str(exc)
        if erreur is not None:
            avant_choix = copy.deepcopy(options_connecteur)
            coupe["connecteur"] = {"type": "aucun"}
            avis = C.conseiller(section_mm2=0)
            avis["raison"] = (
                "Logement automatique refusé : " + erreur + ". Joint sans logement proposé ; maintien à qualifier."
            )
            coupe["conseil_connecteur"] = avis
            plan.setdefault("ajustements_automatiques", []).append(
                {"coupe": index + 1, "avant": avant_choix, "apres": coupe["connecteur"], "raison": erreur}
            )
            nouveaux, axes, rapport = C.appliquer_joints(origine_piece, morceaux, coupe, coupe["connecteur"])
        del parties[nom]
        volumes_decoupes = [q for p in nouveaux.values() for q in G.separer_volumes(p)]
        noms = [f"Piece{prochain + i}" for i in range(len(volumes_decoupes))]
        prochain += len(volumes_decoupes)
        parties.update(dict(zip(noms, volumes_decoupes, strict=True)))
        extras.update({f"Coupe{index + 1}_{k.replace('-', '_')}": p for k, p in axes.items()})
        rapports.append({"coupe": index + 1, "cible": nom, "pieces": noms, "geometrie": bilan, "connecteur": rapport})
    if len(parties) != nombre:
        raise ValueError(f"{len(parties)} pièces obtenues pour {nombre} demandées ; ajuster coupes ou nombre")
    tout = {**parties, **extras}
    union = mf.Manifold.batch_boolean([G.solide(p) for p in tout.values()], mf.OpType.Add)
    original = G.solide(mesh)
    ajoute, retire = float((union - original).volume()), float((original - union).volume())
    reassemblage = G.reassemblage(mesh, tout, volume_ajoute=ajoute, volume_retire=retire)
    echecs = [r for r in rapports if r["connecteur"]["statut"] == "FAIL"]
    rapport = {
        "schema": 1,
        "source": source,
        "unites": "mm",
        "nombre_pieces": len(parties),
        "accessoires_separes": list(extras),
        "coupes": rapports,
        "reassemblage": reassemblage,
        "frontieres_source": frontieres,
        "validation_physique": False,
        "statut": "FAIL" if echecs or reassemblage["statut"] == "FAIL" else "WARN",
        "limites": "Calcul géométrique ; aspect, solidité, montage et fonctionnement physiques non validés",
    }
    if sha256_fichier(source["fichier"]) != source["sha256"]:
        raise ValueError("Source modifiée pendant le calcul")
    return {
        "plan": plan,
        "original": mesh,
        "volumes_source": volumes,
        "pieces": parties,
        "accessoires": extras,
        "rapport": rapport,
    }


def _destination(path, prive=None):
    """Valide un export neuf privé/temporaire ; FAIL sur préparation figée, aucune écriture ni réglage changé."""
    from benchab.qualite.ecritures import destination_neuve

    path = destination_neuve(path, prive=prive)
    if any(
        a.casefold() == "preparations" and re.fullmatch(r"p[0-9]+", b.casefold())
        for a, b in zip(path.parts, path.parts[1:])
    ):
        raise ValueError("Préparation Pnn figée : enregistrer la variante dans candidats ou preuves")
    return path


def _etat_topologique(maillage):
    """Décrit la topologie indexée et la pose en mm, sans correction du maillage."""
    return {
        "sommets": len(maillage.vertices),
        "faces": len(maillage.faces),
        "composantes": maillage.body_count,
        "volume_signe_mm3": float(maillage.volume),
        "orientation_coherente": bool(maillage.is_winding_consistent),
        "ferme": bool(maillage.is_watertight),
        "bornes_mm": maillage.bounds.tolist(),
        "repere": "pose export sur plateau, mm",
    }


def _relire_export_indexe(fichier, avant, *, prive):
    """Écrit et relit la pose sans fusion ; PASS uniquement sur égalité exacte sommets et faces."""
    maillage_indexe.sauvegarder(fichier, avant, prive=prive)
    relu = maillage_indexe.lire(fichier)
    if not (np.array_equal(avant.vertices, relu.vertices) and np.array_equal(avant.faces, relu.faces)):
        raise ValueError("Réouverture NPZ différente : positions ou indices altérés.")
    return relu, {
        "statut": "PASS",
        "avant_export": _etat_topologique(avant),
        "relecture_brute": _etat_topologique(relu),
        "fusion": "NON APPLIQUÉE",
    }


def exporter(resultat, sortie, format_="stl", reglages=None, *, prive=None):
    """Exporte la géométrie posée en mm ; renvoie chemin et rapport NON PRÊT sans profil 3MF explicite.

    Tout retrait ou remplacement des réglages source est déclaré avant écriture puis comparé à la sortie.
    Un réglage inconnu ne peut être retiré ; aucun profil installé n’est modifié (docs/reference/integrite-3mf.md).
    """
    import tempfile

    if resultat["rapport"]["statut"] == "FAIL":
        raise ValueError("Ajustement ou réassemblage FAIL : corriger avant export")
    if format_ not in ("stl", "3mf", "npz"):
        raise ValueError("Format attendu : stl, 3mf ou npz")
    if format_ == "npz" and reglages is not None:
        raise ValueError("NPZ géométrique : aucun réglage d’impression ne peut y être embarqué")
    cible = _destination(sortie, prive)
    source = resultat["plan"]["source"]
    if sha256_fichier(source["fichier"]) != source["sha256"]:
        raise ValueError("Source changée avant export")
    cible.parent.mkdir(parents=True, exist_ok=True)
    poses = {}
    controles_export = {}
    tout = resultat["pieces"] | resultat["accessoires"]
    if len(tout) > 30:
        raise ValueError("Maximum 30 plaques ; répartir explicitement les exports")
    with tempfile.TemporaryDirectory(prefix=".decoupe-", dir=cible.parent) as temp:
        travail = Path(temp) / "export"
        travail.mkdir()
        for index, (nom, mesh) in enumerate(tout.items()):
            plat, matrice, bilan = G.orienter(mesh)
            if not bilan["tient_plateau"]:
                raise ValueError(f"{nom} dépasse le plateau dans ses poses testées ; ajouter une coupe")
            poses[nom] = {
                "vers_plateau": matrice.tolist(),
                "vers_reassemblage": np.linalg.inv(matrice).tolist(),
                "orientation": bilan,
                "accessoire": nom in resultat["accessoires"],
            }
            if format_ == "stl":
                donnees = trimesh.exchange.stl.export_stl(plat)
                donnees = f"BENCHAB units=mm {nom}".encode("ascii").ljust(80, b" ")[:80] + donnees[80:]
                (travail / (nom + ".stl")).write_bytes(donnees)
                recharge = trimesh.load_mesh(travail / (nom + ".stl"), process=True)
                controles_export[nom] = {"topologie_indexee": {
                    "statut": "NON VÉRIFIÉ",
                    "raison": "STL sans indices persistants, relu avec fusion ; exporter en NPZ pour les préserver.",
                }}
            elif format_ == "npz":
                recharge, preuve = _relire_export_indexe(travail / (nom + ".npz"), plat, prive=prive)
                controles_export[nom] = {"topologie_indexee": preuve}
            else:
                items = [
                    {
                        "name": nom,
                        "mesh": plat,
                        "w": float(plat.extents[0]),
                        "h": float(plat.extents[1]),
                        "extruder": 1,
                        "settings": {},
                    }
                ]
                anycubic.ranger(items, 0, 1, 6)
                controle_integrite = {}
                anycubic.ecrire_3mf(
                    [{"name": f"Plaque{index + 1}", "items": items}], reglages or {}, travail / (nom + ".3mf"),
                    nommer_source=True, canonique=bool(reglages), nom_source=nom + ".3mf" if reglages else None,
                    source_3mf=source["fichier"] if Path(source["fichier"]).suffix.lower() == ".3mf" else None,
                    rapport_integrite=controle_integrite,
                )
                # Le nom est déclaré au producteur ; le lanceur le retirera seulement
                # de sa copie moteur (docs/reference/slicer-anycubic.md).
                noms = verifier_nommage(travail / (nom + ".3mf"))
                placement = analyser_plaques(travail / (nom + ".3mf"))
                if noms["statut"] != "PASS" or not placement["conforme"]:
                    raise ValueError("Contrôle U20/U22 de l'export refusé : " + nom)
                controles_export[nom] = {"U22": noms, "U20": placement, "integrite_source": controle_integrite}
                recharge = lire_3mf(travail / (nom + ".3mf")).to_mesh()
            if not recharge.is_volume or not np.allclose(recharge.extents, plat.extents, atol=1e-4):
                conseil = " ; essayer NPZ pour comparer la topologie brute" if format_ == "stl" else ""
                raise ValueError("Réouverture d'export non conforme : " + nom + conseil)
        rapport = copy.deepcopy(resultat["rapport"])
        for coupe in rapport["coupes"]:
            connecteur = coupe["connecteur"]
            joints = connecteur.get("joints", [connecteur])
            for index_joint, joint in enumerate(joints):
                if "axe" not in joint:
                    continue
                noms = [n for n in coupe["pieces"] if n in poses]
                prefixe = f"Coupe{coupe['coupe']}_"
                if "joints" in connecteur:
                    prefixe += f"joint{index_joint + 1}_"
                noms += [n for n in poses if n.startswith(prefixe)]
                directions = {}
                risques = []
                for nom in noms:
                    rotation = np.asarray(poses[nom]["vers_plateau"])[:3, :3]
                    axe = rotation @ np.asarray(joint["axe"])
                    directions[nom] = {
                        "axe_dans_repere_impression": axe.tolist(),
                        "angle_avec_z_deg": float(np.degrees(np.arccos(np.clip(abs(axe[2]), 0, 1)))),
                    }
                    if joint.get("efforts"):
                        risques.append(risque_couches(-(rotation.T @ [0, 0, 1]), joint["efforts"]))
                for controle in joint["controles"]:
                    if controle["controle"] != "Orientation du connecteur":
                        continue
                    controle.update(
                        poses=directions,
                        detail="Évaluation numérique sur les pièces de cette coupe ; "
                        "aucune résistance physique déduite",
                    )
                    if risques:
                        controle.update(
                            statut="WARN" if max(risques) > 0.5 else "PASS",
                            risque_couches_max=max(risques),
                            seuil_heuristique=0.5,
                        )
                    else:
                        controle.update(statut="NON VÉRIFIÉ", raison="Direction d'effort non déclarée")
            if "joints" in connecteur:
                connecteur["controles"] = [{**c, "joint": i + 1} for i, j in enumerate(joints) for c in j["controles"]]
        rapport.update(
            poses_export=poses,
            controles_export=controles_export,
            comparaison_finale={
                "monobloc_pose_source": G.mesure_rapide(
                    resultat["original"].copy().apply_translation(-resultat["original"].bounds[0])
                ),
                "decoupe_avec_connecteurs": {
                    "surplomb_mm2": sum(p["orientation"]["surplomb_mm2"] for p in poses.values()),
                    "indice_support_mm3": sum(p["orientation"]["indice_support_mm3"] for p in poses.values()),
                    "portee": "Indicateurs géométriques dans les poses exportées, pas des supports tranchés",
                },
                "temps": "NON VÉRIFIÉ : trancher les variantes avec les mêmes profils",
                "couture_aspect": "NON VÉRIFIÉ : revue des faces visibles et du G-code requise",
                "solidite_et_reussite": "NON VÉRIFIÉ : aucune garantie ni probabilité inventée",
            },
            format=format_,
            etat_fabrication="Modèles non tranchés ; contrôle d'envoi requis",
            reglages_impression={
                "source": "profil fourni explicitement" if reglages else "non transférés depuis le modèle d'origine",
                "export": (
                    "NON PRÊT : NPZ géométrique, non destiné au slicer" if format_ == "npz" else
                    "NON PRÊT : profil explicite, tranchage et contrôle d'envoi requis" if reglages else
                    "NON PRÊT : export géométrique sans profil 3MF explicitement configuré"
                ),
            },
            fichiers={p.name: sha256_fichier(p) for p in travail.iterdir() if p.is_file()},
        )
        (travail / "decoupe.json").write_text(
            json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        (travail / "choix.json").write_text(
            json.dumps(resultat["plan"], ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        if sha256_fichier(source["fichier"]) != source["sha256"]:
            raise ValueError("Source changée pendant l'export")
        renommer_exclusif(travail, cible)
    return cible


def sauver_plan(plan, cible, *, prive=None):
    """Écrit le plan privé de découpe en mm, sans modifier le modèle source."""
    cible = _destination(cible, prive)
    with PublicationNeuve([plan["source"]["fichier"]], [cible]) as publication:
        publication.temporaire(cible).write_text(
            json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        publication.publier()
    return cible
