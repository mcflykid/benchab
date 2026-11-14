"""Génère une visionneuse 3D autonome et interactive, sans accès réseau."""

import argparse
import base64
import datetime
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import trimesh

from benchab.archives_3mf import Archive3MF
from benchab.chemins import atelier_prive, est_temporaire, sans_lien
from benchab.conception import optimisation_interne as OI
from benchab.controles.gcode_interpreter import Interpreter
from benchab.fabrication import remplissage_localise as RL
from benchab.fabrication.anycubic import empreinte_geometrie
from benchab.gcode.lecture import iter_lignes_gcode_3mf, plaques_gcode_archive
from benchab.machine.configuration import charger as configuration_machine
from benchab.qualite.ecritures import PublicationNeuve
from benchab.qualite.empreintes import sha256_fichier
from benchab.visualisation.hors_ligne import autonome

ICI = Path(__file__).resolve().parent
RACINE = atelier_prive()
# L'atelier peut ouvrir localement la page autonome; ce module ne démarre aucun serveur.
MAX_SUPPORT_CELLS = 50000
MAX_SUPPORT_SAMPLES = 500000
MAX_SUPPORT_GCODE_OCTETS = 128 * 1024 * 1024

PORT_SERVEUR = int(configuration_machine()["visionneuse"]["port"])
LIMITE_OCTETS = 40_000_000
PALETTE = {
    "green": "#2e5c48",
    "vert": "#2e5c48",
    "bone": "#e2d6b6",
    "os": "#e2d6b6",
    "marble": "#dfe1e3",
    "marbre": "#dfe1e3",
    "noir": "#2b2f33",
    "rouge": "#b0412e",
    "bleu": "#3d6a94",
    "orange": "#d27f2c",
    "gris": "#9aa3a8",
}


def _assainir_source(valeur):
    """Retire les préfixes personnels des métadonnées intégrées à la page."""
    if isinstance(valeur, dict):
        return {cle: _assainir_source(item) for cle, item in valeur.items()}
    if isinstance(valeur, list):
        return [_assainir_source(item) for item in valeur]
    if isinstance(valeur, str):
        return re.sub(r"/Users/[^/\s]+/(?:[^/\s]+/)*([^/\s]+)", r"\1", valeur)
    return valeur


class _SansRessources(dict):
    file_name = "geometry"

    def __getitem__(self, key):
        raise ValueError("Ressource externe refusée : géométrie autonome requise.")


def charger_maillage(source, process=True):
    """Charge seulement le fichier choisi, sans annexe ni suivi de lien final."""
    source = Path(source)
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Source de maillage non régulière")
        mesh = trimesh.load_mesh(
            stream,
            file_type=source.suffix.lstrip(".").lower(),
            force="mesh",
            process=process,
            skip_materials=True,
            resolver=_SansRessources(),
            allow_remote=False,
        )
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("Source modifiée pendant chargement")
    return mesh


def _alleger(m, pas):
    """Regroupe les sommets sur une grille de `pas` mm (affichage seulement)."""
    v = np.round(m.vertices / pas) * pas
    q = trimesh.Trimesh(v, m.faces.copy(), process=False)
    q.merge_vertices()
    q.update_faces(q.nondegenerate_faces())
    q.update_faces(q.unique_faces())
    q.remove_unreferenced_vertices()
    return q


def _ident(*parties):
    """Normalise les noms de modèle pour l'export GLB."""
    import re
    import unicodedata

    t = unicodedata.normalize("NFKD", "_".join(str(x) for x in parties)).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_]+", "_", t).strip("_")


def _b64(a):
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode("ascii")


