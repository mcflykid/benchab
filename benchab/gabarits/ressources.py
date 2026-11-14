# ruff: noqa: E501
"""Fichiers génériques des projets privés S/M/L.

Les ressources sont du texte Python empaqueté afin de rester disponibles dans
une installation wheel sans dépendre de fichiers laissés dans le dépôt Source.
"""

COMMUN = {
    "README.md": """# __TITRE__

Projet privé BENCHAB, niveau __NIVEAU__, créé le __DATE__.

## Cycle Python

- `make build` construit les modèles paramétriques et un manifeste local.
- `make plaques` prépare un 3MF depuis les STL construits.
- `make trancher` transmet ce fichier à l’API de tranchage BENCHAB configurée.
- `make preparation ID=P01` enregistre une préparation immuable dans SQLite.

Ces étapes numériques ne prouvent aucun résultat physique. Modifier les cotes
dans `src/parametres.py`, la géométrie dans `src/modele.py` et les exigences
dans `BESOIN.md`. Les réglages de filament sont volontairement à renseigner.
`PROFIL_REFERENCE` peut désigner un 3MF de référence privé : ses réglages sont
copiés puis surchargés par les déclarations explicites du projet. Le rapport
`preuves/profil-source.json` garde la provenance et les différences.
""",
    "BESOIN.md": """# Besoin — __TITRE__

Niveau : __NIVEAU__. Créé le __DATE__.

## Besoin exprimé

À compléter.

## Exigences et critères vérifiables

| ID | Exigence | Vérification | Nature |
|---|---|---|---|
| EX-1 | À compléter | Contrôle numérique ou mesure | À préciser |

## Cotes, matières et hors périmètre

À renseigner. Toute validation physique reste déclarative et datée.
""",
    "CONCEPTION.json": """{
  "schema": 1,
  "objectif": "",
  "type_forme": "technique",
  "dimensions_mm": {},
  "contraintes": [],
  "reference": {"mode": "aucune", "raison": "À renseigner selon le besoin."}
}
""",
    "DECLARATION-ENVOI.json": """{
  "schema": 1,
  "plateau": "À renseigner",
  "filaments": {}
}
""",
    "ETAT.md": """# État — __TITRE__

Créé le __DATE__. Niveau __NIVEAU__. Statut : conception, fabrication non vérifiée.

## Vérifié / calculé / déclaré

- Vérifié physiquement : aucun.
- Calculé : aucun.
- À renseigner : besoin, cotes, machine et matériaux.

## Préparations

Aucune.
""",
    "DECISIONS.md": """# Décisions — __TITRE__

- __DATE__ : projet créé au niveau __NIVEAU__ ; aucune décision de fabrication.
""",
    ".gitignore": """__pycache__/\n*.py[cod]\n!/src/*.py\n.caches/\n""",
    "organisation/organisation.json": """{
  "schema": 1,
  "niveau": "__NIVEAU__",
  "mode": "A_CONFIGURER",
  "suivi": [],
  "cibles": {
    "build": {"commande": "make build", "apres": []},
    "plaques": {"commande": "make plaques", "apres": ["build"]},
    "trancher": {"commande": "make trancher", "apres": ["plaques"]}
  },
  "automatisations": [],
  "lecture_minimale": ["README.md", "ETAT.md", "BESOIN.md"]
}
""",
    "Makefile": """PYTHON ?= __PYTHON__
OPTIMISER ?= 0
OBJECTIF ?=
.PHONY: build plaques trancher controles interfaces qualite references visualiser preparation optimiser tout

build:
\t$(PYTHON) -m src.construire
plaques: build
\t$(PYTHON) -m src.preparer
trancher:
\t$(PYTHON) -m src.trancher
controles: build
\t$(PYTHON) -m benchab.controles.controles_piece candidats/*.stl --json preuves/controles-pieces.json
references:
\t$(PYTHON) -m src.references
interfaces:
\t$(PYTHON) -m src.controler_interfaces
visualiser: build
\t$(PYTHON) -m src.visualiser
preparation:
\t$(PYTHON) -m src.preparation --id $(ID)
optimiser:
\t$(PYTHON) -m src.optimiser $(if $(OBJECTIF),--objectif $(OBJECTIF),)
tout:
\t$(PYTHON) -m benchab.fabrication.chaine . $(if $(filter 1,$(OPTIMISER)),--optimiser,) $(if $(OBJECTIF),--objectif $(OBJECTIF),); test $$? -le 1
""",
    "src/__init__.py": """\"\"\"Code paramétrique privé du projet __NOM__.\"\"\"\n""",
    "src/parametres.py": """\"\"\"Entrées paramétriques privées du projet. Cotes en mm, températures en °C.\"\"\"
P = {"titre": __TITRE_JSON__, "longueur": 40.0, "largeur": 20.0, "epaisseur": 4.0}
# Aucun filament, profil ou réglage personnel n'est présumé.
FILAMENTS = {}
PROFIL_REFERENCE = None
REGLAGES = {"layer_height": "0.2", "wall_loops": "3", "sparse_infill_density": "20%", "sparse_infill_pattern": "gyroid"}
PLAQUES = [{"name": "Plaque", "items": [{"piece": "Piece", "copies": 1, "extruder": 1, "reglages": {}}]}]
# FILAMENTS doit définir explicitement type, color, id, settings_id et les quatre valeurs thermiques/débit.
CONTRAINTES = {}
CAVITES = {}
ZONES_REMPLISSAGE = {}
# Texture de surface facultative : variante numérique distincte, la source reste intacte.
TEXTURES = {}
VUE = {"Piece": {"couleur": "os", "groupe": "Pièces"}}
REMPLISSAGE = {}
LISSAGE = {}
EFFORTS = {}
""",
    "src/modele.py": """\"\"\"Géométrie paramétrique privée : pieces(P) renvoie des solides build123d.\"\"\"
from build123d import Axis, Box, fillet

def pieces(parametres):
    bloc = Box(parametres["longueur"], parametres["largeur"], parametres["epaisseur"])
    return {"Piece": fillet(bloc.edges().filter_by(Axis.Z), 1.0)}
""",
    "src/construire.py": """\"\"\"Construit les STL/STEP privés et leur manifeste, sans tranchage.\"\"\"
import hashlib
import json
from pathlib import Path
from build123d import Location, export_step, export_stl
from benchab.qualite.ecritures import PublicationNeuve
from .modele import pieces
from .parametres import P

def main(argv=None):
    projet = Path(__file__).resolve().parents[1]
    sortie = projet / "candidats"
    sources = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((projet / "src").glob("*.py"))}
    manifeste = {"schema": 1, "sources_sha256": sources, "parametres": P, "pieces": {}, "models": {}, "parts": []}
    solides = pieces(P)
    for nom in solides:
        if not nom.replace("_", "a").isalnum() or not nom[0].isalpha():
            raise ValueError("Nom de pièce invalide")
    cible = sortie / "assemblage.json"
    destinations = [cible, *[sortie / (nom + suffixe) for nom in solides for suffixe in (".stl", ".step")]]
    with PublicationNeuve([projet / "src" / nom for nom in sources], destinations) as publication:
        for nom, solide in solides.items():
            boite = solide.bounding_box()
            solide = solide.moved(Location((-boite.min.X, -boite.min.Y, -boite.min.Z)))
            stl, step = sortie / (nom + ".stl"), sortie / (nom + ".step")
            export_stl(solide, str(publication.temporaire(stl)), tolerance=.005, angular_tolerance=.05)
            export_step(solide, str(publication.temporaire(step)))
            sha = hashlib.sha256(publication.temporaire(stl).read_bytes()).hexdigest()
            manifeste["pieces"][nom] = {"stl_sha256": sha, "volume_mm3": round(solide.volume, 3)}
            manifeste["models"][nom] = {"stl_sha256": sha, "print_sha256": sha, "printable": True}
            manifeste["parts"].append({"name": nom, "model": nom})
        publication.temporaire(cible).write_text(json.dumps(manifeste, ensure_ascii=False, indent=2) + "\\n")
        publication.publier()
    return cible

if __name__ == "__main__":
    print(main())
""",
    "src/preparer.py": """\"\"\"Crée un 3MF multi-plaques privé depuis les STL candidats.\"\"\"
import json
import math
from pathlib import Path
import trimesh
from benchab.conception import optimisation_interne
from benchab.conception import texture as texture_surface
from benchab.fabrication import anycubic
from benchab.fabrication import remplissage_localise
from benchab import reglages as schema_reglages
from benchab.controles.decollement import evaluer as evaluer_appui
from benchab.qualite.ecritures import PublicationNeuve, sortie_privee, valider_chemins
from benchab.qualite.empreintes import sha256_fichier
from . import parametres as PA

FILAMENTS = PA.FILAMENTS
PLAQUES = PA.PLAQUES
REGLAGES = PA.REGLAGES
CAVITES = getattr(PA, "CAVITES", {})
CONTRAINTES = getattr(PA, "CONTRAINTES", {})
LISSAGE = getattr(PA, "LISSAGE", {})
ZONES_REMPLISSAGE = getattr(PA, "ZONES_REMPLISSAGE", {})
TEXTURES = getattr(PA, "TEXTURES", {})

def _filaments_valides():
    if not isinstance(FILAMENTS, dict) or not FILAMENTS:
        raise ValueError("Renseigner FILAMENTS avant de préparer; chaque filament doit déclarer type, color, id et settings_id")
    cles = sorted(FILAMENTS, key=lambda cle: int(cle) if str(cle).isdigit() else -1)
    if cles != [str(index) for index in range(1, len(cles) + 1)]:
        raise ValueError("FILAMENTS doit utiliser des clés numériques continues à partir de '1'")
    champs = ("type", "color", "id", "settings_id", "temp", "temp_1re", "plateau_c", "debit_max")
    for cle in cles:
        filament = FILAMENTS[cle]
        if not isinstance(filament, dict) or any(
            not isinstance(filament.get(champ), str) or not filament[champ].strip()
            for champ in champs[:4]
        ):
            raise ValueError(f"FILAMENTS[{cle!r}] doit fournir explicitement type, color, id et settings_id non vides")
        for champ in champs[4:]:
            valeur = filament.get(champ)
            if isinstance(valeur, bool) or not isinstance(valeur, (int, float)) or not math.isfinite(valeur) or valeur <= 0:
                raise ValueError(f"FILAMENTS[{cle!r}][{champ!r}] doit être un nombre positif explicite")
    return [FILAMENTS[cle] for cle in cles]

def main(argv=None):
    '''Prépare un 3MF depuis les paramètres privés (mm, °C, mm³/s), renvoie son chemin.

    Aucun statut physique ; conserve les réglages déclarés et ne lance jamais le slicer.
    '''
    projet = Path(__file__).resolve().parents[1]
    candidates = projet / "candidats"
    manifeste = json.loads((candidates / "assemblage.json").read_text())
    filaments = _filaments_valides()
    sortie = candidates / "plaques.3mf"
    preuves = projet / "preuves"
    entrees = [candidates / "assemblage.json", *sorted((projet / "src").glob("*.py"))]
    profil_source = getattr(PA, "PROFIL_REFERENCE", None)
    base_settings, provenance_profil = {}, None
    if profil_source is not None:
        profil_source = Path(profil_source)
        if not profil_source.is_absolute():
            profil_source = projet / profil_source
        profil_source = sortie_privee(profil_source, prive=projet.parent.parent)
        base_settings, provenance_profil = anycubic.charger_reglages_reference(profil_source)
        entrees.append(profil_source)
    for nom in manifeste["pieces"]:
        if not nom or not nom.replace("_", "a").isalnum() or not nom[0].isalpha():
            raise ValueError("Nom de pièce invalide")
        entrees.append(candidates / (nom + ".stl"))
    if not isinstance(TEXTURES, dict) or any(not isinstance(value, dict) or not value for value in TEXTURES.values()):
        raise ValueError("TEXTURES doit associer les noms de pièces à leurs paramètres de texture")
    if set(CAVITES) - set(manifeste["pieces"]) or set(ZONES_REMPLISSAGE) - set(manifeste["pieces"]) or set(TEXTURES) - set(manifeste["pieces"]):
        raise ValueError("Cavité, zone ou texture liée à une pièce non construite")
    pieces_sur_plaques = set()
    if isinstance(PLAQUES, list):
        for plaque in PLAQUES:
            if isinstance(plaque, dict) and isinstance(plaque.get("items"), list):
                pieces_sur_plaques.update(item.get("piece") for item in plaque["items"] if isinstance(item, dict))
    if set(TEXTURES) - pieces_sur_plaques:
        raise ValueError("TEXTURES doit viser une pièce déclarée sur PLAQUES")
    destinations = [sortie, preuves / "placement.json"]
    if provenance_profil is not None:
        destinations.append(preuves / "profil-source.json")
    if any(CAVITES.values()):
        destinations.append(preuves / "optimisation-interne.json")
    if any(ZONES_REMPLISSAGE.values()):
        destinations.append(preuves / "remplissage-zones.json")
    if any(TEXTURES.values()):
        destinations.append(preuves / "textures.json")
    destinations.extend(candidates / "optimise" / (nom + ".stl") for nom, cavites in CAVITES.items() if cavites)
    destinations.extend(candidates / "texture" / (nom + ".stl") for nom, texture in TEXTURES.items() if texture)
    valider_chemins(entrees, destinations)
    plaques = []
    index = []
    if not isinstance(PLAQUES, list) or not PLAQUES:
        raise ValueError("PLAQUES doit contenir au moins une plaque")
    rapports_cavites = {}
    rapports_zones = {}
    rapports_textures = {}
    meshes = {}
    pieces_optimisees = set()
    variantes_optimisees = {}
    variantes_textures = {}
    for numero, plaque in enumerate(PLAQUES):
        items = []
        if not isinstance(plaque, dict) or not isinstance(plaque.get("items"), list):
            raise ValueError(f"PLAQUES[{numero}] doit contenir une liste items")
        for item in plaque["items"]:
            if not isinstance(item, dict):
                raise ValueError("Chaque item de PLAQUES doit être un dictionnaire")
            nom = item["piece"]
            if nom not in manifeste["pieces"]:
                raise ValueError("Pièce non construite : " + nom)
            copies = item.get("copies", 1)
            if isinstance(copies, bool) or not isinstance(copies, int) or not 1 <= copies <= 100:
                raise ValueError(f"Quantité de copies invalide pour {nom!r}; entier entre 1 et 100 requis")
            extruder = item.get("extruder", 1)
            if isinstance(extruder, bool) or not isinstance(extruder, int) or not 1 <= extruder <= len(filaments):
                raise ValueError(f"Extrudeur invalide pour {nom!r}; il doit correspondre à FILAMENTS")
            if nom not in meshes:
                mesh = trimesh.load_mesh(candidates / (nom + ".stl"), process=True)
                if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
                    raise ValueError(f"Maillage invalide pour {nom!r}")
                mesh.apply_translation(-mesh.bounds[0])
                if TEXTURES.get(nom):
                    # La texture est un essai géométrique distinct; son rapport n'est pas une validation physique (docs/explications/preuves.md).
                    mesh_texturee, rapport = texture_surface.appliquer(mesh, TEXTURES[nom], CONTRAINTES.get(nom))
                    if rapport.get("statut") == "FAIL":
                        raise ValueError(f"Texture refusée pour {nom!r}: contrôle géométrique en échec")
                    rapports_textures[nom] = rapport
                    variantes_textures[candidates / "texture" / (nom + ".stl")] = mesh_texturee.copy()
                    mesh = mesh_texturee
                if CAVITES.get(nom):
                    mesh, rapport = optimisation_interne.appliquer(mesh, CAVITES[nom], CONTRAINTES.get(nom))
                    if rapport.get("statut") == "FAIL":
                        raise ValueError(f"Cavités refusées pour {nom!r}: contrôles géométriques en échec")
                    rapports_cavites[nom] = rapport
                    pieces_optimisees.add(nom)
                meshes[nom] = mesh
            mesh = meshes[nom]
            zones = ZONES_REMPLISSAGE.get(nom, [])
            volumes, bilan = remplissage_localise.volumes(mesh, zones) if zones else ([], [])
            if zones:
                rapports_zones[nom] = bilan
            reglages_objet = schema_reglages.Reglages(
                {str(cle): str(valeur) for cle, valeur in item.get("reglages", {}).items()}
            )
            if nom in LISSAGE:
                mode = LISSAGE[nom]
                if mode not in ("top", "topmost"):
                    raise ValueError(f"LISSAGE[{nom!r}] doit valoir 'top' ou 'topmost'")
                # La surcharge explicite de l'objet reste prioritaire sur le conseil LISSAGE
                # (docs/reference/profils-et-procedes.md) ; aucun réglage choisi n'est remplacé.
                reglages_objet.setdefault("ironing_type", mode)
            for copie in range(copies):
                nom_objet = nom if copies == 1 else f"{nom}_{copie + 1}"
                instance = mesh.copy()
                if nom in pieces_optimisees:
                    variantes_optimisees[candidates / "optimise" / (nom + ".stl")] = mesh.copy()
                    pieces_optimisees.discard(nom)
                items.append({"name": nom_objet, "model": nom, "mesh": instance,
                              "w": float(instance.extents[0]), "h": float(instance.extents[1]),
                              "extruder": extruder, "settings": reglages_objet, "volumes": volumes})
        ecart = plaque.get("ecart_mm", 12.0)
        if isinstance(ecart, bool) or not isinstance(ecart, (int, float)) or not math.isfinite(ecart) or ecart < 0:
            raise ValueError(f"PLAQUES[{numero}].ecart_mm doit être un nombre fini positif ou nul")
        anycubic.ranger(items, numero, len(PLAQUES), ecart)
        plaques.append({"name": plaque["name"], "items": items})
        for item in items:
            appui = evaluer_appui(item["mesh"])
            index.append({"object_id": len(index) + 1, "name": item["name"], "model": item["model"], "plate": numero + 1,
                          "xy_interface": [item["x"], item["y"]], "size_mm": [item["w"], item["h"]],
                          "size": [float(cote) for cote in item["mesh"].extents],
                          "accroche": {"assise_mm2": appui["appui_mm2"], "elancement": appui["elancement"],
                              "source": "contrôle numérique commun decollement.evaluer ; aucune adhérence physique mesurée"},
                          "xy": [item["x"], item["y"]], "rot_z_deg": 0,
                          "reglages": item["settings"]})
    surcharges = {**REGLAGES,
                "filament_settings_id": [f["settings_id"] for f in filaments],
                "filament_type": [f["type"] for f in filaments],
                "filament_colour": [f["color"] for f in filaments],
                "filament_ids": [f["id"] for f in filaments],
                "nozzle_temperature": [str(f["temp"]) for f in filaments],
                "nozzle_temperature_initial_layer": [str(f["temp_1re"]) for f in filaments],
                "textured_plate_temp": [str(f["plateau_c"]) for f in filaments],
                "textured_plate_temp_initial_layer": [str(f["plateau_c"]) for f in filaments],
                "filament_max_volumetric_speed": [str(f["debit_max"]) for f in filaments]}
    # Les valeurs viennent des filaments explicitement choisis, jamais d'une
    # réduction de vecteurs machine (docs/reference/integrite-3mf.md).
    contexte = schema_reglages.contexte_filaments(surcharges, consommateur="cli")
    settings = schema_reglages.Reglages(base_settings, contexte=contexte)
    settings.update(surcharges)
    for cle in ("nozzle_temperature", "nozzle_temperature_initial_layer", "textured_plate_temp", "textured_plate_temp_initial_layer", "filament_max_volumetric_speed", *REGLAGES):
        for suffixe in ("_BRASS", "_HS"):
            if cle + suffixe in base_settings:
                contexte_suffixe = schema_reglages.contexte_filaments(
                    {"filament_settings_id": [f["settings_id"] for f in filaments],
                     cle + suffixe: settings[cle]}, consommateur="cli")
                settings.contexte["correspondances_filaments"].update(
                    contexte_suffixe["correspondances_filaments"])
                settings[cle + suffixe] = settings[cle]
    # Le schéma commun est aussi lu par la routine, pas seulement par le slicer
    # (docs/reference/controles.md, disposition réelle).
    for objet in index:
        # Le contrôle d'envoi doit voir la valeur effective, y compris l'héritage
        # projet (docs/reference/controles.md), et non seulement la surcharge locale.
        objet["reglages_effectifs"] = {
            **{cle: schema_reglages.valeur_brute(settings, cle)
               for cle in ("brim_type", "brim_width", "enable_support", "layer_height") if cle in settings},
            **objet["reglages"],
        }
    rapports = {preuves / "placement.json": {"schema": 1,
        "assembly_manifest_sha256": sha256_fichier(candidates / "assemblage.json"), "objects": index,
        "plates": [{"index": n + 1, "name": p["name"], "objects": len(p["items"])}
                   for n, p in enumerate(plaques)]}}
    if provenance_profil is not None:
        rapports[preuves / "profil-source.json"] = {**provenance_profil,
            "source": str(profil_source),
            "surcharges_declarees": {cle: {"avant": base_settings.get(cle), "apres": valeur}
                for cle, valeur in settings.items() if base_settings.get(cle) != valeur},
            "validation_physique": False}
    if rapports_cavites:
        rapports[preuves / "optimisation-interne.json"] = rapports_cavites
    if rapports_zones:
        rapports[preuves / "remplissage-zones.json"] = rapports_zones
    if rapports_textures:
        rapports[preuves / "textures.json"] = rapports_textures
    with PublicationNeuve(entrees, [sortie, *rapports, *variantes_optimisees, *variantes_textures]) as publication:
        if provenance_profil is not None and sha256_fichier(profil_source) != provenance_profil["source_sha256"]:
            raise ValueError("Profil de référence modifié avant la préparation")
        anycubic.ecrire_3mf(plaques, settings, publication.temporaire(sortie), nommer_source=True,
                           canonique=True, nom_source=sortie.name)
        for cible, mesh in variantes_optimisees.items():
            mesh.export(publication.temporaire(cible))
        for cible, mesh in variantes_textures.items():
            mesh.export(publication.temporaire(cible))
        for cible, contenu in rapports.items():
            publication.temporaire(cible).write_text(json.dumps(contenu, ensure_ascii=False, indent=2) + "\\n")
        publication.publier()
    return sortie

if __name__ == "__main__":
    print(main())
""",
    "src/controler_interfaces.py": """\"\"\"Indique que le niveau S ne déclare pas d'interfaces d'assemblage.\"\"\"
def main(argv=None):
    print("NON APPLICABLE — niveau S, aucune interface déclarée")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
""",
    "src/trancher.py": """\"\"\"Délègue à l'API commune de tranchage privé BENCHAB.\"\"\"
from pathlib import Path
from benchab.fabrication.trancher import trancher_fichier
from benchab.fabrication.anycubic import resultats
from benchab.qualite.ecritures import PublicationNeuve, valider_chemins
from .parametres import FILAMENTS, REGLAGES

def attendu():
    \"\"\"Renvoie les attentes explicites °C, mm³/s et unités natives ; aucun profil ni réglage inventé.\"\"\"
    filaments = [FILAMENTS[k] for k in sorted(FILAMENTS, key=int)]
    if not filaments:
        raise ValueError("Renseigner FILAMENTS avant le tranchage")
    return {**REGLAGES,
            "nozzle_temperature": [str(f["temp"]) for f in filaments],
            "nozzle_temperature_initial_layer": [str(f["temp_1re"]) for f in filaments],
            "textured_plate_temp": [str(f["plateau_c"]) for f in filaments],
            "textured_plate_temp_initial_layer": [str(f["plateau_c"]) for f in filaments],
            "filament_max_volumetric_speed": [str(f["debit_max"]) for f in filaments]}

def main(argv=None):
    \"\"\"Livre le 3MF et les estimations en s/g ; erreur si résultat absent, jamais une mesure physique.\"\"\"
    projet = Path(__file__).resolve().parents[1]
    prive = projet.parent.parent
    source = projet / "candidats/plaques.3mf"
    sortie = projet / "preuves/tranchage-natif"
    final = projet / "candidats/tranche.3mf"
    rapport_cible = projet / "preuves/tranchage.json"
    valider_chemins([source], [final, rapport_cible])
    path, rapport = trancher_fichier(source, sortie, "tranche.3mf", prive=prive, journal=projet / "preuves/tranchage.log", attendu=attendu())
    # Les estimations par plaque alimentent le contrôle d'envoi et le budget,
    # sans devenir des temps physiques (docs/explications/preuves.md).
    rapport = {**rapport, "plaques": resultats(path)}
    with PublicationNeuve([source, path], [final, rapport_cible]) as publication:
        with publication.temporaire(final).open("xb") as destination, path.open("rb") as original:
            __import__("shutil").copyfileobj(original, destination)
        publication.temporaire(rapport_cible).write_text(__import__("json").dumps(rapport, ensure_ascii=False, indent=2) + "\\n")
        publication.publier()
    return final

if __name__ == "__main__":
    print(main())
""",
    "src/references.py": """\"\"\"Contrôle local des références déclarées, sans accès réseau.\"\"\"
from pathlib import Path
from benchab.qualite.references import verify

def main(argv=None):
    projet = Path(__file__).resolve().parents[1]
    rapport = verify(projet, historical=False, anchors=True, cache=False)
    print(rapport)
    return 0 if rapport["ok"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
""",
    "src/visualiser.py": """\"\"\"Génère les vues d'assemblage privées ; n'ouvre ni navigateur ni imprimante.\"\"\"
import json
from pathlib import Path
import trimesh
from benchab.visualisation.visionneuse import Visionneuse
from . import parametres as PA

def main(argv=None):
    \"\"\"Compose les poses et groupes d'inspection (mm), renvoie le HTML ; ne modifie aucun modèle ni réglage.\"\"\"
    projet = Path(__file__).resolve().parents[1]
    candidats = projet / "candidats"
    manifeste = json.loads((candidats / "assemblage.json").read_text())
    noms = list(manifeste["pieces"])
    vue = Visionneuse(PA.P.get("titre", projet.name), sous_titre=PA.P.get("sous_titre", ""),
                      note="Inspection numérique seulement ; aucune validation physique.", axe_haut="Z")
    for nom in noms:
        assemble = candidats / "assemblage" / (nom + ".stl")
        fichier = assemble if assemble.is_file() else candidats / (nom + ".stl")
        options = PA.VUE.get(nom, {})
        vue.modele(nom, fichier, couleur=options.get("couleur", "os"))
        # Les poses du projet portent le montage réel ; ne pas le remplacer par la pose d'impression
        # (docs/guides/cycle-projet.md, inspection numérique).
        vue.piece(nom, nom, groupe=options.get("groupe", "Pièces"), matrice=options.get("matrice"))
    references = sorted((projet / "reference").glob("*/assemblage"), key=lambda chemin: chemin.stat().st_mtime)
    if references:
        vue.comparer(references[-1].parent.name, sorted(references[-1].glob("*.stl")), pieces=noms)
    if len(noms) > 1:
        vue.montage([{"texte": f"poser {nom}", "pieces": [nom], "depuis": (0, 0, 60)} for nom in noms])
    zones = getattr(PA, "ZONES_REMPLISSAGE", {})
    if any(zones.values()):
        decalage = max(trimesh.load_mesh(candidats / (nom + ".stl")).bounds[1, 0] for nom in noms) + 30
        fichier_rapport = projet / "preuves/remplissages.json"
        rapports = json.loads(fichier_rapport.read_text()).get("pieces", {}) if fichier_rapport.is_file() else {}
        for nom, regions in zones.items():
            if nom in noms and regions:
                rapport = rapports.get(nom, {})
                texte = f"Référence {rapport.get('grammes')} g, {rapport.get('temps_h')} h" if rapport else None
                vue.zones_remplissage(nom, candidats / (nom + ".stl"), regions,
                                     decalage=(decalage, 0, 0), resultats=texte)
                decalage += 60
    cavites = getattr(PA, "CAVITES", {})
    if any(cavites.values()):
        decalage = max(trimesh.load_mesh(candidats / (nom + ".stl")).bounds[1, 0] for nom in noms) + 30
        fichier_rapport = projet / "preuves/optimisation-interne.json"
        rapports = json.loads(fichier_rapport.read_text()) if fichier_rapport.is_file() else {}
        for nom, regions in cavites.items():
            if nom in noms and regions:
                vue.optimisation_interne(nom, candidats / (nom + ".stl"), regions,
                                         getattr(PA, "CONTRAINTES", {}).get(nom), rapports.get(nom),
                                         decalage=(decalage, -80, 0), variante=candidats / "optimise" / (nom + ".stl"))
                decalage += 100
    if (projet / "preuves/placement.json").is_file():
        vue.vue_impression(projet / "preuves/placement.json", candidats, candidats / "tranche.3mf",
                           projet / "preuves/tranchage.json")
    sortie = projet / "preuves/visualisation/projet-3d.html"
    sortie.parent.mkdir(parents=True, exist_ok=True)
    if sortie.exists():
        raise FileExistsError("Aperçu existant ; aucun écrasement")
    return vue.ecrire(sortie)

if __name__ == "__main__":
    print(main())
""",
    "src/preparation.py": """\"\"\"Crée une préparation candidate immuable via le service BENCHAB.\"\"\"
import argparse
from pathlib import Path
from benchab.fabrication.preparation import main as preparation_main

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True)
    parser.add_argument("--precedente")
    args = parser.parse_args(argv)
    projet = Path(__file__).resolve().parents[1]
    prive = projet.parent.parent
    sources = [str(p) for p in sorted((projet / "src").glob("*.py"))]
    appel = ["--prive", str(prive), "creer", "--projet", str(projet), "--id", args.id,
             "--plaques", str(projet / "candidats/plaques.3mf"), "--tranche", str(projet / "candidats/tranche.3mf"),
             "--sources", *sources, "--modeles", *[str(p) for p in sorted((projet / "candidats").glob("*.stl"))],
             "--controles", *[str(p) for p in sorted((projet / "preuves").glob("*.json"))]]
    if args.precedente:
        appel.extend(["--precedente", args.precedente])
    return preparation_main(appel)

if __name__ == "__main__":
    raise SystemExit(main())
""",
    "src/optimiser.py": """\"\"\"Délègue la comparaison facultative à l'API BENCHAB.\"\"\"
import argparse
from pathlib import Path
from benchab.fabrication.optimisation import main as optimisation_main

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objectif", choices=("qualite", "equilibre", "rapide", "resistance"))
    args = parser.parse_args(argv)
    projet = Path(__file__).resolve().parents[1]
    appel = ["--prive", str(projet.parent.parent), "comparer", "--projet", str(projet), "--source", str(projet / "candidats/plaques.3mf")]
    if args.objectif:
        appel.extend(["--objectif", args.objectif])
    return optimisation_main(appel)

if __name__ == "__main__":
    raise SystemExit(main())
""",
    "src/remplissages.py": """\"\"\"Compare les remplissages déclarés via le comparateur BENCHAB.\"\"\"
import json
import traceback
from pathlib import Path
from benchab.fabrication import comparer_remplissages as comparer
from .parametres import CAVITES, EFFORTS, FILAMENTS, PLAQUES, REMPLISSAGE, REGLAGES, ZONES_REMPLISSAGE
from benchab.qualite.ecritures import ecrire_json_neuf, valider_chemins

def _etudier(argv=None):
    \"\"\"Compare les variantes déclarées et renvoie 0/1/2 pour PASS/WARN/FAIL, sans modifier les réglages.\"\"\"
    projet = Path(__file__).resolve().parents[1]
    base = projet / "candidats/plaques.3mf"
    if not REMPLISSAGE:
        rapport = {"statut": "WARN", "detail": "Usage non déclaré dans REMPLISSAGE : choix de remplissage à justifier."}
        sortie = projet / "preuves/remplissages.json"
        ecrire_json_neuf(sortie, rapport)
        return 1
    if not FILAMENTS or not base.is_file():
        raise ValueError("Préparer les plaques et déclarer les filaments avant l'étude")
    pieces, avertissements = comparer.construire_pieces_remplissages(
        PLAQUES, REGLAGES, REMPLISSAGE, zones=ZONES_REMPLISSAGE, cavites=CAVITES, efforts=EFFORTS
    )
    if not pieces:
        rapport = {"statut": "WARN" if avertissements else "NON APPLICABLE", "pieces": {a["piece"]: a for a in avertissements}}
        valider_chemins([], [projet / "preuves/remplissages.json"])
        ecrire_json_neuf(projet / "preuves/remplissages.json", rapport)
        return 1 if avertissements else 0
    dossier = projet / "preuves/remplissages"
    valider_chemins([], [dossier])
    dossier.mkdir(parents=True, exist_ok=False)
    etude = dossier / "etude.json"
    ecrire_json_neuf(etude, {"base_3mf": "../../candidats/plaques.3mf", "sortie": "resultat", "pieces": pieces})
    code = comparer.cli(["--prive", str(projet.parent.parent), str(etude)])
    if code != 0:
        raise RuntimeError(f"Comparaison des remplissages interrompue (code {code}).")
    comparaison = json.loads((dossier / "resultat/comparaison.json").read_text(encoding="utf-8"))
    rapport = comparer.rapport_remplissages(pieces, comparaison, avertissements)
    valider_chemins([], [projet / "preuves/remplissages.json"])
    ecrire_json_neuf(projet / "preuves/remplissages.json", rapport)
    return 2 if rapport["statut"] == "FAIL" else 1 if rapport["statut"] == "WARN" else 0

def main(argv=None):
    \"\"\"Étudie le remplissage, durées en h et masses en g ; codes PASS0/WARN1/FAIL2, sans changer de réglage.\"\"\"
    rapport = Path(__file__).resolve().parents[1] / "preuves/remplissages.json"
    # Une preuve existante n'est jamais réutilisée comme résultat du nouvel essai ;
    # l'absence ou l'échec reste bloquant (docs/reference/controles.md).
    try:
        valider_chemins([], [rapport])
        return _etudier(argv)
    except BaseException as erreur:
        if not rapport.exists():
            ecrire_json_neuf(rapport, {"statut": "FAIL", "detail": f"Étude interrompue : {type(erreur).__name__}: {erreur}"})
        if isinstance(erreur, Exception):
            traceback.print_exc()
            return 2
        raise

if __name__ == "__main__":
    raise SystemExit(main())
""",
    "src/lissage.py": """\"\"\"Chiffre le lissage déclaré en étude privée, sans remplacer les réglages.\"\"\"
import json
from pathlib import Path
from benchab.fabrication import comparer_remplissages as comparer
from .parametres import FILAMENTS, PLAQUES, LISSAGE, REGLAGES
from benchab.qualite.ecritures import ecrire_json_neuf, valider_chemins

def main(argv=None):
    \"\"\"Mesure le coût temporel du lissage et renvoie 0/1/2 pour PASS/WARN/FAIL.\"\"\"
    projet = Path(__file__).resolve().parents[1]
    base = projet / "candidats/plaques.3mf"
    sortie = projet / "preuves/lissage.json"
    if not LISSAGE:
        ecrire_json_neuf(sortie, {"statut": "NON APPLICABLE", "detail": "Aucune pièce déclarée dans LISSAGE."})
        return 0
    if not FILAMENTS or not base.is_file():
        raise ValueError("Préparer les plaques et déclarer les filaments avant l'étude")
    pieces = []
    vus = set()
    for plaque in PLAQUES:
        for item in plaque["items"]:
            mode = LISSAGE.get(item["piece"])
            if mode and item["piece"] not in vus:
                # Une pièce sur plusieurs plaques ne doit pas multiplier l'étude
                # du même lissage (docs/reference/profils-et-procedes.md).
                vus.add(item["piece"])
                ref = {**REGLAGES, **item.get("reglages", {}), "ironing_type": "no ironing"}
                pieces.append({"nom": item["piece"], "mode": mode, "stl": "../../candidats/" + item["piece"] + ".stl", "extruder": item.get("extruder", 1),
                               "reference": ref, "variantes": [{"nom": "lissage " + mode, "reglages": {**ref, "ironing_type": mode}, "acceptable": True,
                                                                  "justification": "Dessus visibles déclarés."}]})
    if not pieces: raise ValueError("Aucune pièce couverte par LISSAGE")
    dossier = projet / "preuves/lissage-etude"
    valider_chemins([], [dossier, sortie])
    dossier.mkdir(parents=True, exist_ok=False)
    etude = dossier / "etude.json"
    ecrire_json_neuf(etude, {"base_3mf": "../../candidats/plaques.3mf", "sortie": "resultat", "pieces": pieces})
    code = comparer.cli(["--prive", str(projet.parent.parent), str(etude)])
    if code != 0:
        raise RuntimeError(f"Comparaison du lissage interrompue (code {code}).")
    comparaison = json.loads((dossier / "resultat/comparaison.json").read_text(encoding="utf-8"))
    resultat = comparer.rapport_lissage(pieces, comparaison)
    resultat["limite"] = "Comparaison numérique seulement ; l'aspect reste à vérifier physiquement."
    ecrire_json_neuf(sortie, resultat)
    return 2 if resultat["statut"] == "FAIL" else 1 if resultat["statut"] == "WARN" else 0

if __name__ == "__main__":
    raise SystemExit(main())
""",
    "fiche-essai.md": """# Essai — date / objet / identifiant

Statut : proposé / tranché / retour utilisateur / contrôle mesuré / abandonné.
Machine, firmware, buse, filament et conditions réelles : à consigner.
Projet et empreintes des préparations : à consigner.
Changements et paramètres retenus : à consigner.
Durée prévue et observée : à distinguer.
Retour utilisateur exact, date et limites : à consigner.
""",
}

