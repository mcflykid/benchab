"""Contrôle qualité commun aux projets : fichiers, géométrie, plaques et preuves.

Usage : benchab qualite niveau1|niveau3|incrementiel|resume <qualite.json> [ancien manifeste]

Statuts : PASS (vérifié et réussi), FAIL (bloquant), WARNING (risque), NON VÉRIFIÉ (jamais compté comme PASS).
Rapports détaillés : <projet>/preuves/qualite/*.json ; synthèse : *.md.
Le niveau 2 (mécanique) reste propre à chaque projet (scripts de contrôle du projet) ; le niveau 4 est l'audit final.
Score de risque d'accroche : indicateur heuristique documenté dans README.md, jamais une prédiction."""

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

from benchab import reglages as schema_reglages
from benchab.controles.rapports import _protege
from benchab.gcode.lecture import lire_texte_gcode_3mf
from benchab.machine.configuration import charger as configuration_machine
from benchab.qualite.ecritures import PublicationNeuve, _sans_lien, valider_chemins
from benchab.qualite.statuts import code_statut

OK, KO, AV, NV = "PASS", "FAIL", "WARNING", "NON VÉRIFIÉ"


def executer_borne(commande, delai=600):
    if not math.isfinite(delai) or delai <= 0:
        raise ValueError("délai fini positif requis")
    try:
        return subprocess.run(commande, capture_output=True, text=True, timeout=delai)
    except subprocess.TimeoutExpired as exc:

        def decode(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""

        return subprocess.CompletedProcess(
            commande,
            124,
            decode(exc.stdout),
            decode(exc.stderr) + "\nDélai du contrôle dépassé ; vérifier les éventuels descendants",
        )


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _lire_preuve_existante(path):
    """Lit une ancienne preuve régulière sans suivre de lien ni accepter une lecture instable."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as flux:
        avant = os.fstat(flux.fileno())
        if not stat.S_ISREG(avant.st_mode) or avant.st_size > 32 * 1024 * 1024:
            raise ValueError("Destination de rapport non régulière ou trop volumineuse")
        contenu = flux.read(32 * 1024 * 1024 + 1)
        apres = os.fstat(flux.fileno())

    def identite(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    if len(contenu) > 32 * 1024 * 1024 or identite(avant) != identite(apres):
        raise ValueError("Destination modifiée pendant la comparaison")
    courant = path.lstat()
    if not stat.S_ISREG(courant.st_mode) or identite(courant) != identite(apres):
        raise ValueError("Destination remplacée pendant la comparaison")
    return contenu


class Rapport:
    def __init__(self, nom, cfg_path):
        self.nom, self.items = nom, []
        self.base = Path(cfg_path).resolve().parent
        self.entrees = [Path(cfg_path).resolve()]

    def ajouter_entrees(self, *chemins):
        """Enregistre les fichiers lus pour interdire leur collision avec les preuves publiées."""
        for chemin in chemins:
            if chemin is not None:
                self.entrees.append(Path(chemin).absolute())

    def add(self, controle, statut, detail="", **extra):
        self.items.append({"controle": controle, "statut": statut, "detail": detail, **extra})
        return statut

    def ecrire(self, supplementaires=None):
        """Publie les rapports JSON/Markdown et preuves complémentaires sans écrasement.

        Un rejeu identique est sans écriture ; un contenu différent est rangé en dossier
        `rejeu-*`. Les rapports du même contrôle sont préparés et publiés ensemble.
        """
        out = self.base / "preuves/qualite"
        cpt = {s: sum(1 for i in self.items if i["statut"] == s) for s in (OK, KO, AV, NV)}
        global_ = KO if cpt[KO] else (NV if cpt[NV] else (AV if cpt[AV] else OK))
        contenu_json = json.dumps(
            {"niveau": self.nom, "statut_global": global_, "compte": cpt, "controles": self.items},
            indent=1,
            ensure_ascii=False,
        )
        lignes = [
            f"# {self.nom} : {global_}  ({cpt[OK]} PASS, {cpt[KO]} FAIL, {cpt[AV]} WARNING, {cpt[NV]} NON VÉRIFIÉ)",
            "",
        ]
        lignes += [
            f"- **{i['statut']}** {i['controle']}" + (f" — {i['detail']}" if i["detail"] and i["statut"] != OK else "")
            for i in self.items
            if i["statut"] != OK
        ]
        contenus_base = {
            f"{self.nom}.json": contenu_json,
            f"{self.nom}.md": "\n".join(lignes) + "\n",
        }
        supplementaires = supplementaires or {}
        if set(contenus_base) & set(supplementaires):
            raise ValueError("Preuve complémentaire en collision avec le rapport principal")
        contenus = {**contenus_base, **supplementaires}
        if any(not isinstance(nom, str) or Path(nom).name != nom or nom in (".", "..") for nom in contenus):
            raise ValueError("Nom de preuve complémentaire invalide")
        cibles = [out / nom for nom in contenus]
        for cible in cibles:
            _protege(cible)
        entrees = [_sans_lien(p).resolve() for p in self.entrees]
        cibles = [_sans_lien(p) for p in cibles]
        cibles_resolues = [p.resolve() for p in cibles]
        if len(set(cibles_resolues)) != len(cibles_resolues) or set(entrees) & set(cibles_resolues):
            raise ValueError("Collision entrée, sortie ou rapport : écrasement refusé.")
        existants = {}
        for cible in cibles:
            try:
                existants[cible] = _lire_preuve_existante(cible)
            except FileNotFoundError:
                continue
        if len(existants) == len(cibles) and all(
            existants[cible] == contenus[cible.name].encode("utf-8") for cible in cibles
        ):
            pass
        else:
            destination = out
            if existants:
                for _ in range(8):
                    destination = out / f"rejeu-{uuid.uuid4().hex}"
                    if not destination.exists() and not destination.is_symlink():
                        break
                else:
                    raise FileExistsError("Impossible de réserver une nouvelle preuve de rejeu")
                cibles = [destination / nom for nom in contenus]
                for cible in cibles:
                    _protege(cible)
            valider_chemins(entrees, cibles)
            with PublicationNeuve(self.entrees, cibles) as publication:
                for cible in cibles:
                    publication.temporaire(cible).write_text(contenus[cible.name], encoding="utf-8")
                publication.publier()
            if destination != out:
                print(f"Résultat différent conservé dans une nouvelle preuve : {destination}")
        print("\n".join(lignes[:1] + lignes[2:40]))
        return global_


def charger(cfg_path):
    cfg = json.loads(Path(cfg_path).read_text())
    base = Path(cfg_path).resolve().parent

    def P(k):
        return base / cfg[k]

    return cfg, base, P


def objets_3mf(path):
    """Plaques -> noms d'objets d'un 3MF Anycubic/Orca (model_settings.config)."""
    cfg = zipfile.ZipFile(path).read("Metadata/model_settings.config").decode()
    noms = dict(re.findall(r'<object id="(\d+)">\s*<metadata key="name" value="([^"]+)"', cfg))
    plaques = []
    for pl in re.findall(r"<plate>(.*?)</plate>", cfg, re.S):
        nom = re.search(r'key="plater_name" value="([^"]*)"', pl)
        ids = re.findall(r'key="object_id" value="(\d+)"', pl)
        plaques.append({"nom": nom.group(1) if nom else "", "objets": [noms.get(i, "?" + i) for i in ids]})
    return plaques


def niveau1(cfg_path):
    import trimesh

    cfg, base, P = charger(cfg_path)
    R = Rapport("niveau1", cfg_path)
    man = P("manifeste")
    if not man.exists():
        R.add("manifeste présent", KO, str(man))
        return R.ecrire()
    S = json.loads(man.read_text())
    R.add("manifeste présent", OK)
    modeles = {**S["models"], **S.get("options", {})}
    R.ajouter_entrees(
        man,
        *(P("stl_assemblage") / (m + ".stl") for m in modeles),
        *(P("stl_impression") / (m + ".stl") for m in modeles),
        *(P(k) for k in ("plaques_3mf", "tranche_3mf") if k in cfg),
    )
    inst = {p["name"]: p["model"] for p in S["parts"]}
    inst.update({n: n for n in S.get("options", {})})
    vol = cfg.get("volume_impression_mm", configuration_machine()["placement"]["plateau_xyz_mm"])
    for m, info in sorted(modeles.items()):
        fa, fi = P("stl_assemblage") / (m + ".stl"), P("stl_impression") / (m + ".stl")
        if not fa.exists() or not fi.exists():
            R.add(f"fichiers {m}", KO, "STL absent")
            continue
        for role, file, key in (("assemblage", fa, "stl_sha256"), ("impression", fi, "print_sha256")):
            expected = info.get(key)
            R.add(
                f"intégrité {role} {m}",
                NV if not expected else OK if sha(file) == expected else KO,
                "empreinte attendue absente" if not expected else "",
            )
            try:
                mesh = trimesh.load_mesh(file)
                good = bool(mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0)
                R.add(f"maillage {role} {m}", OK if good else KO)
            except Exception as exc:
                R.add(f"maillage {role} {m}", KO, str(exc))
        try:
            q = trimesh.load_mesh(fi)
        except Exception:
            continue
        if not (q.is_watertight and q.is_winding_consistent and q.volume > 0):
            R.add(f"maillage {m}", KO, f"étanche {q.is_watertight}, volume {q.volume:.1f}")
            continue
        e = q.extents
        if any(e[i] > vol[i] for i in range(3)):
            R.add(f"volume d'impression {m}", KO, f"{e.round(1).tolist()} > {vol}")
            continue
        R.add(f"fichier et maillage {m}", OK, encombrement=e.round(1).tolist())
    # Pièces protégées : empreintes, présence au manifeste, absence des plaques.
    plaques = []
    for k in ("plaques_3mf", "tranche_3mf"):
        if k in cfg and P(k).exists():
            plaques += [(P(k).name, p) for p in objets_3mf(P(k))]
    for m, pr in cfg.get("protegees", {}).items():
        fa, fi = P("stl_assemblage") / (m + ".stl"), P("stl_impression") / (m + ".stl")
        if not fa.exists() or not fi.exists():
            R.add(f"protégée {m} : présente", KO, "modèle absent du projet")
            continue
        da, di = sha(fa) == pr["sha256_assemblage"], sha(fi) == pr["sha256_impression"]
        R.add(
            f"protégée {m} : géométrie identique à la pièce imprimée",
            OK if da and di else KO,
            "" if da and di else "EMPREINTE DIFFÉRENTE : modification interdite",
        )
        manq = [i for i in pr.get("instances", []) if inst.get(i) != m]
        R.add(
            f"protégée {m} : instances {pr.get('instances')} au manifeste",
            OK if not manq else KO,
            f"manquantes ou remplacées : {manq}",
        )
        sur = [
            (f, p["nom"])
            for f, p in plaques
            for o in p["objets"]
            if inst.get(o, o) == m or o in pr.get("instances", [])
        ]
        if not plaques:
            R.add(f"protégée {m} : absente des plaques", NV, "aucun 3MF de plaques trouvé")
        else:
            R.add(f"protégée {m} : absente des plaques", OK if not sur else KO, f"présente sur {sur}")
    # Plaques : doublons, pièces attendues présentes une fois.
    if plaques:
        attendu = sorted(
            n
            for n, mo in inst.items()
            if mo not in cfg.get("protegees", {})
            and n not in cfg.get("hors_plaques", [])
            and modeles[mo].get("printable", True)
            and mo not in cfg.get("deja_imprimees_conservees", {})
        )
        for fichier in sorted({f for f, _ in plaques}):
            vus = [o for f, p in plaques if f == fichier for o in p["objets"]]
            dbl = sorted({o for o in vus if vus.count(o) > 1})
            man_ = sorted(set(attendu) - set(vus))
            trop = sorted(
                set(vus) - set(attendu) - set(cfg.get("rechanges", []))
            )  # rechanges déclarées (21/10/2026) : acceptées en plus, rien d'autre
            R.add(
                f"{fichier} : pièces attendues, une fois chacune",
                OK if not (dbl or man_ or trop) else KO,
                f"doublons {dbl}, manquantes {man_}, en trop {trop}",
            )
    else:
        R.add("plaques 3MF", NV, "aucun 3MF de plaques trouvé")
    return R.ecrire()


def score_accroche(o, cfg):
    """Heuristique documentée (README) : assise, élancement, bordure, supports, familles déjà décollées."""
    a = o.get("accroche", {})
    r = o.get("reglages_effectifs", o.get("reglages", {}))
    A, s = a.get("assise_mm2"), a.get("elancement")
    if A is None or s is None:
        return None, ["mesures d'assise absentes"]
    sc, why = 0, []
    if s > 3:
        sc += 3
        why.append("très élancée")
    elif s > 1.5:
        sc += 2
        why.append("élancée")
    elif s > 0.8:
        sc += 1
        why.append("élancement moyen")
    if A < 50:
        sc += 2
        why.append("assise < 50 mm²")
    elif A < 150:
        sc += 1
        why.append("assise < 150 mm²")
    brim = r.get("brim_type") not in (None, "no_brim", "auto_brim")
    bw = float(r.get("brim_width", 0) or 0) if brim else 0.0
    if not brim and (s > 0.8 or A < 150):
        sc += 2
        why.append("sans bordure")
    if bw >= 8:
        sc -= 2
        why.append("bordure ≥ 8 mm")
    elif bw >= 5:
        sc -= 1
        why.append("bordure ≥ 5 mm")
    if str(r.get("enable_support", "0")) == "1":
        sc += 1
        why.append("supports")
    if any(o["name"].startswith(f) for f in cfg.get("familles_echecs", [])):
        sc += 1
        why.append("famille déjà décollée")
    if any(o["name"].startswith(f) for f in cfg.get("familles_reussies", {})):
        sc -= 2
        why.append("réussite physique ensuite avec ces réglages")
    return max(sc, 0), why


def niveau3(cfg_path):
    """Exécute niveau3 avec les entrées déclarées et renvoie le résultat du contrôle. Réglages en
    unités natives (mm, s, °C, %) lus via le dictionnaire typé ; aucun réglage inconnu transformé."""
    cfg, base, P = charger(cfg_path)
    R = Rapport("niveau3", cfg_path)
    t3, pl = P("tranche_3mf"), P("placement")
    R.ajouter_entrees(t3, pl, P("plaques_3mf") if "plaques_3mf" in cfg else None)
    if not t3.exists():
        R.add("3MF tranché présent", KO, str(t3))
        return R.ecrire()
    z = zipfile.ZipFile(t3)
    if "Metadata/slice_info.config" not in z.namelist():
        R.add("3MF tranché", KO, "pas de slice_info : non tranché")
        return R.ecrire()
    from xml.etree import ElementTree as E

    info = E.fromstring(z.read("Metadata/slice_info.config"))
    from benchab.fabrication.anycubic import resultats

    try:
        mesures = resultats(t3)
        for mesure in mesures:
            if not lire_texte_gcode_3mf(t3, f"Metadata/plate_{mesure['index']}.gcode").strip():
                raise ValueError("G-code vide")
    except (ValueError, KeyError, E.ParseError, zipfile.BadZipFile) as exc:
        R.add("résultats du tranchage", KO, str(exc))
        return R.ecrire()
    noms = [p["nom"] for p in objets_3mf(t3)]
    obj = json.loads(pl.read_text())["objects"] if pl.exists() else []
    R.ajouter_entrees(*(P("stl_impression") / (o["model"] + ".stl") for o in obj if "model" in o))
    od = cfg.get("objectifs_duree_h", {})
    marge = od.get("marge", 0.15)
    tableau = []
    for p in info.findall("plate"):
        md = {m.get("key"): m.get("value") for m in p.findall("metadata")}
        k = int(md["index"])
        h = float(md.get("prediction", 0)) / 3600
        g = float(md.get("weight", 0))
        nom = noms[k - 1] if k - 1 < len(noms) else str(k)
        cat = next(
            (c for c in ("courte", "moyenne", "longue") if c in od and h <= od[c] * (1 + marge)), "hors catégorie"
        )
        risques = []
        for o in obj:
            if o.get("plate") != k:
                continue
            sc, why = score_accroche(o, cfg)
            risques.append({"objet": o["name"], "score": sc, "raisons": why})
        smax = max([r["score"] for r in risques if r["score"] is not None] or [0])
        niveau = "élevé" if smax >= 4 else ("moyen" if smax >= 2 else "faible")
        tableau.append(
            {
                "plaque": k,
                "nom": nom,
                "heures": round(h, 2),
                "grammes": round(g, 1),
                "categorie": cat,
                "risque_accroche": niveau,
                "score_max": smax,
                "objets": risques,
            }
        )
        if cat == "hors catégorie":
            R.add(
                f"plaque {k} {nom} : durée {h:.2f} h",
                AV,
                "au-delà de 3 h : acceptable seulement pour une pièce unique qui ne se découpe pas",
                pieces=len(risques),
            )
        else:
            R.add(f"plaque {k} {nom} : durée {h:.2f} h ({cat})", OK)
        R.add(
            f"plaque {k} {nom} : risque d'accroche {niveau} (score max {smax})",
            NV if not risques or any(r["score"] is None for r in risques) else AV if niveau == "élevé" else OK,
            "; ".join(
                f"{r['objet']} {r['score']} ({', '.join(r['raisons'])})" for r in risques if (r["score"] or 0) >= 4
            ),
        )
    # Îlots : contrôle supplémentaire limité aux objets réellement présents sur les plaques.
    from benchab.controles import couches_courtes as mod

    if obj:
        st_cool = schema_reglages.decoder(z.read("Metadata/project_settings.config"))
        for o in obj:
            file = P("stl_impression") / (o["model"] + ".stl")
            if not file.exists():
                continue
            ilots = mod.analyse(file, float(o["reglages"].get("layer_height", 0.2)))
            if ilots:
                vals = st_cool.get("slow_down_layer_time", [])
                mins = st_cool.get("slow_down_min_speed", [])
                mitigation = bool(vals) and min(map(float, vals)) >= 20 and bool(mins) and max(map(float, mins)) <= 10
                R.add(
                    "couches courtes " + o["name"],
                    AV if mitigation else KO,
                    "îlots détectés ; consigne≥20s/plancher≤10mm/s embarquée, "
                    "durée réelle non prouvée par cette heuristique"
                    if mitigation
                    else "îlots sans mitigation vérifiée",
                    ilots=ilots,
                    mitigation=mitigation,
                )
    # Réglages globaux de première couche et de trajectoire (lecture des réglages réellement embarqués).
    st = schema_reglages.decoder(z.read("Metadata/project_settings.config"))
    rc = st.get("reduce_crossing_wall")
    R.add("évitement des parois en déplacement (reduce_crossing_wall)", OK if str(rc) == "1" else AV, f"valeur {rc}")
    R.add(
        "remplissage par défaut sans croisement dans la couche",
        NV
        if not st.get("sparse_infill_pattern")
        else OK
        if st.get("sparse_infill_pattern") not in ("grid", "triangles", "stars", "cubic", "tri-hexagon")
        else AV,
        f"motif {st.get('sparse_infill_pattern')}",
    )
    # Routine commune existante (placement selon les deux pas, réglages appliqués, disposition réelle, débits).
    from benchab.controles.routine import controler as routine

    if P("plaques_3mf").exists() and pl.exists():
        _protege(base / "preuves/audit/slicer")
        controle = routine(P("plaques_3mf"), t3, pl, base / "preuves/audit/slicer")
        R.add(
            "routine controler-3mf.sh (placement, réglages, disposition, débits)",
            OK if controle["conforme"] else KO,
            controle["statut"],
        )
    else:
        R.add("routine controler-3mf.sh", NV, "fichiers manquants")
    return R.ecrire({"plaques.json": json.dumps(tableau, indent=1, ensure_ascii=False)})


def incrementiel(cfg_path, ancien):
    cfg, base, P = charger(cfg_path)
    a = json.loads(Path(ancien).read_text())["models"]
    b = json.loads(P("manifeste").read_text())["models"]
    chg = sorted(
        m
        for m in set(a) | set(b)
        if any(a.get(m, {}).get(k) != b.get(m, {}).get(k) for k in ("stl_sha256", "print_sha256"))
    )
    dep = cfg.get("dependances", {})
    a_faire = set()
    for m in chg:
        for k, v in dep.items():
            if m == k or (k.endswith("_") and m.startswith(k)):
                a_faire |= set(v)
        if m in cfg.get("protegees", {}):
            print("FAIL : pièce protégée modifiée :", m)
    a_faire |= {"niveau1"} if chg else set()
    print("modèles modifiés :", chg or "aucun")
    print("contrôles à relancer :", sorted(a_faire) or "aucun")
    return chg, sorted(a_faire)


def resume(cfg_path):
    cfg, base, P = charger(cfg_path)
    for f in sorted((base / "preuves/qualite").glob("*.md")):
        print(f.read_text().splitlines()[0])


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) < 2:
        print(__doc__)
        return 2
    f = {"niveau1": niveau1, "niveau3": niveau3, "resume": resume}.get(a[0])
    if a[0] == "incrementiel":
        if len(a) != 3:
            print(__doc__)
            return 2
        incrementiel(a[1], a[2])
        return 0
    if f is None:
        print(__doc__)
        return 2
    try:
        s = f(a[1])
    except (OSError, ValueError, KeyError, TypeError) as erreur:
        print(f"Publication de qualité refusée : {erreur}", file=sys.stderr)
        return 2
    return code_statut(s) if s is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