class Visionneuse:
    """Prépare des vues de géométries (unités de scène) ; aucune source ou réglage d’impression modifié."""

    def __init__(self, titre, sous_titre="", note="", axe_haut="Z", vue_initiale=(1.0, -1.4, 0.9), source=None):
        """Prépare la visionneuse dans le repère du projet.

        axe_haut : axe vertical du repère du projet ('Z' ou 'Y').
        vue_initiale : direction (repère du projet) d'où la caméra regarde le centre au chargement.
        source : dictionnaire libre affiché en pied de page (manifeste, empreinte, date des contrôles…).
        """
        self.titre, self.sous_titre, self.note, self.axe_haut = titre, sous_titre, note, axe_haut.upper()
        self.vue_initiale = list(map(float, vue_initiale))
        self.source = source or {}
        self.modeles, self.pieces, self.mecanismes, self.groupes = {}, [], [], {}

    def modele(
        self, nom, chemin_ou_maillage, couleur="gris", allegement=None, max_faces=600000, couleurs=None, opacite=1.0
    ):
        """Ajoute un maillage, optionnellement allégé ou coloré par face."""
        m = (
            chemin_ou_maillage
            if isinstance(chemin_ou_maillage, trimesh.Trimesh)
            else charger_maillage(chemin_ou_maillage)
        )
        if allegement or len(m.faces) > max_faces:
            m = _alleger(m, allegement or 0.15)
        if couleurs is not None:
            fc = np.asarray(couleurs(m), np.uint8)
            m = m.copy()  # couleurs par face moyennées aux sommets : sommets non dédoublés (page légère)
            m.visual = trimesh.visual.ColorVisuals(m, face_colors=fc)
            m.visual = trimesh.visual.ColorVisuals(m, vertex_colors=m.visual.vertex_colors)
        if _ident(nom) != nom:
            raise ValueError(
                f"nom de modèle {nom!r} : utiliser des lettres, chiffres et soulignés; ex. {_ident(nom)!r}"
            )
        self.modeles[nom] = {
            "maillage": m,
            "couleur": "#ffffff" if couleurs is not None else PALETTE.get(couleur, couleur),
            "opacite": float(opacite),
            "couleurs_sommets": couleurs is not None,
        }

    def piece(
        self, nom, modele, matrice=None, groupe="Pièces", eclate=None, info="", base_cachee=False, cliquable=True
    ):
        """Ajoute une instance avec pose, groupe et options d'interaction."""
        M = np.eye(4) if matrice is None else np.asarray(matrice, float)
        self.pieces.append(
            {
                "nom": nom,
                "modele": modele,
                "groupe": groupe,
                "M": M,
                "eclate": None if eclate is None else list(map(float, eclate)),
                "info": info,
                "base_cachee": bool(base_cachee),
                "cliquable": bool(cliquable),
            }
        )
        self.groupes.setdefault(groupe, True)

    def contacts(self, rapport):
        """Carte calculée sur la même source, en millimètres dans le repère du projet."""
        if not isinstance(rapport, dict) or rapport.get("mode") != "contacts" or rapport.get("repere") != "projet":
            raise ValueError("Rapport de carte de contacts dans le repère projet exigé")
        if rapport.get("unites") != "mm" or not self.source.get("sha256"):
            raise ValueError("Source et unités de la carte de contacts non établies")
        if rapport.get("source_sha256") != self.source["sha256"]:
            raise ValueError("Carte de contacts issue d'une autre source")
        lignes = rapport.get("contacts")
        if not isinstance(lignes, list) or len(lignes) > 2000:
            raise ValueError("Carte de contacts absente ou trop volumineuse")
        noms = {p["nom"] for p in self.pieces}
        for ligne in lignes:
            if not isinstance(ligne, dict) or ligne.get("a") not in noms or ligne.get("b") not in noms:
                raise ValueError("Pièce de la carte absente de la scène")
            points = ligne.get("patch", [])
            if not isinstance(points, list) or len(points) > 260:
                raise ValueError("Nuage de contact invalide ou trop volumineux")
            valeurs = [ligne.get("mm"), *ligne.get("at", [])]
            if len(ligne.get("at", [])) != 3:
                raise ValueError("Point de contact 3D exigé")
            for point in points:
                if not isinstance(point, list) or len(point) != 4:
                    raise ValueError("Échantillon de contact x/y/z/jeu exigé")
                valeurs.extend(point)
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in valeurs):
                raise ValueError("Valeur numérique finie exigée dans la carte")
        self._contacts = json.loads(json.dumps(rapport, allow_nan=False))

    def commentaires(self, chemin):
        """Charge les commentaires exportés et les rattache aux noms des pièces."""
        d = json.loads(Path(chemin).read_text())
        self._commentaires = d.get("commentaires", d) if isinstance(d, dict) else d

    def groupe_masque(self, groupe):
        """Groupe décoché au chargement (reste activable dans la page)."""
        self.groupes[groupe] = False

    def mecanisme(self, ident, libelle, valeurs, poses, unite="", etat=None, defaut=0):
        """Enregistre les poses du mécanisme pour chaque position échantillonnée.

        Le rappel poses fournit une matrice 4×4 par pièce ou None pour la masquer.
        Les pièces omises gardent leur pose initiale. Le rappel etat fournit le texte facultatif.
        """
        valeurs = list(valeurs)
        frames = [poses(v) for v in valeurs]
        noms = sorted({n for f in frames for n in f})
        idx = {p["nom"]: i for i, p in enumerate(self.pieces)}
        inconnues = [n for n in noms if n not in idx]
        if inconnues:
            raise KeyError(f"pièces inconnues dans le mécanisme {ident} : {inconnues[:5]}")
        mats = np.zeros((len(frames), len(noms), 16), np.float32)
        cach = np.zeros((len(frames), len(noms)), np.uint8)
        for k, f in enumerate(frames):
            for j, n in enumerate(noms):
                M = f.get(n, self.pieces[idx[n]]["M"])
                if M is None:
                    cach[k, j] = 1
                    M = self.pieces[idx[n]]["M"]
                mats[k, j] = np.asarray(M, float).reshape(16)
        self.mecanismes.append(
            {
                "id": ident,
                "libelle": libelle,
                "unite": unite,
                "valeurs": [float(v) if isinstance(v, (int, float, np.floating)) else str(v) for v in valeurs],
                "pieces": [idx[n] for n in noms],
                "matrices": _b64(mats),
                "cachees": _b64(cach) if cach.any() else None,
                "etats": [etat(v) for v in valeurs] if etat else None,
                "defaut": defaut,
            }
        )

    # Les comportements par défaut s'appliquent à tout projet.

    def comparer(self, libelle, anciens, seuils=(0.2, 2.0), allegement=2.5, pieces=None):
        """Ajoute une comparaison colorée à une ancienne version de l'assemblage."""
        ms = [a if isinstance(a, trimesh.Trimesh) else charger_maillage(a) for a in anciens]
        ancien = trimesh.util.concatenate([_alleger(m, 0.4) for m in ms])  # référence fine pour la mesure
        gE, gF = f"Écarts avec {libelle}", f"Version {libelle} (fantôme)"
        for p in [
            q
            for q in list(self.pieces)
            if not q.get("base_cachee") and q["groupe"] not in (gE, gF) and (pieces is None or q["nom"] in pieces)
        ]:
            src = self.modeles[p["modele"]]["maillage"]
            if len(src.faces) < 200:
                continue
            fin = src.copy()
            fin.apply_transform(p["M"])
            fin = _alleger(fin, 0.4)
            Minv = np.linalg.inv(p["M"])

            def couleurs(m, fin=fin, Minv=Minv, M=p["M"]):
                # Compense l'écart produit par l'allègement d'affichage.
                c = trimesh.transformations.transform_points(m.triangles_center, M)
                d = np.abs(trimesh.proximity.closest_point(ancien, c)[1]) - np.abs(
                    trimesh.proximity.closest_point(fin, c)[1]
                )
                out = np.tile(np.array([190, 194, 198, 255], np.uint8), (len(d), 1))
                out[d >= seuils[0]] = [232, 196, 40, 255]
                out[d >= seuils[1]] = [214, 50, 38, 255]
                return out

            n = _ident("ecart", libelle, p["nom"])
            self.modele(n, src, allegement=allegement, couleurs=couleurs)
            self.piece(
                f"{p['nom']} — écart {libelle}",
                n,
                p["M"],
                groupe=gE,
                eclate=p["eclate"],
                info=(
                    f"gris < {seuils[0]} mm ; jaune ; rouge ≥ {seuils[1]} mm de {libelle} "
                    "(écart mesuré sur surfaces fines)"
                ),
            )
        self.modele(_ident("fantome", libelle), _alleger(ancien, 2.5), couleur="#7fa3c8", opacite=0.22)
        self.piece(gF, _ident("fantome", libelle), groupe=gF)
        self.groupe_masque(gE)
        self.groupe_masque(gF)

    @staticmethod
    def couleurs_surplomb(m):
        """Colorie les surplombs, faces au plateau et surfaces sans surplomb."""
        n, c = m.face_normals, m.triangles_center
        bas = -n[:, 2]
        out = np.tile(np.array([214, 218, 221, 255], np.uint8), (len(n), 1))
        out[(bas > 0) & (c[:, 2] > 0.3)] = [86, 170, 92, 255]
        out[(bas > 0.707) & (c[:, 2] > 0.3)] = [232, 150, 40, 255]
        out[(bas > 0.866) & (c[:, 2] > 0.3)] = [214, 50, 38, 255]
        out[(bas > 0.99) & (c[:, 2] <= 0.3)] = [70, 120, 200, 255]
        return out

    def vue_impression(
        self, placement, candidats, tranche=None, tranchage=None, plateau=(250.0, 250.0), allegement=0.3, *,
        archive_tranche=None,
    ):
        """Ajoute les plaques et pièces en mm ainsi que l'aperçu échantillonné des supports.

        Un contexte 3MF fourni doit rester ouvert et correspondre exactement à `tranche`.
        Les budgets sont conservés ; aucune modification de source ni validation physique.
        """
        pl = placement if isinstance(placement, dict) else json.loads(Path(placement).read_text())
        C = Path(candidats)
        objets = pl.get("objects", [])
        if not objets:
            return
        lb = self.pieces and np.min(
            [
                self.modeles[p["modele"]]["maillage"].bounds[0] @ np.eye(3)
                for p in self.pieces
                if not p.get("base_cachee")
            ],
            axis=0,
        )
        ox, oy = (
            (float(lb[0]) if len(self.pieces) else 0.0) - plateau[0] - 60,
            (float(lb[1]) if len(self.pieces) else 0.0),
        )
        T0 = translation(ox, oy, 0)
        plaques = sorted({o["plate"] for o in objets})
        infos = {}
        if tranchage and Path(tranchage).exists():
            for q in json.loads(Path(tranchage).read_text()).get("plaques", []):
                infos[q["index"]] = q
        noms_plaque = {k: [] for k in plaques}
        for k in plaques:
            n = _ident("plateau", k)
            self.modele(n, trimesh.creation.box([plateau[0], plateau[1], 1.0]), couleur="#2a2e33")
            self.modeles[n]["plateau"] = [float(plateau[0]), float(plateau[1])]  # grille dessinée par la page
            self.piece(
                f"Plateau {k}",
                n,
                T0 @ translation(plateau[0] / 2, plateau[1] / 2, -0.5),
                groupe="Impression",
                base_cachee=True,
            )
            noms_plaque[k].append(f"Plateau {k}")
        for o in objets:
            f = C / f"{o['model']}.stl"
            if not f.exists():
                continue
            angle = float(o.get("rot_z_deg", 0))
            if not math.isfinite(angle):
                raise ValueError("Rotation non finie")
            n = _ident("impression", o["model"], str(angle))
            if n not in self.modeles:
                m = charger_maillage(f)
                if angle:
                    m.apply_transform(trimesh.transformations.rotation_matrix(math.radians(angle), [0, 0, 1]))
                m.apply_translation(-m.bounds[0])
                # Identité du maillage avant allègement, dans l'unité du 3MF.
                identite = empreinte_geometrie(m)
                self.modele(n, m, allegement=allegement, couleurs=self.couleurs_surplomb)
                self.modeles[n]["identite_impression"] = identite
            self.source.setdefault("objets_impression", []).append(
                {
                    "piece": o["name"],
                    "modele": o["model"],
                    "plaque": o["plate"],
                    "xy_plaque_mm": o["xy_interface"],
                    "rot_z_deg": angle,
                    "geometrie": self.modeles[n]["identite_impression"],
                }
            )
            nom = f"{o['name']} (impression, plaque {o['plate']})"
            self.piece(
                nom,
                n,
                T0 @ translation(o["xy_interface"][0], o["xy_interface"][1], 0),
                groupe="Impression",
                base_cachee=True,
                info="vert : surplomb < 45° ; orange : 45–60° ; rouge : > 60°, à soutenir ; bleu : face au plateau",
            )
            noms_plaque[o["plate"]].append(nom)
        if tranche and Path(tranche).exists():
            contexte = Archive3MF(tranche) if archive_tranche is None else nullcontext(archive_tranche)
            with contexte as archive:
                # L'index et les lectures de plaques partagent le même contexte borné (#122).
                archive.verifier_source(tranche)
                membres = dict(plaques_gcode_archive(archive))
                for k in plaques:
                    nom_g = membres.get(k)
                    if nom_g is None:
                        continue
                    parser = Interpreter()
                    cel, boites = set(), []
                    samples = 0
                    segments = 0
                    budget = False
                    if archive.taille_membre(nom_g) > MAX_SUPPORT_GCODE_OCTETS:
                        self.source[f"supports_plaque_{k}"] = {
                            "statut": "INDISPONIBLE_BUDGET_GCODE",
                            "limite": "Pièce et plateau conservés ; aucune lecture des supports.",
                        }
                        self.source[f"supports_plaque_{k}_qualite"] = (
                            "INDISPONIBLE : G-code >128MiB ; pièce et plateau conservés."
                        )
                        continue
                    for line in iter_lignes_gcode_3mf(tranche, nom_g, archive=archive):
                        event = parser.feed_line(line)
                        if event and event["trajectory"] == "arc_g17_xy":
                            parser.error("aperçu des supports : arc analytique non échantillonné comme corde")
                            continue
                        if (
                            not event
                            or not (event.get("type") or "").startswith("Support")
                            or event["deposited"] <= 0
                            or event["deposited_length"] <= 0
                        ):
                            continue
                        segments += 1
                        if budget or (segments - 1) % 4:
                            continue
                        start, end = np.array(event["deposition_start"]), np.array(event["end"])
                        n = max(2, min(1000, int(event["deposited_length"] / 2.0) + 1))
                        if samples + n > MAX_SUPPORT_SAMPLES:
                            budget = True
                            continue
                        samples += n
                        for xyz in np.linspace(start, end, n):
                            if len(cel) >= MAX_SUPPORT_CELLS:
                                budget = True
                                break
                            cel.add((round(xyz[0] / 2), round(xyz[1] / 2), round(xyz[2])))
                    self.source[f"supports_plaque_{k}"] = {
                        "statut": "PARTIELLE_BUDGET"
                        if budget
                        else "PARTIELLE_INTERPRETATION"
                        if parser.errors
                        else "APERCU_ECHANTILLONNE",
                        "segments_support_lus": segments,
                        "echantillons": samples,
                        "cellules": len(cel),
                        "erreurs": parser.errors,
                        "methode": (
                            "Un segment support sur4 ; grille XY2mm/Z1mm ; 1000points max par segment. "
                            "Affichage approximatif sans contact physique."
                        ),
                        "budgets": {"cellules": MAX_SUPPORT_CELLS, "echantillons": MAX_SUPPORT_SAMPLES},
                    }
                    qualite = self.source[f"supports_plaque_{k}"]["statut"]
                    self.source[f"supports_plaque_{k}_qualite"] = (
                        f"{qualite} : {len(cel)} cellules ; XY2mm/Z1mm, un segment sur4. "
                        "Approximation visuelle, contacts non mesurés."
                    )
                    if not cel:
                        continue
                    # Regroupe les couches contiguës en colonnes pour limiter les boîtes.
                    zs = sorted({c[2] for c in cel})
                    rang = {z: i for i, z in enumerate(zs)}
                    cols = {}
                    for cx, cy, cz in cel:
                        cols.setdefault((cx, cy), []).append(rang[cz])
                    for (cx, cy), rs in cols.items():
                        rs.sort()
                        debut = prec = rs[0]
                        for r in rs[1:] + [None]:
                            if r is not None and r == prec + 1:
                                prec = r
                                continue
                            z0 = max(0, zs[debut] - 0.5)
                            z1 = max(z0 + 0.1, zs[prec] + 0.5)
                            b = trimesh.creation.box([2.0, 2.0, z1 - z0])
                            b.apply_translation([cx * 2.0, cy * 2.0, (z0 + z1) / 2])
                            boites.append(b)
                            if r is not None:
                                debut = prec = r
                    n = _ident("supports", k)
                    self.modele(n, trimesh.util.concatenate(boites), couleur="#e08a2c", max_faces=10**9)
                    self.piece(
                        f"Supports plaque {k}",
                        n,
                        T0,
                        groupe="Impression",
                        base_cachee=True,
                        info=(
                            f"{len(boites)} colonnes de support approximatives (XY2mm/Z1mm, "
                            "échantillonnées) lues dans le G-code"
                        ),
                    )
                    noms_plaque[k].append(f"Supports plaque {k}")
        tous = [p["nom"] for p in self.pieces if not p.get("base_cachee")]
        valeurs = ["Assemblage"] + [f"Plaque {k}" for k in plaques]

        def poses(v):
            """Renvoie les poses de vue en unités de scène ; ne déplace ni ne modifie les maillages sources."""
            if v == "Assemblage":
                return {n: None for ns in noms_plaque.values() for n in ns}
            k = int(v.split()[1])
            d = {n: None for n in tous}
            for kk in plaques:
                for n in noms_plaque[kk]:
                    d[n] = None if kk != k else self.pieces[[p["nom"] for p in self.pieces].index(n)]["M"]
            return d

        def etat(v):
            """Renvoie l’état sérialisable de la vue, unités natives ; aucune écriture ni validation physique."""
            if v == "Assemblage":
                return "Assemblage. Choisir une plaque pour la voir telle qu’elle sera imprimée."
            k = int(v.split()[1])
            i = infos.get(k, {})
            objs = ", ".join(o["name"] for o in objets if o["plate"] == k)
            return (
                f"Plaque {k} : {objs}"
                + (f" — {i['secondes'] / 3600:.1f} h, {i['grammes']:.0f} g (estimation du slicer)" if i else "")
                + ". Surplombs : vert < 45°, orange 45–60°, rouge > 60° ; supports orange lus dans le G-code."
            )

        self.mecanisme("impression", "Impression (plaques, surplombs, supports)", valeurs, poses, etat=etat)

    def montage(self, etapes, pas=14, libelle="Montage pas à pas"):
        """Construit une animation illustrative de montage, sans contrôle de trajectoire."""
        idx = {p["nom"]: p for p in self.pieces}
        n = len(etapes)
        valeurs = list(range(n * pas))

        def poses(k):
            """Renvoie les poses de vue en unités de scène ; ne déplace ni ne modifie les maillages sources."""
            s, t = k // pas, (k % pas) / (pas - 1)
            d = {}
            for i, e in enumerate(etapes):
                for nom in e["pieces"]:
                    M = idx[nom]["M"]
                    d[nom] = (
                        None
                        if i > s
                        else (
                            M if i < s else translation(*(np.asarray(e.get("depuis", (0, 0, 0)), float) * (1 - t))) @ M
                        )
                    )
            return d

        def etat(k):
            """Renvoie l’état sérialisable de la vue, unités natives ; aucune écriture ni validation physique."""
            s = k // pas
            return f"Étape {s + 1}/{n} : {etapes[s]['texte']} (mouvement illustratif, pas un contrôle de trajectoire)."

        self.mecanisme("montage", libelle, valeurs, poses, etat=etat)

    def _glb(self):
        scene = trimesh.Scene()
        for nom, d in self.modeles.items():
            scene.add_geometry(d["maillage"], node_name=nom, geom_name=nom)
        # La page recalcule les normales et le GLB omet leurs tableaux redondants.
        return trimesh.exchange.gltf.export_glb(scene, include_normals=False)

    def zones_remplissage(self, piece, stl, zones, decalage=(0.0, 0.0, 0.0), resultats=None):
        """Affiche une pièce et ses volumes de remplissage localisé par rôle."""
        COUL = {"decoratif": "#4a90d9", "inchange": "#9a9a9a", "attention": "#e0c341", "structurel": "#e8913a"}
        m = charger_maillage(stl)
        m.apply_translation(-m.bounds[0])
        T = translation(*decalage) if any(decalage) else None
        g = f"Remplissage localisé — {piece}"
        nom = re.sub(r"\W", "_", f"rl_{piece}")
        self.modele(nom, m, couleur="#dfe3ea", opacite=0.25)
        self.piece(
            f"{piece} (référence)",
            nom,
            groupe=g,
            matrice=T,
            eclate=(0, 0, 0),
            info=(resultats or "zones déclarées dans ZONES_REMPLISSAGE ; reste de la pièce : réglages de la pièce"),
        )
        for k, z in enumerate(zones):
            vol = RL.volume(z)
            try:
                vol = trimesh.boolean.intersection([vol, m], engine="manifold")
            except Exception:
                pass
            reg = dict(RL.ROLES[z["role"]][0])
            reg.update(z.get("reglages", {}))
            self.modele(f"{nom}_z{k}", vol, couleur=COUL.get(z["role"], "#d9534f"), opacite=0.85)
            self.piece(
                f"zone {z['nom']}",
                f"{nom}_z{k}",
                groupe=g,
                matrice=T,
                eclate=(0, 0, 0),
                info=(
                    f"rôle {z['role']} ; {', '.join(f'{a} = {b}' for a, b in reg.items()) or 'aucun changement'} ; "
                    f"{RL.ROLES[z['role']][1]}"
                ),
            )

    def optimisation_interne(
        self, piece, stl, cavites, contraintes=None, rapport=None, decalage=(0.0, 0.0, 0.0), variante=None
    ):
        """Affiche cavités, renforts, protections et variante d'optimisation."""
        COUL = {
            "fixation": "#d9534f",
            "trou": "#d9534f",
            "appui": "#4a90d9",
            "optique": "#f2d03b",
            "phosphorescent": "#7ee08a",
            "thermique": "#ff7f50",
            "interface": "#b07cd8",
            "collage": "#b07cd8",
            "cable": "#40c4c4",
            "mecanisme": "#b07cd8",
        }
        m = charger_maillage(stl)
        m.apply_translation(-m.bounds[0])
        T = translation(*decalage) if any(decalage) else None
        g = f"Optimisation interne — {piece}"
        nom = re.sub(r"\W", "_", f"oi_{piece}")
        ctl = {c["controle"]: c for c in (rapport or {}).get("controles", [])}
        self.modele(nom, m, couleur="#dfe3ea", opacite=0.2)
        bilan = (
            f"statut des contrôles : {(rapport or {}).get('statut', 'non calculé')} ; "
            f"volume retiré {(rapport or {}).get('volume_retire_mm3', '?')} mm³"
        )
        self.piece(
            f"{piece} (enveloppe de référence)",
            nom,
            groupe=g,
            matrice=T,
            eclate=(0, 0, 0),
            cliquable=False,  # les détails internes restent cliquables
            info=bilan + " ; coupe X/Y/Z pour voir l'intérieur",
        )
        for k, c in enumerate(cavites):
            if c.get("forme") in OI.RENFORTS:  # Nervure ou gousset, découpé à l'enveloppe comme dans appliquer().
                rf = OI.renfort(c)
                try:
                    rf = trimesh.boolean.intersection([rf, m], engine="manifold")
                except Exception:
                    pass
                ap = ctl.get(f"appui de {c['nom']}", {})
                ep = ctl.get(f"épaisseur de {c['nom']}", {})
                self.modele(f"{nom}_r{k}", rf, couleur="#3fa7d6", opacite=0.9)
                self.piece(
                    f"{c['forme']} {c['nom']}",
                    f"{nom}_r{k}",
                    groupe=g,
                    matrice=T,
                    eclate=(0, 0, 0),
                    info=(
                        f"épaisseur {c['epaisseur']} mm ({ep.get('statut', '?')}) ; "
                        f"appui {ap.get('statut', '?')} ({ap.get('detail', '')}) ; "
                        f"rigidité non évaluée ; pièce : {bilan}"
                    ),
                )
                continue
            cav = OI.cavite(c)
            p = ctl.get(f"paroi autour de {c['nom']}", {})
            pl = ctl.get(f"plafonds autoportants de {c['nom']}", {})
            self.modele(f"{nom}_c{k}", cav, couleur="#ff9f1c", opacite=0.9)
            self.piece(
                f"cavité {c['nom']}",
                f"{nom}_c{k}",
                groupe=g,
                matrice=T,
                eclate=(0, 0, 0),
                info=(
                    f"toit {c.get('angle', 45)}°, faîtage {c.get('faitage', 'x')} ; "
                    f"{cav.volume:.0f} mm³ ; paroi {p.get('statut', '?')} ({p.get('detail', '')}) ; "
                    f"plafonds {pl.get('statut', '?')} ; pièce : {bilan}"
                ),
            )
        if variante is not None and Path(variante).exists():
            mv = charger_maillage(variante)
            mv.apply_translation(-mv.bounds[0])
            Tv = translation(decalage[0], decalage[1] + float(m.extents[1]) + 15, decalage[2])
            self.modele(f"{nom}_var", mv, couleur="#8fb996")
            self.piece(
                f"{piece} (variante optimisée)",
                f"{nom}_var",
                groupe=g,
                matrice=Tv,
                eclate=(0, 0, 0),
                info=(
                    f"volume {abs(mv.volume):.0f} mm³ contre {abs(m.volume):.0f} mm³ pour la référence ; "
                    f"même coupe X/Y/Z pour comparer l’intérieur ; {bilan}"
                ),
            )
        for k, z in enumerate((contraintes or {}).get("protege", [])):
            v = OI.RL.volume(z)
            try:
                v = trimesh.boolean.intersection([v, m], engine="manifold")
            except Exception:
                pass
            c = ctl.get(f"région protégée {z['nom']} ({z['type']})", {})
            self.modele(f"{nom}_p{k}", v, couleur=COUL.get(z["type"], "#9a9a9a"), opacite=0.55)
            self.piece(
                f"protégé {z['nom']}",
                f"{nom}_p{k}",
                groupe=g,
                matrice=T,
                eclate=(0, 0, 0),
                info=(
                    f"type {z['type']}, marge {z.get('marge', 0)} mm ; "
                    f"contrôle {c.get('statut', 'non calculé')} ({c.get('detail', '')})"
                )
                + (" ; propriété lumineuse NON VÉRIFIÉE" if z["type"] in ("optique", "phosphorescent") else ""),
            )

    def ecrire(self, chemin, hors_ligne=True, *, prive=None):
        """Écrit une page autonome neuve en privé/temporaire; géométries et réglages restent intacts."""
        brut = Path(chemin).expanduser().absolute()
        chemin = sans_lien(brut)
        racine_privee = atelier_prive(prive)
        if not chemin.is_relative_to(racine_privee) and not est_temporaire(chemin):
            raise ValueError("Les pages liées à un projet doivent rester dans prive/ ou /private/tmp")
        if chemin.exists() or chemin.is_symlink():
            raise FileExistsError("La page existe déjà : jamais d'écrasement")
        if not hors_ligne:
            raise ValueError("Seule la visionneuse autonome hors ligne est autorisée")
        chemin.parent.mkdir(parents=True, exist_ok=True)
        # Centre et taille de l'assemblage (pose de base) pour la caméra, la coupe et l'éclaté par défaut.
        pts = []
        for p in [q for q in self.pieces if not q.get("base_cachee")] or self.pieces:
            b = self.modeles[p["modele"]]["maillage"].bounds
            c = np.array([[x, y, z, 1.0] for x in b[:, 0] for y in b[:, 1] for z in b[:, 2]]) @ p["M"].T
            pts.append(c[:, :3])
        pts = np.vstack(pts)
        lo, hi = pts.min(0), pts.max(0)
        centre = (lo + hi) / 2
        taille = float(np.linalg.norm(hi - lo))
        pieces = []
        for p in self.pieces:
            b = self.modeles[p["modele"]]["maillage"].bounds
            c = p["M"] @ np.append((b[0] + b[1]) / 2, 1.0)
            if p["eclate"] is None:
                d = c[:3] - centre
                n = np.linalg.norm(d)
                p["eclate"] = list((d / n * 0.35 * taille) if n > 1e-6 else [0.0, 0.0, 0.0])
            ext = self.modeles[p["modele"]]["maillage"].extents
            pieces.append(
                {
                    "nom": p["nom"],
                    "modele": p["modele"],
                    "groupe": p["groupe"],
                    "base_cachee": p.get("base_cachee", False),
                    "matrice": [round(float(x), 6) for x in p["M"].reshape(16)],
                    "eclate": [round(float(x), 3) for x in p["eclate"]],
                    "dimensions": [round(float(x), 2) for x in ext],
                    "info": p["info"],
                }
                | ({} if p.get("cliquable", True) else {"cliquable": False})
            )
        glb = self._glb()
        donnees = {
            "titre": self.titre,
            "sous_titre": self.sous_titre,
            "note": self.note,
            "axe_haut": self.axe_haut,
            "vue_initiale": self.vue_initiale,
            "centre": [float(x) for x in centre],
            "bornes": [[float(x) for x in lo], [float(x) for x in hi]],
            "taille": taille,
            "genere_le": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
            "source": _assainir_source(self.source),
            "modeles": {
                n: {
                    "couleur": d["couleur"],
                    "opacite": d.get("opacite", 1.0),
                    "couleurs_sommets": d.get("couleurs_sommets", False),
                }
                | ({"plateau": d["plateau"]} if d.get("plateau") else {})
                for n, d in self.modeles.items()
            },
            "groupes": [{"nom": g, "visible": v} for g, v in self.groupes.items()],
            # GLB compressé gzip, décompressé par le navigateur pour réduire le poids du HTML.
            "diagnostics": getattr(self, "diagnostics", []),
            "atelier": getattr(self, "atelier", None),
            "contacts": getattr(self, "_contacts", None),
            "pieces": pieces,
            "mecanismes": self.mecanismes,
            "commentaires": getattr(self, "_commentaires", []),
            "glb": base64.b64encode(gzip.compress(glb, 9, mtime=0)).decode("ascii"),
            "glb_gzip": True,
            "empreinte_glb": hashlib.sha256(glb).hexdigest()[:12],
        }
        js = json.dumps(donnees, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        if hasattr(self, "_decoupe"):
            donnees["decoupe"] = self._decoupe
            js = json.dumps(donnees, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        gabarit = (ICI / "gabarit.html").read_text()
        html = gabarit.replace("__TITRE__", _html(self.titre)).replace("__DONNEES__", js)
        html = html.replace("__OUTIL_DECOUPE__", (ICI / "decoupe.js").read_text() if hasattr(self, "_decoupe") else "")
        ressources = ICI / "ressources"
        html = html.replace("__ATELIER_CSS__", (ressources / "atelier-preparation.css").read_text()
                            if self.atelier_actif else "")
        html = html.replace("__ATELIER_JS__", (ressources / "atelier-preparation.js").read_text()
                            if self.atelier_actif else "")
        html = autonome(html)
        with chemin.open("x", encoding="utf-8") as flux:
            flux.write(html)
        r = {
            "fichier": chemin.name,
            "octets": chemin.stat().st_size,
            "hors_ligne": hors_ligne,
            "pieces": len(pieces),
            "modeles": len(self.modeles),
            "mecanismes": len(self.mecanismes),
        }
        r.update(adresse_page(chemin, prive=prive))
        if r["octets"] > LIMITE_OCTETS:
            r["avertissement_taille"] = (
                f"{r['octets']} octets > {LIMITE_OCTETS} : alléger les modèles (max_faces) pour une ouverture fiable"
            )
        return r

    @property
    def atelier_actif(self):
        """Indique si la scène contient la présentation de préparation, sans modifier la scène."""
        return getattr(self, "atelier", None) is not None


def adresse_page(chemin, *, prive=None):
    """Construit une URL locale seulement si la page est sous l'atelier; absence sinon."""
    try:
        racine = atelier_prive(prive) if prive else RACINE
        chemin = sans_lien(chemin)
        return {
            "url_locale": f"http://localhost:{PORT_SERVEUR}/" + chemin.relative_to(racine).as_posix()
        }
    except ValueError:
        return {"avertissement": "Page hors de la racine servie ; aucune adresse locale déduite."}


def _html(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rotation(axe, deg, centre=(0.0, 0.0, 0.0)):
    """Matrice 4×4 de rotation (degrés) autour de l'axe 'X', 'Y', 'Z' ou d'un vecteur, passant par `centre`."""
    v = {"X": [1, 0, 0], "Y": [0, 1, 0], "Z": [0, 0, 1]}.get(axe, axe)
    return trimesh.transformations.rotation_matrix(math.radians(deg), v, centre)


def translation(x=0.0, y=0.0, z=0.0):
    """Construit une translation 4×4 de x, y et z unités de scène sans modifier le maillage."""
    t = np.eye(4)
    t[:3, 3] = [x, y, z]
    return t


def main(argv=None, *, prive=None):
    """Construit une page depuis une source explicitement privée/temporaire; aucun modèle source n'est modifié."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--sortie", type=Path, required=True)
    parser.add_argument("--unites", choices=["mm", "cm", "m"], required=True)
    parser.add_argument("--titre", default="Vue du modèle")
    parser.add_argument("--allegement", type=float)
    parser.add_argument("--placement", type=Path)
    parser.add_argument("--candidats", type=Path)
    parser.add_argument("--tranche", type=Path)
    parser.add_argument("--contacts", type=Path)
    args = parser.parse_args(argv)
    prive = atelier_prive(prive)
    sortie = sans_lien(args.sortie.absolute())
    if not sortie.is_relative_to(prive) and not est_temporaire(sortie):
        raise ValueError("Les pages de projet doivent rester dans prive/ ou /private/tmp")
    source = sans_lien(args.source, strict=True)
    avant = sha256_fichier(source)
    modele = None
    if not args.contacts:
        modele = charger_maillage(source)
        modele.apply_scale({"mm": 1.0, "cm": 10.0, "m": 1000.0}[args.unites])
    scene = Visionneuse(args.titre, source={"fichier": source.name, "sha256": avant, "unites": args.unites})
    if args.contacts:
        if args.unites != "mm":
            raise ValueError("La carte de contacts attend une source explicitement en millimètres")
        from benchab.tiers.modeling import load_parts

        for indice, (nom, mesh) in enumerate(load_parts(source).items()):
            ident = f"objet_{indice + 1}"
            scene.modele(ident, mesh, allegement=args.allegement)
            scene.piece(nom, ident)
        scene.contacts(json.loads(sans_lien(args.contacts, strict=True).read_text()))
    else:
        scene.modele("modele", modele, allegement=args.allegement)
        scene.piece("Pièce", "modele")
    if args.placement:
        if not args.candidats:
            raise ValueError("Le placement requiert --candidats.")
        scene.vue_impression(args.placement, args.candidats, tranche=args.tranche)
    with PublicationNeuve([source, *([args.contacts] if args.contacts else [])], [args.sortie]) as publication:
        with tempfile.TemporaryDirectory(prefix="benchab-visionneuse-") as travail:
            page = Path(travail) / "vue.html"
            resultat = scene.ecrire(page, prive=prive)
            if sha256_fichier(source) != avant:
                raise ValueError("Source modifiée pendant la génération de la page.")
            shutil.copyfile(page, publication.temporaire(args.sortie))
            publication.publier()
    resultat["fichier"] = args.sortie.name
    resultat.pop("avertissement", None)
    resultat.pop("url_locale", None)
    resultat.update(adresse_page(sortie, prive=prive))
    print(json.dumps(resultat, ensure_ascii=False, indent=2))
    return 0