M = {
    "interfaces.json": """{
  "schema": 1,
  "interfaces": [],
  "exemple": {"id": "IF-1", "type": "coulissement", "piece_a": "Piece", "piece_b": "Autre",
    "cote_nominale": 6.0, "jeu_radial": 0.4, "justification": "À préciser", "mesure": null}
}
""",
    "src/controler_interfaces.py": """\"\"\"Contrôle les jeux déclarés et mesurés entre pièces d'un projet M/L.\"\"\"
import json, math
from pathlib import Path
import numpy as np
import trimesh
from shapely.geometry import Point, Polygon
from benchab.qualite.ecritures import ecrire_json_neuf, valider_chemins, _sans_lien
from benchab.qualite.empreintes import sha256_fichier

POLITIQUE = {"rotation_libre": .45, "coulissement": .4, "emboitement_forme": .4, "passage": .4,
             "pion_rainure": .35, "emboitement_impose": .35, "filet": .4, "axe_fourche": .3, "axe_noeud": .35,
             "axial": .4, "essai": None}

def diametre(projet, piece, xy, z, interieur):
    \"\"\"Mesure un diamètre équivalent en mm dans une coupe z/mm ; None si absent, aucune validation physique.\"\"\"
    fichier = projet / "candidats" / (piece + ".stl")
    if not fichier.is_file():
        return None
    mesh = trimesh.load_mesh(fichier, process=False)
    section = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if section is None:
        return None
    polygones, matrice = section.to_2D()
    point = (np.linalg.inv(matrice) @ np.array([xy[0], xy[1], z, 1.]))[:2]
    for poly in polygones.polygons_full:
        rings = list(poly.interiors) if interieur else [poly.exterior]
        for ring in rings:
            forme = Polygon(ring)
            if forme.contains(Point(point)):
                return 2 * math.sqrt(forme.area / math.pi)
    return None

def verifier(projet=None):
    \"\"\"Contrôle jeux/mm et SHA du partenaire privé ; PASS/WARN/FAIL, un essai justifié reste NON APPLICABLE.\"\"\"
    projet = Path(projet or Path(__file__).resolve().parents[1])
    sortie = projet / "preuves/interfaces.json"
    valider_chemins([projet / "interfaces.json"], [sortie])
    donnees = json.loads((projet / "interfaces.json").read_text())
    resultats = []
    for item in donnees.get("interfaces", []):
        mini = POLITIQUE.get(item.get("type"), "inconnu")
        if mini == "inconnu":
            statut = "FAIL"
        elif mini is None:
            statut = "NON APPLICABLE" if item.get("justification") else "FAIL"
        else:
            statut = "PASS" if item.get("jeu_radial", -1) >= mini else "WARN"
        ligne = {"id": item.get("id"), "politique": statut, "minimum_mm": mini}
        mesure = item.get("mesure")
        if mesure:
            trou, arbre = mesure["trou"], mesure["arbre"]
            dt = diametre(projet, trou["piece"], trou["xy"], trou["z"], True)
            da = diametre(projet, arbre["piece"], arbre["xy"], arbre["z"], False)
            jeu = (dt - da) / 2 if dt is not None and da is not None else None
            ligne["mesure"] = {"jeu_radial_mm": jeu, "statut": "PASS" if jeu is not None and abs(jeu-item["jeu_radial"]) <= .02 else "FAIL"}
        else:
            ligne["mesure"] = {"statut": "NON APPLICABLE"}
        partenaire = item.get("partenaire_imprime")
        ligne["partenaire"] = {"statut": "NON APPLICABLE"}
        if partenaire:
            # Un partenaire déclaré doit être le fichier imprimé identifié, sans lecture
            # hors de l'espace privé (docs/explications/fiabilite-fdm.md, assemblages).
            try:
                fichier = _sans_lien(projet / partenaire["fichier"]).resolve(strict=True)
                if not fichier.is_relative_to(projet.parent.parent.resolve()) or not fichier.is_file():
                    raise ValueError("Partenaire hors de l'espace privé ou fichier absent")
                conforme = sha256_fichier(fichier) == partenaire["sha256"]
                ligne["partenaire"] = {"statut": "PASS" if conforme else "FAIL", "fichier": partenaire["fichier"]}
            except (OSError, ValueError, KeyError, TypeError) as erreur:
                ligne["partenaire"] = {"statut": "FAIL", "detail": str(erreur)}
        resultats.append(ligne)
    statut = "FAIL" if any(x["partenaire"]["statut"] == "FAIL" or x["politique"] == "FAIL" or x["mesure"]["statut"] == "FAIL" for x in resultats) else "WARN" if not resultats or any(x["politique"] == "WARN" for x in resultats) else "PASS"
    ecrire_json_neuf(sortie, {"schema": 1, "statut": statut, "resultats": resultats})
    return statut

def main(argv=None):
    \"\"\"Renvoie 0/PASS ou NA,1/WARN,2/FAIL ; aucune commande machine ni preuve physique.\"\"\"
    statut = verifier()
    print(statut)
    return 2 if statut == "FAIL" else 1 if statut == "WARN" else 0

if __name__ == "__main__":
    raise SystemExit(main())
""",
}

