"""Read-only checks; never imports project scripts or contacts a slicer/printer.

Usage: python3 verifier_reference.py [--root FOLDER] [--sans-historique]
The dated preservation audit runs when its inventory is present. Generic syntax
and current Markdown links remain usable independently of that migration.
"""

import argparse
import ast
import hashlib
import io
import json
import os
import re
import stat
import sys
import tokenize
import urllib.parse
from pathlib import Path

from benchab.chemins import atelier_prive, sans_lien
from benchab.donnees.base import Base


def projet_local(root, path):
    try:
        rel = Path(path).absolute().relative_to(Path(root).absolute())
    except ValueError:
        return False
    return bool(rel.parts and rel.parts[0] == "prive")


P = Path(__file__).resolve().parents[2]
HISTORY = "suivi/journal/organisation-2026-10-15"
EXCLUDED_LINK_PARTS = {"sauvegardes", "archives", "skills", "historique"}
DEPENDENCY_PARTS = {
    "node_modules",
    "site-packages",
    "__pycache__",
    ".git",
    ".venv",
    ".venv-dfam",
    "venv",
    ".cache-coherence",
}
MAX_SCRIPT_OCTETS = 8 * 1024 * 1024
MAX_TOTAL_SCRIPT_OCTETS = 64 * 1024 * 1024


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def markdown_targets(text):
    """Inline Markdown destinations; ignore examples in fenced/inline code.

    Handles angle destinations, optional titles and balanced parentheses. This
    is intentionally not a full Markdown parser: reference-style links and
    HTML links are outside the reported local-link count.
    """
    text = re.sub(r"^\s*(`{3,}|~{3,}).*?^\s*\1\s*$", "", text, flags=re.MULTILINE | re.DOTALL)
    text = re.sub(r"(`+).*?\1", "", text, flags=re.DOTALL)
    for match in re.finditer(r"(?<!\\)\]\(", text):
        start = match.end()
        rest = text[start:]
        if rest.startswith("<"):
            end = rest.find(">")
            if end >= 0:
                yield rest[1:end]
            continue
        depth = 0
        escaped = False
        chars = []
        for char in rest:
            if escaped:
                chars.append(char)
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == ")" and depth == 0:
                break
            if char.isspace() and depth == 0:
                break
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            chars.append(char)
        if chars:
            yield "".join(chars)

    for match in re.finditer(r"^ {0,3}\[[^\]\n]+\]:\s*(?:<([^>]+)>|(\S+))", text, re.M):
        yield match[1] if match[1] is not None else match[2]


