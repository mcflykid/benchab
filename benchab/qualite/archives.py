"""Inventaire sans exécution ni extraction. Aucun lien source n'est suivi.

Les empreintes prouvent une lecture, pas une compréhension du contenu.
Les budgets bornent la décompression; chaque limite reste dans le manifeste.
"""

import argparse
import collections
import datetime
import hashlib
import io
import json
import os
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from benchab.qualite.ecritures import ecrire_json_neuf, sortie_privee, valider_chemins

MAX_MEMBER = 512 * 1024**2
MAX_ARCHIVE = 12 * 1024**3
MAX_DEPTH = 3


def sha_stream(f, limit=None):
    """Renvoie le SHA-256 et le nombre d’octets lus depuis la position courante.

    Le flux binaire n’est pas fermé ni rembobiné. Une limite facultative,
    en octets, est contrôlée après chaque bloc lu ; dépassement → ValueError.
    """
    h = hashlib.sha256()
    n = 0
    while True:
        b = f.read(1024**2)
        if not b:
            break
        n += len(b)
        if limit is not None and n > limit:
            raise ValueError("budget_decompression")
        h.update(b)
    return h.hexdigest(), n


def classify(rel, typ):
    """Classe un chemin d’inventaire selon son suffixe, sa zone et son type.

    Renvoie une catégorie et une description textuelle. Le contenu n’est
    pas ouvert ; le classement ne vaut ni analyse ni validation physique.
    """
    parts = rel.replace("!/", "/").split("/")
    suffix = Path(rel).suffix.lower()
    if typ == "directory":
        return "structure", "Dossier, enfants inventoriés séparément"
    if typ == "symlink":
        return "link", "Lien inventorié sans suivre sa destination"
    if ".git" in parts:
        return "git", "Métadonnées ou objets Git; historique, pas code exécuté"
    if any(x in parts for x in ("node_modules", ".ruff_cache", "__pycache__", "rea-npm-cache")):
        return "cache_dependency", "Dépendance ou cache tiers; provenance et reconstruction à vérifier"
    if any(x in parts for x in ("build", "build-arm64", "build-ruvyko", "DL_CACHE")):
        return "build_dependency", "Construction/dépendance tierce; ne pas confondre avec intégration"
    if suffix in (".md", ".txt", ".rst", ".adoc"):
        return "documentation", "Documentation ou preuve textuelle à examiner"
    if suffix in (".py", ".rs", ".cpp", ".hpp", ".h", ".c", ".mm", ".m", ".js", ".ts", ".sh", ".cmake"):
        return "source", "Source ou script; analyse sémantique séparée"
    if suffix in (".json", ".toml", ".yaml", ".yml", ".xml", ".ini", ".cfg", ".csv", ".conf"):
        return "configuration_data", "Configuration/données; contexte et sensibilité à vérifier"
    if suffix in (".stl", ".obj", ".3mf", ".step", ".glb", ".gcode"):
        return "geometry_print", "Géométrie ou fichier de fabrication; aucune validation physique"
    if suffix in (".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".ttf", ".woff2"):
        return "media", "Ressource visuelle/document; inspection visuelle distincte"
    if suffix in (".log", ".ips"):
        return "evidence", "Trace historique; résultat à dater et ne pas réexécuter implicitement"
    return "other", "Nature à préciser par signature et examen fonctionnel"


def record(path, typ, size):
    """Crée une entrée d’inventaire pour un chemin, un type et une taille en octets.

    Renvoie un dictionnaire neuf sans empreinte ni décision. L’objet source
    n’est pas lu ou changé.
    """
    category, role = classify(path, typ)
    return dict(
        path=path,
        type=typ,
        size=size,
        sha256=None,
        category=category,
        role=role,
        dependencies=[],
        features=[],
        potential_value="à qualifier",
        state="inventorié",
        analysis="metadata_only",
        required_actions=["examen de contenu ou de fonction"],
        decision="en attente",
        justification="Inventaire technique distinct de l’analyse sémantique",
        destination=None,
        validation="non intégrée",
        errors=[],
    )


def safe_member(name):
    """Teste si le nom interne peut être représenté comme chemin POSIX relatif.

    Retourne False pour un chemin absolu, une remontée, un segment vide
    ou des caractères interdits. True ne valide aucun contenu et n’extrait rien.
    """
    parts = name.rstrip("/").split("/")
    return (
        bool(name)
        and not PurePosixPath(name).is_absolute()
        and not any(x in ("", "..") for x in parts)
        and ":" not in name
        and "\\" not in name
        and not any(ord(c) < 32 or ord(c) == 127 for c in name)
    )


def archive_kind(name, signature=b""):
    """Détermine le lecteur ZIP ou TAR à partir du nom et d’octets de préfixe.

    Renvoie zip, tar ou None. Un suffixe reconnu suffit à choisir une
    tentative de lecture ; il ne prouve pas l’intégrité du conteneur.
    """
    n = name.lower()
    if signature[:4] in (b"PK\x03\x04", b"PK\x05\x06") or n.endswith((".zip", ".3mf", ".whl", ".jar")):
        return "zip"
    if n.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        return "tar"
    return None


def inspect_archive(source, name, entries, depth=0, kind=None):
    """Ajoute au manifeste les membres d’un ZIP ou TAR déjà désigné par l’appelant.

    Reçoit un chemin contrôlé ou un flux repositionnable, son nom logique,
    une liste modifiée sur place et une profondeur sans unité. Les tailles
    et budgets sont en octets ; les limites et erreurs restent dans la liste.
    Renvoie None. Aucun membre n’est extrait, exécuté ni suivi comme lien.
    """
    if kind is None:
        if hasattr(source, "read"):
            pos = source.tell()
            sig = source.read(512)
            source.seek(pos)
        else:
            with open(source, "rb") as stream:
                sig = stream.read(512)
        kind = archive_kind(name, sig)
    if not kind:
        return
    seen = collections.Counter()
    total = 0
    try:
        a = (
            zipfile.ZipFile(source)
            if kind == "zip"
            else tarfile.open(
                fileobj=source if hasattr(source, "read") else None,
                name=None if hasattr(source, "read") else str(source),
                mode="r:*",
            )
        )
        with a:
            members = a.infolist() if kind == "zip" else a
            for m in members:
                member_name = m.filename if kind == "zip" else m.name
                seen[member_name] += 1
                rel = name + "!/" + member_name
                size = m.file_size if kind == "zip" else m.size
                islink = stat.S_ISLNK(m.external_attr >> 16) if kind == "zip" else m.issym() or m.islnk()
                isdir = m.is_dir() if kind == "zip" else m.isdir()
                special_zip = kind == "zip" and stat.S_IFMT(m.external_attr >> 16) not in (
                    0,
                    stat.S_IFREG,
                    stat.S_IFDIR,
                    stat.S_IFLNK,
                )
                typ = (
                    "archive_special"
                    if special_zip
                    else "archive_directory"
                    if isdir
                    else "archive_link"
                    if islink
                    else "archive_file"
                    if kind == "zip" or m.isfile()
                    else "archive_special"
                )
                e = record(rel, typ, size)
                e["container"] = name
                e["member_index"] = seen[member_name]
                e["safe_path"] = safe_member(member_name)
                if not e["safe_path"]:
                    e["errors"].append("chemin non sûr: aucune extraction")
                if seen[member_name] > 1:
                    e["errors"].append("nom de membre dupliqué")
                entries.append(e)
                if typ != "archive_file":
                    if kind != "zip" and islink:
                        e["target"] = m.linkname
                    if kind == "zip" and islink:
                        try:
                            if size > 4096:
                                raise ValueError("cible de lien trop longue")
                            raw = a.read(m)
                            e["target"] = os.fsdecode(raw)
                            e["sha256"] = hashlib.sha256(raw).hexdigest()
                            e["sha256_scope"] = "link_text"
                        except Exception as exc:
                            e["errors"].append(type(exc).__name__ + ": " + str(exc)[:240])
                    continue
                if depth > MAX_DEPTH or size > MAX_MEMBER or total + size > MAX_ARCHIVE:
                    e["errors"].append("contenu non lu: budget de décompression")
                    e["inspection"] = "incomplete"
                    continue
                try:
                    with a.open(m) if kind == "zip" else a.extractfile(m) as f:
                        prefix = f.read(512)
                        child_kind = archive_kind(member_name, prefix)
                        if child_kind and depth < MAX_DEPTH:
                            data = prefix + f.read(MAX_MEMBER + 1 - len(prefix))
                            if len(data) > MAX_MEMBER:
                                raise ValueError("budget_decompression")
                            e["sha256"] = hashlib.sha256(data).hexdigest()
                            actual = len(data)
                            inspect_archive(io.BytesIO(data), rel, entries, depth + 1)
                        else:
                            h = hashlib.sha256(prefix)
                            actual = len(prefix)
                            while chunk := f.read(1024**2):
                                actual += len(chunk)
                                if actual > MAX_MEMBER:
                                    raise ValueError("budget_decompression")
                                h.update(chunk)
                            e["sha256"] = h.hexdigest()
                            if child_kind:
                                e["errors"].append("archive imbriquée: profondeur limite")
                                e["inspection"] = "incomplete"
                    total += actual
                    if actual != size:
                        e["errors"].append("taille déclarée différente du flux")
                    e["integrity"] = "flux lu, CRC ZIP vérifié" if kind == "zip" else "flux lu"
                except Exception as exc:
                    e["errors"].append(type(exc).__name__ + ": " + str(exc)[:240])
                    e["inspection"] = "incomplete"
    except Exception as exc:
        entries.append(
            dict(
                path=name + "!/",
                type="archive_error",
                size=0,
                sha256=None,
                analysis="unreadable",
                inspection="incomplete",
                errors=[type(exc).__name__ + ": " + str(exc)[:240]],
                decision="bloqué",
                justification="Archive non inspectable intégralement",
                destination=None,
                validation="non intégrée",
            )
        )


def _nettoyage_simule(entries):
    """Propose métadonnées Finder et sauvegardes strictement dupliquées, octets logiques ; ne supprime jamais rien."""
    fichiers = {e["path"]: e for e in entries if e["type"] == "file" and "!/" not in e["path"]}
    groupes = collections.defaultdict(list)
    for chemin, entree in fichiers.items():
        if entree.get("sha256") and entree["size"]:
            groupes[(entree["size"], entree["sha256"])].append(chemin)
    copies = {}
    gains = []
    for (taille, sha), chemins in groupes.items():
        if len(chemins) < 2:
            continue
        chemins.sort()
        conserve = next((p for p in chemins if not Path(p).name.startswith(".avant-tranchage-")), chemins[0])
        for chemin in chemins:
            if chemin != conserve:
                copies[chemin] = conserve
        gains.append({"sha256": sha, "chemins": chemins, "copie_conservee": conserve,
                      "gain_theorique_maximal_octets": taille * (len(chemins) - 1)})
    candidats = []
    for chemin, entree in fichiers.items():
        nom = Path(chemin).name
        if nom == ".DS_Store":
            candidats.append({"chemin": chemin, "octets": entree["size"], "raison": "Métadonnée Finder",
                              "accord_requis": True})
        elif nom.startswith(".avant-tranchage-") and chemin in copies:
            candidats.append({"chemin": chemin, "octets": entree["size"], "sha256": entree["sha256"],
                              "copies_conservees": [copies[chemin]], "accord_requis": True,
                              "raison": "Copie strictement identique ; dépendances et protections à qualifier"})
    # Un doublon n'autorise aucune suppression : une copie de référence reste désignée,
    # et le gain réel demeure nul (docs/reference/donnees.md, conservation privée).
    return {"simulation": True, "suppression_possible_par_cet_outil": False,
            "candidats": candidats, "groupes_doublons_physiques": gains, "gain_reel_octets": 0}


def inventory(root, archives=True):
    """Inventorie chemins, SHA, liens, dossiers vides et octets par zone; main classe PASS/WARN/FAIL sans extraction."""
    root = Path(root).absolute()
    entries = []
    errors = []
    empty_directories = set()
    if any(p.is_symlink() for p in (root, *root.parents)) or not root.is_dir():
        raise ValueError("La racine et ses ancêtres doivent être de vrais dossiers, pas des liens")

    def visit(folder):
        try:
            with os.scandir(folder) as it:
                children = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            errors.append(dict(path=str(Path(folder).relative_to(root)), error=str(exc)))
            return
        if not children:
            empty_directories.add(Path(folder).relative_to(root).as_posix())
        for child in children:
            p = Path(child.path)
            rel = p.relative_to(root).as_posix()
            try:
                s = p.lstat()
                typ = (
                    "symlink"
                    if stat.S_ISLNK(s.st_mode)
                    else "directory"
                    if stat.S_ISDIR(s.st_mode)
                    else "file"
                    if stat.S_ISREG(s.st_mode)
                    else "special"
                )
                e = record(rel, typ, s.st_size)
                entries.append(e)
                e["mtime_ns"] = s.st_mtime_ns
                e["mode"] = oct(stat.S_IMODE(s.st_mode))
                if typ == "symlink":
                    target = os.readlink(p)
                    e["target"] = target
                    e["sha256"] = hashlib.sha256(os.fsencode(target)).hexdigest()
                    e["sha256_scope"] = "link_text"
                    # lexical normalization only; never stat/read target
                    dest = Path(os.path.abspath(p.parent / target))
                    e["external_target"] = not dest.is_relative_to(root)
                elif typ == "directory":
                    visit(p)
                elif typ == "file":
                    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(fd, "rb") as f:
                        opened = os.fstat(f.fileno())
                        if not stat.S_ISREG(opened.st_mode) or (s.st_dev, s.st_ino) != (opened.st_dev, opened.st_ino):
                            e["errors"].append("source remplacée avant lecture")
                            continue
                        sig = f.read(32)
                        f.seek(0)
                        e["sha256"], n = sha_stream(f)
                        e["signature_hex"] = sig[:16].hex()
                        if archives and archive_kind(rel, sig):
                            f.seek(0)
                            inspect_archive(f, rel, entries, kind=archive_kind(rel, sig))
                        end = os.fstat(f.fileno())
                    now = p.lstat()
                    if (s.st_size, s.st_mtime_ns, s.st_ino, s.st_ctime_ns) != (
                        end.st_size,
                        end.st_mtime_ns,
                        end.st_ino,
                        end.st_ctime_ns,
                    ) or (s.st_dev, s.st_ino) != (now.st_dev, now.st_ino):
                        e["errors"].append("source modifiée pendant la lecture")
            except OSError as exc:
                errors.append(dict(path=rel, error=str(exc)))

    visit(root)
    hashes = collections.defaultdict(list)
    for e in entries:
        if e.get("sha256") and e["type"] in ("file", "archive_file"):
            hashes[e["sha256"]].append(e["path"])
    duplicates = {h: ps for h, ps in hashes.items() if len(ps) > 1}
    # Les zones additionnent les octets physiques; les membres ZIP/TAR détaillés
    # ne sont pas recomptés au-dessus de leur archive (docs/reference/donnees.md).
    zones = collections.defaultdict(lambda: {"fichiers": 0, "octets": 0})
    for entry in entries:
        chemin = entry["path"]
        if entry["type"] == "file" and "!/" not in chemin:
            parties = PurePosixPath(chemin).parts
            zone = parties[0] if len(parties) > 1 else "."
            zones[zone]["fichiers"] += 1
            zones[zone]["octets"] += entry["size"]
    # Déduire les dossiers vides depuis les entrées observées, sans suivre de lien.
    repertoires = [
        entry["path"].rstrip("/")
        for entry in entries
        if entry["type"] in ("directory", "archive_directory")
    ]
    for repertoire in repertoires:
        prefixe = repertoire + "/"
        if not any(
            entry["path"].startswith(prefixe) and entry["path"].rstrip("/") != repertoire for entry in entries
        ):
            empty_directories.add(repertoire)
    return dict(
        schema_version=1,
        source=str(root),
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        source_untouched=True,
        method="lstat/scandir, SHA256 streaming, archives sans extraction; aucun lien suivi",
        inventory_errors=errors,
        budgets=dict(member_bytes=MAX_MEMBER, archive_bytes=MAX_ARCHIVE, depth=MAX_DEPTH),
        counts=dict(collections.Counter(e["type"] for e in entries)),
        entries=entries,
        identical_groups=duplicates,
        nettoyage_simule=_nettoyage_simule(entries),
        empty_directories=sorted(empty_directories),
        zone_sizes={zone: zones[zone] for zone in sorted(zones)},
        octets_fichiers_physiques=sum(valeur["octets"] for valeur in zones.values()),
        limitations=[
            "SHA et métadonnées ne constituent pas une analyse complète",
            "Tailles par chemin; un lien dur rencontré sous plusieurs chemins est compté plusieurs fois",
            "Objets Git binaires inventoriés, objets historiques internes non dépaquetés",
            "Archives non ZIP/TAR à qualifier séparément",
            "Aucun secret ni contenu de fichier reproduit par cet inventaire",
        ],
    )


def main(argv=None, *, prive=None):
    """Inventorie la source et publie un JSON privé neuf hors de celle-ci.

    Affiche les compteurs, les tailles en octets et le chemin du rapport.
    Codes : 0 PASS, 2 WARN, 1 FAIL. L’inventaire ne déplace aucun fichier
    et ne valide pas le contenu ni l’usage physique des archives.
    """
    p = argparse.ArgumentParser()
    p.add_argument("source")
    p.add_argument("output")
    p.add_argument("--sans-archives", action="store_true")
    a = p.parse_args(argv)
    q = sortie_privee(a.output, prive=prive)
    valider_chemins([Path(a.source)], [q])
    if q.resolve().is_relative_to(Path(a.source).resolve()):
        raise ValueError("Le rapport doit rester hors de la source inventoriée.")
    m = inventory(a.source, not a.sans_archives)
    incomplet = any(e.get("inspection") == "incomplete" for e in m["entries"])
    erreurs = bool(m["inventory_errors"]) or any(e["errors"] for e in m["entries"])
    statut = "FAIL" if erreurs else "WARN" if incomplet else "PASS"
    m["statut"] = statut
    ecrire_json_neuf(q, m)
    print(
        json.dumps(
            {
                "output": str(q),
                "statut": statut,
                "counts": m["counts"],
                "inventory_errors": len(m["inventory_errors"]),
                "entry_errors": sum(bool(e["errors"]) for e in m["entries"]),
                "duplicate_groups": len(m["identical_groups"]),
                "empty_directories": m["empty_directories"],
                "zone_sizes": m["zone_sizes"],
                "octets_fichiers_physiques": m["octets_fichiers_physiques"],
            },
            ensure_ascii=False,
        )
    )
    return {"PASS": 0, "WARN": 2, "FAIL": 1}[statut]


if __name__ == "__main__":
    raise SystemExit(main())