L = {
    "qualite.json": """{
  "schema": 1,
  "manifeste": "candidats/assemblage.json",
  "plaques_3mf": "candidats/plaques.3mf",
  "tranche_3mf": "candidats/tranche.3mf",
  "placement": "preuves/placement.json",
  "volume_impression_mm": null,
  "protegees": {},
  "interfaces": "interfaces.json"
}
""",
    "organisation/organisation.json": """{
  "schema": 1,
  "niveau": "L",
  "mode": "A_CONFIGURER",
  "suivi": [{"nom": "controles-mecanisme", "motifs": ["src/controles/*.py"], "declenche": ["check"]}],
  "cibles": {"build": {"commande": "make build", "apres": []}, "plaques": {"commande": "make plaques", "apres": ["build"]},
    "trancher": {"commande": "make trancher", "apres": ["plaques"]}, "check": {"commande": "make check", "apres": ["trancher"]}},
  "automatisations": [], "lecture_minimale": ["README.md", "ETAT.md", "BESOIN.md"]
}
""",
    "src/verifier.py": """\"\"\"Appelle les contrôles L dans le même interpréteur Python.\"\"\"
import argparse, contextlib, importlib, io, json, traceback
from pathlib import Path
from benchab.qualite.ecritures import ecrire_json_neuf, valider_chemins

def verifier(projet=None):
    \"\"\"Agrège les contrôles privés sans unité ; renvoie PASS/WARN/FAIL/NV sans promouvoir un résultat inconnu.\"\"\"
    projet = Path(projet or Path(__file__).resolve().parents[1])
    sortie = projet / "preuves/qualite-projet.json"
    controles = [f for f in sorted((projet / "src/controles").glob("*.py")) if not f.name.startswith("_")]
    journaux = {f.stem: projet / "preuves/controles" / (f.stem + ".json") for f in controles}
    valider_chemins([], [sortie, *journaux.values()])
    resultats = []
    for fichier in controles:
        if fichier.name.startswith("_"):
            continue
        journal = io.StringIO()
        try:
            # Les contrôles doivent laisser une trace, même si leur import ou leur
            # exécution échoue (docs/reference/controles.md, diagnostics conservés).
            with contextlib.redirect_stdout(journal), contextlib.redirect_stderr(journal):
                module = importlib.import_module("src.controles." + fichier.stem)
                fonction = getattr(module, "verifier", None)
                if not callable(fonction):
                    raise ValueError("verifier(projet) absent")
                resultat = fonction(projet)
            ligne = {"controle": fichier.stem, **(resultat if isinstance(resultat, dict) else {"statut": str(resultat)})}
            brut = str(ligne.get("statut", "")).strip().upper()
            aliases = {"INCONNU": "NON VÉRIFIÉ", "UNKNOWN": "NON VÉRIFIÉ", "NON VERIFIE": "NON VÉRIFIÉ", "WARNING": "WARN"}
            statut = aliases.get(brut, brut)
            ligne["statut"] = statut if statut in {"PASS", "WARN", "FAIL", "NON VÉRIFIÉ", "NON APPLICABLE"} else "NON VÉRIFIÉ"
        except (Exception, SystemExit) as erreur:
            ligne = {"controle": fichier.stem, "statut": "FAIL", "detail": str(erreur), "pile": traceback.format_exc()}
        ligne["journal"] = str(journaux[fichier.stem].relative_to(projet))
        ecrire_json_neuf(journaux[fichier.stem], {**ligne, "sortie_complete": journal.getvalue()})
        resultats.append(ligne)
    # Un exemple ou un contrôle non renseigné ne prouve pas sa réussite
    # (docs/reference/controles.md, statuts explicites).
    statut = ("FAIL" if any(r["statut"] == "FAIL" for r in resultats) else
              "NON VÉRIFIÉ" if not resultats or any(r["statut"] == "NON VÉRIFIÉ" for r in resultats) else
              "WARN" if any(r["statut"] == "WARN" for r in resultats) else
              "NON APPLICABLE" if all(r["statut"] == "NON APPLICABLE" for r in resultats) else "PASS")
    ecrire_json_neuf(sortie, {"schema": 1, "statut": statut, "resultats": resultats})
    return statut

def main(argv=None):
    \"\"\"Exécute les contrôles L ; codes0/PASS ou NA,1/WARN,2/FAIL,3/NV ; aucune action machine.\"\"\"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projet", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    statut = verifier(args.projet)
    print(statut)
    return {"PASS": 0, "NON APPLICABLE": 0, "WARN": 1, "FAIL": 2, "NON VÉRIFIÉ": 3}[statut]

if __name__ == "__main__":
    raise SystemExit(main())
""",
    "src/controles/__init__.py": """\"\"\"Contrôles paramétriques privés du projet L.\"\"\"\n""",
    "src/controles/exemple_mouvement.py": """\"\"\"Exemple neutre; aucun mouvement physique n'est déclaré.\"\"\"
def verifier(projet):
    return {"statut": "NON APPLICABLE", "detail": "Aucun mécanisme ou contact déclaré."}
""",
}


def fichiers(niveau):
    """Renvoie les textes S/M/L (paramètres en mm et °C), sans écrire ni lancer de fabrication ou renvoyer de statut."""
    if niveau not in ("S", "M", "L"):
        raise ValueError("niveau attendu : S, M ou L")
    resultat = dict(COMMUN)
    if niveau in ("M", "L"):
        resultat.update(M)
    if niveau == "L":
        resultat.update(L)
        resultat["Makefile"] += (
            "\n.PHONY: references qualite check remplissages lissage\ncheck:\n\t$(PYTHON) -m src.verifier\nqualite:\n\t$(PYTHON) -m benchab.qualite.qualite niveau1 qualite.json\n\t$(PYTHON) -m benchab.qualite.qualite niveau3 qualite.json\nremplissages:\n\t$(PYTHON) -m src.remplissages\nlissage:\n\t$(PYTHON) -m src.lissage\n"
        )
    else:
        resultat["Makefile"] += (
            "\n.PHONY: remplissages lissage\nremplissages:\n\t$(PYTHON) -m src.remplissages\nlissage:\n\t$(PYTHON) -m src.lissage\n"
        )
    resultat["fiche-essai.md"] = COMMUN["fiche-essai.md"]
    return resultat