def valider_json(path):
    """Valide la grammaire sans matérialiser les grands inventaires en mémoire."""
    decoder = json.JSONDecoder()
    stack = []
    root = False

    def value():
        nonlocal root
        if not stack:
            if root:
                raise ValueError("plusieurs valeurs JSON")
            root = True
            return
        kind, state = stack[-1]
        if kind == "object" and state != "value":
            raise ValueError("valeur JSON inattendue")
        if kind == "array" and state not in ("value", "value_or_end"):
            raise ValueError("valeur JSON inattendue")
        stack[-1][1] = "comma_or_end"

    with Path(path).open(encoding="utf-8") as stream:
        buf = ""
        eof = False
        while True:
            buf = buf.lstrip()
            if not buf:
                if eof:
                    break
                buf = stream.read(8192)
                eof = not bool(buf)
                continue
            c = buf[0]
            # Les enregistrements d'inventaire sont petits : décodage C par
            # objet au lieu d'une machine Python pour chacun de leurs champs.
            if c == "{" and stack and stack[-1][0] == "array":
                try:
                    block, end = decoder.raw_decode(buf)
                except ValueError:
                    if not eof and len(buf) < 16384:
                        more = stream.read(8192)
                        eof = not bool(more)
                        buf += more
                        continue
                else:
                    value()
                    buf = buf[end:]
                    continue
            if c in "{}[],:":
                buf = buf[1:]
                if c in "{[":
                    value()
                    stack.append(["object", "key_or_end"] if c == "{" else ["array", "value_or_end"])
                elif c in "}]":
                    if (
                        not stack
                        or (
                            c == "}"
                            and (stack[-1][0] != "object" or stack[-1][1] not in ("key_or_end", "comma_or_end"))
                        )
                        or (
                            c == "]"
                            and (stack[-1][0] != "array" or stack[-1][1] not in ("value_or_end", "comma_or_end"))
                        )
                    ):
                        raise ValueError("fermeture JSON invalide")
                    stack.pop()
                elif c == ":":
                    if not stack or stack[-1] != ["object", "colon"]:
                        raise ValueError("deux-points JSON inattendu")
                    stack[-1][1] = "value"
                else:
                    if not stack or stack[-1][1] != "comma_or_end":
                        raise ValueError("virgule JSON inattendue")
                    stack[-1][1] = "key" if stack[-1][0] == "object" else "value"
                continue
            try:
                token, end = decoder.raw_decode(buf)
            except ValueError:
                if eof:
                    raise
                more = stream.read(8192)
                eof = not bool(more)
                buf += more
                if len(buf) > 16 * 1024 * 1024:
                    raise ValueError("élément JSON >16 Mio : validation non effectuée")
                continue
            if not eof and (end == len(buf) or buf[end] not in " \t\r\n{}[],:"):
                more = stream.read(8192)
                eof = not bool(more)
                buf += more
                if len(buf) > 16 * 1024 * 1024:
                    raise ValueError("élément JSON excessif")
                continue
            if end < len(buf) and buf[end] not in " \t\r\n{}[],:":
                raise ValueError("séparateur JSON invalide")
            buf = buf[end:]
            if stack and stack[-1][0] == "object" and stack[-1][1] in ("key_or_end", "key"):
                if not isinstance(token, str):
                    raise ValueError("clé JSON non textuelle")
                stack[-1][1] = "colon"
            else:
                value()
        if not root or stack:
            raise ValueError("JSON incomplet")


def ancres_markdown(text):
    counts = {}
    ids = set(re.findall(r"<a\s+(?:id|name)=[\"\']([^\"\']+)", text, re.I))
    text = re.sub(r"^(`{3,}|~{3,}).*?^\1[^\n]*$", "", text, flags=re.M | re.S)
    for line in text.splitlines():
        if not re.match(r"^#{1,6}\s+", line):
            continue
        title = re.sub(r"^#+\s+", "", line).strip().rstrip("#").strip().lower()
        title = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", title)
        slug = re.sub(r"[^\w\- ]", "", title).replace(" ", "-")
        n = counts.get(slug, 0)
        counts[slug] = n + 1
        ids.add(slug + (f"-{n}" if n else ""))
    return ids


def _cache_references(root):
    """Cache dérivé local. Son identité lie contenu, validateur et version Python."""
    path = atelier_prive() / "cache/references.json"
    version = hashlib.sha256(Path(__file__).read_bytes()).hexdigest() + sys.version
    try:
        if path.is_symlink() or path.parent.is_symlink():
            return path, version, {}
        data = json.loads(path.read_text())
        fichiers = data.get("fichiers", {})
        return path, version, fichiers if data.get("validateur") == version and isinstance(fichiers, dict) else {}
    except (OSError, ValueError, AttributeError):
        return path, version, {}


def valider_json_memo(path, root, memo):
    def signature():
        s = path.stat()
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

    before = signature()
    h = digest(path)
    key = path.relative_to(root).as_posix()
    reused = memo.get(key) == h
    if not reused:
        valider_json(path)
    if signature() != before:
        raise ValueError("source modifiée pendant la validation JSON")
    memo[key] = h
    return reused


