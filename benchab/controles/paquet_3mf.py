"""

Inspection bornée des ZIP/3MF, sans extraction ni qualification d'impression.

Adaptation locale de RUVYKO/core/ruvyko_core/packet.py (30/10/2026).
Provenance, plafonds et limites : PAQUETS-3MF.md. Aucun accès à RUVYKO à l'exécution.


"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import stat
import struct
import sys
import unicodedata
import zipfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from benchab import reglages
from benchab.qualite.ecritures import PublicationNeuve, destination_rapport_optionnel, valider_chemins

PROJECT_SETTINGS = "Metadata/project_settings.config"
_CHUNK = 64 * 1024


@dataclass(frozen=True)
class PacketLimits:
    """Budgets de lecture ZIP/JSON : tailles en octets, nombres de membres et profondeur sans unité."""
    archive_bytes: int = 64 * 1024 * 1024
    member_bytes: int = 64 * 1024 * 1024
    total_bytes: int = 256 * 1024 * 1024
    members: int = 256
    compression_ratio: int = 200
    json_bytes: int = 1024 * 1024
    json_depth: int = 32
    json_nodes: int = 20_000

    def __post_init__(self) -> None:
        """Refuse un budget hors des plafonds internes sans lire de fichier."""
        ceilings = (512 * 1024 * 1024, 512 * 1024 * 1024, 1024 * 1024 * 1024, 8192, 1000, 8 * 1024 * 1024, 64, 200_000)
        for value, ceiling in zip(vars(self).values(), ceilings):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError("Limite de paquet invalide ou supérieure au plafond interne")


DEFAULT_LIMITS = PacketLimits()


@dataclass(frozen=True)
class PacketMember:
    """Identité d’un membre contrôlé : tailles en octets, CRC32 et SHA-256, sans interprétation métier."""
    name: str
    size: int
    compressed_size: int
    crc32: int
    sha256: str
    is_directory: bool


@dataclass(frozen=True)
class ProjectSettings:
    """Nom, empreinte et clés du membre de réglages, sans mutation de leur valeur."""
    member: str
    sha256: str
    keys: tuple[str, ...]


@dataclass(frozen=True)
class PacketReport:
    """Résultat structurel du paquet : taille en octets, inventaire et CRC, sans verdict d’envoi."""
    archive_sha256: str
    archive_size: int
    members: tuple[PacketMember, ...]
    crc_verified: bool
    project_settings: ProjectSettings | None
    gcode_members: tuple[str, ...]
    json_members: tuple[str, ...]
    unknowns: tuple[str, ...]


def _read_source(path: Path, limit: int) -> bytes:
    valider_chemins([path], [])
    # O_NONBLOCK avoids waiting on a FIFO; O_NOFOLLOW rejects a source symlink.
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
                raise ValueError("Une archive locale régulière et bornée est requise")
            data = source.read(limit + 1)
            after = os.fstat(source.fileno())

            def identity(value):
                return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns

            if identity(metadata) != identity(after) or len(data) != metadata.st_size:
                raise ValueError("Archive modifiée pendant la lecture ; inspection refusée")
    except OSError as error:
        raise ValueError("Lecture de l’archive locale impossible ou lien refusé") from error
    if len(data) > limit:
        raise ValueError("La taille de l’archive dépasse la limite")
    return data


def _central_budget(data: bytes, limits: PacketLimits) -> int:
    # Check declared entry count before ZipFile allocates its central directory.
    offset = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if offset < 0 or offset + 22 > len(data):
        raise ValueError("Fin de répertoire ZIP absente ou tronquée")
    disk, directory_disk, count_disk, count, size, start, comment = struct.unpack_from("<4H2LH", data, offset + 4)
    if offset + 22 + comment != len(data) or disk or directory_disk or count_disk != count:
        raise ValueError("Archive ZIP tronquée, ajoutée ou répartie sur plusieurs volumes")
    if count == 65535 or size == 2**32 - 1 or start == 2**32 - 1:
        raise ValueError("ZIP64 n’est pas pris en charge dans ce contrat borné")
    if not 1 <= count <= limits.members or size > 16 * 1024 * 1024:
        raise ValueError("Le répertoire ZIP dépasse le budget de membres ou de métadonnées")
    if start + size != offset:
        raise ValueError("Position du répertoire ZIP incohérente")
    return count


def _extra_fields(extra: bytes) -> None:
    offset = 0
    seen: set[int] = set()
    while offset < len(extra):
        if offset + 4 > len(extra):
            raise ValueError("Métadonnées ZIP tronquées")
        tag, size = struct.unpack_from("<HH", extra, offset)
        offset += 4
        # Unknown extras may carry link targets; accept only timestamps and uid/gid.
        if tag not in (0x5455, 0x7875) or tag in seen or offset + size > len(extra):
            raise ValueError("Métadonnées ZIP inconnues, liens ou ZIP64 refusés")
        seen.add(tag)
        offset += size


def _member_metadata(info: zipfile.ZipInfo, data: bytes, limits: PacketLimits) -> str:
    name = info.orig_filename
    parts = name[:-1].split("/") if name.endswith("/") else name.split("/")
    if (
        not name
        or len(name) > 512
        or name.startswith("/")
        or "\\" in name
        or ":" in name
        or any(part in ("", ".", "..") for part in parts)
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise ValueError("Chemin de membre ZIP absolu, ambigu ou traversant refusé")
    mode = stat.S_IFMT(info.external_attr >> 16)
    if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or (mode == stat.S_IFDIR) != info.is_dir() and mode != 0:
        raise ValueError("Liens et fichiers spéciaux ZIP refusés")
    if info.flag_bits & (1 | 0x40) or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ValueError("Chiffrage ou compression ZIP non pris en charge")
    if info.file_size > limits.member_bytes or info.file_size > max(1, info.compress_size) * limits.compression_ratio:
        raise ValueError("Taille ou ratio de décompression ZIP excessif")
    if info.is_dir() and info.file_size:
        raise ValueError("Un répertoire ZIP ne doit pas contenir de données")
    _extra_fields(info.extra)
    offset = info.header_offset
    if offset < 0 or offset + 30 > len(data) or data[offset : offset + 4] != b"PK\x03\x04":
        raise ValueError("En-tête local ZIP invalide")
    flags, method = struct.unpack_from("<HH", data, offset + 6)
    name_size, extra_size = struct.unpack_from("<HH", data, offset + 26)
    end = offset + 30 + name_size + extra_size
    if end > len(data) or flags != info.flag_bits or method != info.compress_type:
        raise ValueError("En-têtes ZIP local et central incohérents")
    _extra_fields(data[offset + 30 + name_size : end])
    return unicodedata.normalize("NFC", name.rstrip("/")).casefold()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Clé JSON dupliquée")
        result[key] = value
    return result


def _integer(value: str) -> int:
    if len(value) > 128:
        raise ValueError("Entier JSON trop long")
    return int(value)


def _constant(value: str) -> None:
    raise ValueError("Nombre JSON non fini refusé")


def _json_object(data: bytes, limits: PacketLimits, *, require_object: bool) -> tuple[str, ...]:
    if len(data) > limits.json_bytes:
        raise ValueError("Document JSON trop volumineux")
    try:
        texte = data.decode("utf-8")
        if require_object:
            root = reglages.decoder(texte, convertir_entier=_integer)
        else:
            root = json.loads(texte, object_pairs_hook=_pairs, parse_int=_integer, parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("Document JSON UTF-8 invalide ou trop profond") from error
    if require_object and not isinstance(root, dict):
        raise ValueError("project_settings doit être un objet JSON explicite")
    stack: list[tuple[Any, int]] = [(root, 0)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if depth > limits.json_depth or nodes > limits.json_nodes:
            raise ValueError("Profondeur ou nombre d’éléments JSON excessif")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Nombre JSON non fini refusé")
        if isinstance(value, dict):
            stack.extend((child, depth + 1) for child in value.values())
            nodes += len(value)
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
    if nodes > limits.json_nodes:
        raise ValueError("Nombre d’éléments JSON excessif")
    return tuple(sorted(root)) if isinstance(root, dict) else ()


def inspect_packet(path: Path, *, limits: PacketLimits = DEFAULT_LIMITS) -> PacketReport:
    """
    Lit et hache un instantané en mémoire, sans extraction ni exécution.

        Seul le membre exact Metadata/project_settings.config est identifié comme
        configuration de projet. Les autres JSON sont contrôlés syntaxiquement.
        Aucun contenu XML, G-code, MD5, mapping ou profil n'est interprété.

    """
    if not isinstance(path, Path) or type(limits) is not PacketLimits:
        raise ValueError("Un chemin local et des limites explicites valides sont requis")
    data = _read_source(path, limits.archive_bytes)
    declared_count = _central_budget(data, limits)
    members: list[PacketMember] = []
    gcode: list[str] = []
    json_members: list[str] = []
    project: ProjectSettings | None = None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) != declared_count or len(entries) > limits.members:
                raise ValueError("Nombre de membres ZIP excessif")
            names: set[str] = set()
            total = 0
            for info in entries:
                portable_name = _member_metadata(info, data, limits)
                if portable_name in names:
                    raise ValueError("Membres ZIP dupliqués ou chemins ambigus")
                names.add(portable_name)
                total += info.file_size
                if total > limits.total_bytes:
                    raise ValueError("Taille totale décompressée ZIP excessive")
            for info in entries:
                parse_json = info.filename == PROJECT_SETTINGS or info.filename.lower().endswith(".json")
                if parse_json and info.file_size > limits.json_bytes:
                    raise ValueError("Document JSON trop volumineux")
                digest = hashlib.sha256()
                content = bytearray()
                actual_size = 0
                with archive.open(info) as stream:
                    while chunk := stream.read(_CHUNK):
                        actual_size += len(chunk)
                        if actual_size > min(info.file_size, limits.member_bytes):
                            raise ValueError("Taille réelle du membre ZIP incohérente")
                        digest.update(chunk)
                        if parse_json:
                            content.extend(chunk)
                if actual_size != info.file_size:
                    raise ValueError("Taille réelle du membre ZIP incohérente")
                sha256 = digest.hexdigest()
                members.append(
                    PacketMember(info.filename, info.file_size, info.compress_size, info.CRC, sha256, info.is_dir())
                )
                if parse_json:
                    keys = _json_object(bytes(content), limits, require_object=info.filename == PROJECT_SETTINGS)
                    json_members.append(info.filename)
                    if info.filename == PROJECT_SETTINGS:
                        project = ProjectSettings(info.filename, sha256, keys)
                if not info.is_dir() and info.filename.lower().endswith(".gcode"):
                    gcode.append(info.filename)
    except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError, OSError) as error:
        raise ValueError("Archive ZIP illisible, incohérente ou CRC invalide") from error
    unknowns = [
        "Compatibilité du paquet Anycubic non établie ; aucun mapping ACE déduit.",
        "XML, relations 3MF, profils, G-code et purge non interprétés.",
    ]
    if project is None:
        unknowns.append("Configuration de projet au chemin exact attendu absente.")
    if not gcode:
        unknowns.append("Aucun membre G-code identifié ; nature projet ou fabrication inconnue.")
    return PacketReport(
        hashlib.sha256(data).hexdigest(),
        len(data),
        tuple(sorted(members, key=lambda member: member.name)),
        True,
        project,
        tuple(sorted(gcode)),
        tuple(sorted(json_members)),
        tuple(unknowns),
    )


def main(argv=None, *, prive=None):
    """Inspecte un paquet 3MF en lecture seule, affiche le rapport ou publie un JSON neuf.

    Budgets CLI en Mio, Kio ou compte selon le champ. Code 0 pour inspection
    terminée, 2 si refus. Le résumé exige un rapport complet neuf dans
    l’atelier privé. Aucun verdict physique ou autorisation d’envoi.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--sortie", "--json", type=Path, help="Rapport neuf, sans écrasement")
    parser.add_argument("--resume", action="store_true", help="Affichage court ; exige --sortie")
    fields = [
        ("archive-mio", "archive_bytes", 1024**2),
        ("membre-mio", "member_bytes", 1024**2),
        ("total-mio", "total_bytes", 1024**2),
        ("membres", "members", 1),
        ("ratio", "compression_ratio", 1),
        ("json-kio", "json_bytes", 1024),
        ("profondeur-json", "json_depth", 1),
        ("noeuds-json", "json_nodes", 1),
    ]
    for option, field, scale in fields:
        parser.add_argument(
            "--" + option, type=int, default=None, help="Budget explicite ; voir PAQUETS-3MF.md pour le plafond"
        )
    args = parser.parse_args(argv)
    try:
        cible = args.sortie
        if args.resume:
            cible = destination_rapport_optionnel(cible, resume=True, nom="du paquet 3MF", prive=prive)
        values = vars(DEFAULT_LIMITS).copy()
        for option, field, scale in fields:
            value = getattr(args, option.replace("-", "_"))
            if value is not None:
                values[field] = value * scale
        limits = PacketLimits(**values)
        outputs = [cible] if cible is not None else []
        with PublicationNeuve([args.source], outputs) as publication:
            report = asdict(inspect_packet(args.source, limits=limits))
            report.update(
                schema="atelier.paquet-3mf.v1",
                statut="INSPECTION_STRUCTURELLE_TERMINEE",
                ready_to_send=False,
                physically_validated=False,
                limits=asdict(limits),
            )
            serialized = json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2) + "\n"
            if cible is not None:
                publication.temporaire(cible).write_text(serialized, encoding="utf-8")
                publication.publier()
            else:
                print(serialized, end="")
        if args.resume:
            # L’inventaire par membre reste dans le JSON intégral (#35).
            resume = {
                "statut": report["statut"], "archive_sha256": report["archive_sha256"],
                "archive_size": report["archive_size"], "crc_verified": report["crc_verified"],
                "nombre_membres": len(report["members"]), "nombre_membres_gcode": len(report["gcode_members"]),
                "ready_to_send": report["ready_to_send"], "physically_validated": report["physically_validated"],
                "rapport": str(cible),
            }
            print(json.dumps(resume, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (OSError, ValueError) as error:
        print("Inspection refusée : " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