def _ecrire_cache_references(path, version, memo):
    if path.parent.is_symlink() or path.is_symlink():
        return
    payload = json.dumps({"validateur": version, "fichiers": memo}, sort_keys=True)
    try:
        if path.is_file() and path.read_text() == payload:
            return
        import tempfile

        path.parent.mkdir(exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".references-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    except OSError:
        pass  # Les contrôles restent valables, seul le cache ne sera pas réutilisé.


def verify(root, historical=True, anchors=False, cache=False):
    """Vérifie liens et empreintes (SHA-256), renvoie le bilan ok/erreurs ; ne modifie ni modèle ni original."""
    root = Path(root).resolve()
    cache_path, cache_version, memo = _cache_references(root) if cache else (None, None, {})
    errors, notes = [], []
    totals = dict(json=0, python=0, local_links=0, preserved_originals=0, changed_originals_backed_up=0)
    operation = root / HISTORY

    def checked(label, action):
        try:
            return action()
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
            errors.append(f"{label}: {exc}")
            return None

    if not root.is_dir():
        return dict(ok=False, checks=totals, notes=notes, errors=[f"Reference folder missing: {root}"])

    inventory_path = operation / "inventaire-avant.json"
    if historical and inventory_path.is_file():
        inventory = checked("Historical inventory", lambda: json.loads(inventory_path.read_text()))
        moves = checked("Historical moves", lambda: json.loads((operation / "deplacements.json").read_text()))
        if inventory is not None and not isinstance(inventory, list):
            errors.append("Historical inventory: expected a JSON list")
        if moves is not None and not isinstance(moves, list):
            errors.append("Historical moves: expected a JSON list")
        if isinstance(inventory, list) and isinstance(moves, list):

            def mapped(path):
                # Most specific prefix wins when directory and file moves overlap.
                for entry in sorted(moves, key=lambda e: len(e["avant"]), reverse=True):
                    old, new = root / entry["avant"], root / entry["apres"]
                    if path == old or path.is_relative_to(old):
                        return new / path.relative_to(old)
                return path

            retires_f = (
                root
                / "suivi/preuves/maintenance/2026-10-22-qualification-socle-phase-4-nettoyage-fichiers-retires.json"
            )
            retires = (
                set(json.loads(retires_f.read_text())) if retires_f.is_file() else set()
            )  # nettoyage du 22/10 validé par l'utilisateur

            def preserve(record):
                path = mapped(root / record["path"])
                if projet_local(root, path) or not path.is_file():
                    if str(path.relative_to(root)) in retires:
                        notes.append("Retiré le 22/10/2026 (archive de refonte) : " + record["path"])
                    elif path.name == ".DS_Store":
                        notes.append("Finder metadata absent: " + record["path"])
                    else:
                        errors.append("Missing original: " + record["path"])
                elif digest(path) == record["sha256"]:
                    totals["preserved_originals"] += 1
                else:
                    backup_root = root / "sauvegardes"
                    backups = (
                        [folder / record["path"] for folder in sorted(backup_root.iterdir()) if folder.is_dir()]
                        if backup_root.is_dir()
                        else []
                    )
                    backup = next(
                        (
                            candidate
                            for candidate in backups
                            if candidate.is_file() and digest(candidate) == record["sha256"]
                        ),
                        None,
                    )
                    if backup is not None:
                        totals["changed_originals_backed_up"] += 1
                    elif path.name == ".DS_Store":
                        notes.append("Finder metadata changed: " + str(path.relative_to(root)))
                    else:
                        errors.append("Changed original without matching backup: " + record["path"])

            for record in inventory:
                checked("Preservation record", lambda record=record: preserve(record))

    else:
        notes.append(
            "Historical preservation audit skipped (" + ("disabled" if not historical else "no inventory") + ")"
        )

    skipped = []
    paths = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs):
            if name in DEPENDENCY_PARTS or projet_local(root, Path(folder) / name):
                skipped.append(str((Path(folder) / name).relative_to(root)))
                dirs.remove(name)
        paths.extend(Path(folder) / name for name in files)
    for path in sorted(paths):
        if projet_local(root, path) or not path.is_file():
            continue
        rel = path.relative_to(root)
        if path.suffix in {".json", ".py"}:
            try:
                if path.suffix == ".json":
                    if cache:
                        valider_json_memo(path, root, memo)
                    else:
                        valider_json(path)
                    totals["json"] += 1
                else:
                    ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
                    totals["python"] += 1
            except (OSError, UnicodeError, ValueError, SyntaxError) as exc:
                errors.append(f"{path.suffix[1:]} {rel}: {exc}")
        if str(rel).startswith("donnees/sources/outils-3d/"):
            notes.append("Extrait tiers immuable : liens relatifs au package source, pas à l’atelier : " + str(rel))
            continue
        if path.suffix != ".md" or any(
            part in EXCLUDED_LINK_PARTS or part == "sources-code" or part.startswith("sauvegarde-")
            for part in rel.parts
        ):
            continue
        try:
            for target in markdown_targets(path.read_text(encoding="utf-8")):
                url = urllib.parse.urlsplit(target)
                if url.scheme or url.netloc or (not url.path and not (anchors and url.fragment)):
                    continue
                dest = Path(urllib.parse.unquote(url.path))
                if rel == Path("outils/gabarits/projet-commun/README.md") and dest.parts[:3] == ("..", "..", "docs"):
                    # Copié sans réécriture dans projets/NOM : ces liens visent
                    # les docs de la base depuis ce futur emplacement.
                    dest = root.joinpath(*dest.parts[2:])
                else:
                    dest = (dest if dest.is_absolute() else path.parent / dest) if url.path else path
                if projet_local(root, dest.resolve()):
                    continue  # Référence facultative à un projet conservé seulement sur le Mac.
                totals["local_links"] += 1
                if not dest.exists():
                    errors.append(f"Link {rel} -> {target}")
                elif anchors and url.fragment and dest.suffix == ".md":
                    if urllib.parse.unquote(url.fragment) not in ancres_markdown(dest.read_text()):
                        errors.append(f"Anchor {rel} -> {target}")
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"Markdown {rel}: {exc}")
    notes.append("Tous les projets sont contrôlés ; les seuls artefacts locaux exclus sont déclarés par rôle.")
    notes.append("Links checked: inline Markdown only; no remote URLs checked; anchors checked only with --ancres")
    notes.append("Third-party dependency trees excluded from project syntax/link checks: " + ", ".join(sorted(skipped)))
    notes.append(
        (
            "Historical snapshots and copied third-party sources-code are exclude"
            "d from current-link checks; their original links are preserved"
        )
    )
    if cache:
        _ecrire_cache_references(cache_path, cache_version, memo)
    return dict(ok=not errors, checks=totals, notes=notes, errors=errors)


def verifier_scripts_projet(base: Base, projet: str) -> dict:
    """Vérifie la syntaxe des scripts actifs d'un projet privé, sans import ni pyc.

    Entrées : Base explicite et nom de projet; sortie : statut PASS/FAIL/NON VÉRIFIÉ,
    chemins relatifs et erreurs. Liens, échappées et fichiers au-delà de 8 Mio (64 Mio
    cumulés) sont refusés. Aucun script ni réglage n'est exécuté ou modifié.
    Voir docs/reference/controles.md.
    """
    rapport = {"statut": "FAIL", "projet": projet, "fichiers": [], "erreurs": [], "notes": []}
    if not isinstance(base, Base):
        rapport["erreurs"].append("Base BENCHAB explicite requise")
        return rapport
    if not isinstance(projet, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", projet) is None:
        rapport["erreurs"].append("Identifiant de projet invalide")
        return rapport

    try:
        racine = atelier_prive(base.prive)
        dossiers = sans_lien(racine / "projets", racine=racine, strict=True)
        cible = sans_lien(dossiers / projet, racine=dossiers, strict=True)
        if not cible.is_dir():
            raise ValueError("Projet absent ou non régulier")
    except (OSError, ValueError, TypeError) as erreur:
        rapport["erreurs"].append(str(erreur))
        return rapport

    scripts = []
    octets_total = 0

    def echec(message):
        rapport["erreurs"].append(message)

    def erreur_parcours(erreur):
        raise erreur

    try:
        for dossier, sous_dossiers, fichiers in os.walk(
            cible, topdown=True, followlinks=False, onerror=erreur_parcours
        ):
            courant = sans_lien(dossier, racine=cible, strict=True)
            sous_dossiers.sort()
            fichiers.sort()
            for nom in list(sous_dossiers):
                chemin = courant / nom
                if chemin.is_symlink():
                    echec(f"Lien de dossier refusé : {chemin.relative_to(cible).as_posix()}")
                    sous_dossiers.remove(nom)
                elif nom in DEPENDENCY_PARTS or nom in {"historique-code", "preparations", "sources", "preuves"}:
                    # Une préparation figée ou une copie historique n'est pas un
                    # script actif à requalifier (docs/reference/controles.md).
                    sous_dossiers.remove(nom)
                    rapport["notes"].append(f"Dépendance exclue : {chemin.relative_to(cible).as_posix()}")
            for nom in fichiers:
                if Path(nom).suffix != ".py":
                    continue
                chemin = sans_lien(courant / nom, racine=cible, strict=True)
                scripts.append(chemin)
    except (OSError, ValueError, TypeError) as erreur:
        echec(f"Parcours privé incomplet : {erreur}")

    for chemin in scripts:
        relatif = chemin.relative_to(cible).as_posix()
        try:
            fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                avant = os.fstat(fd)
                if not stat.S_ISREG(avant.st_mode) or avant.st_size > MAX_SCRIPT_OCTETS:
                    raise ValueError("script non régulier ou supérieur à 8 Mio")
                if octets_total + avant.st_size > MAX_TOTAL_SCRIPT_OCTETS:
                    raise ValueError("budget total des scripts supérieur à 64 Mio")
                blocs = []
                taille = 0
                while bloc := os.read(fd, min(64 * 1024, MAX_SCRIPT_OCTETS + 1 - taille)):
                    blocs.append(bloc)
                    taille += len(bloc)
                    if taille > MAX_SCRIPT_OCTETS:
                        raise ValueError("script supérieur à 8 Mio")
                apres = os.fstat(fd)
                if (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns) != (
                    apres.st_dev,
                    apres.st_ino,
                    apres.st_size,
                    apres.st_mtime_ns,
                    apres.st_ctime_ns,
                ):
                    raise ValueError("script modifié pendant sa lecture")
                if (apres.st_dev, apres.st_ino) != (chemin.lstat().st_dev, chemin.lstat().st_ino):
                    raise ValueError("chemin de script remplacé pendant sa lecture")
                contenu = b"".join(blocs)
            finally:
                os.close(fd)
            encodage, _ = tokenize.detect_encoding(io.BytesIO(contenu).readline)
            ast.parse(contenu.decode(encodage), filename=relatif)
            octets_total += len(contenu)
            rapport["fichiers"].append({"chemin": relatif, "statut": "PASS", "octets": len(contenu)})
        except (OSError, UnicodeError, SyntaxError, ValueError, RecursionError) as erreur:
            rapport["fichiers"].append({"chemin": relatif, "statut": "FAIL", "erreur": str(erreur)})
            echec(f"Python {relatif}: {erreur}")

    if rapport["erreurs"]:
        rapport["statut"] = "FAIL"
    elif not scripts:
        rapport["statut"] = "NON VÉRIFIÉ"
        rapport["notes"].append("Aucun script Python actif trouvé dans le projet")
    else:
        rapport["statut"] = "PASS"
    rapport["nombre_scripts"] = len(scripts)
    rapport["octets_lus"] = octets_total
    rapport["mode"] = "tokenize + AST; aucun import, aucune exécution, aucun pyc"
    return rapport


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=P)
    parser.add_argument("--ancres", action="store_true")
    parser.add_argument(
        "--sans-cache",
        action="store_true",
        help="reparser tous les JSON ; sinon réutiliser seulement les validations au même SHA et même validateur",
    )
    parser.add_argument("--sans-historique", action="store_true")
    args = parser.parse_args(argv)
    report = verify(args.root, historical=not args.sans_historique, anchors=args.ancres, cache=not args.sans_cache)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
