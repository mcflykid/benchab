"""Extraction explicitement autorisée de la session locale, en mémoire uniquement.

Version macOS précisément identifiée. Aucun dump de processus, fichier de jeton,
installation ou modification de configuration. Les constantes cryptographiques
restent dans le logiciel installé et ne sont pas recopiées dans ce dépôt.
"""

import base64
import ctypes
import hashlib
import struct
import sys
from pathlib import Path

APP = Path("/Applications/AnycubicSlicerNext.app/Contents")
BINAIRE_SHA = "618b3fc1b291f30a564c4df2e87df5e4e6598927ae061e3d9a90dd119754a58d"
BIBLIOTHEQUE_SHA = "e7954a486f73dc0950887abfc3b2b805fbb616ffbad82963fa8281579b8e6e03"


def _adresse(blob, adresse, taille):
    if type(adresse) is not int or type(taille) is not int or adresse < 0 or taille <= 0:
        raise ValueError("SESSION_FORMAT_NON_PRIS_EN_CHARGE")
    if len(blob) < 32 or blob[:4] != b"\xcf\xfa\xed\xfe":
        raise ValueError("SESSION_FORMAT_NON_PRIS_EN_CHARGE")
    n = struct.unpack_from("<I", blob, 16)[0]
    pos = 32
    if n > 10000:
        raise ValueError("SESSION_FORMAT_NON_PRIS_EN_CHARGE")
    for _ in range(n):
        if pos + 8 > len(blob):
            break
        cmd, size = struct.unpack_from("<II", blob, pos)
        if size < 8 or pos + size > len(blob):
            break
        if cmd == 0x19 and size >= 72:
            vm, _, offset, file_size = struct.unpack_from("<QQQQ", blob, pos + 24)
            start = offset + adresse - vm
            if vm <= adresse and adresse + taille <= vm + file_size and 0 <= start <= len(blob) - taille:
                return blob[start : start + taille]
        pos += size
    raise ValueError("SESSION_FORMAT_NON_PRIS_EN_CHARGE")


def _materiel():
    if sys.platform != "darwin":
        raise ValueError("SESSION_PLATEFORME_NON_PRISE_EN_CHARGE")
    binary = APP / "MacOS/AnycubicSlicerNext"
    library = APP / "Frameworks/libcommon_encrypt.dylib"
    for p in (binary, library):
        if p.is_symlink() or not p.is_file() or p.stat().st_size > 256 * 1024 * 1024:
            raise ValueError("SESSION_LOGICIEL_NON_PRIS_EN_CHARGE")
    blob = binary.read_bytes()
    if (
        hashlib.sha256(blob).hexdigest() != BINAIRE_SHA
        or hashlib.sha256(library.read_bytes()).hexdigest() != BIBLIOTHEQUE_SHA
    ):
        raise ValueError("SESSION_VERSION_NON_PRISE_EN_CHARGE")
    # X3/X4 de l'appel EVP_DecryptInit_ex observé dans cet exécutable arm64.
    return _adresse(blob, 0x103E7D37B, 32), _adresse(blob, 0x103E7D39B, 16), library


def _dechiffrer(value, key, iv, lib):
    if not isinstance(value, str) or len(value) > 32768:
        raise ValueError("SESSION_CHIFFREMENT_INVALIDE")
    encrypted = base64.b64decode(value, validate=True)
    if not encrypted or len(encrypted) % 16:
        raise ValueError("SESSION_CHIFFREMENT_INVALIDE")
    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        raise ValueError("SESSION_CHIFFREMENT_INVALIDE")
    out = ctypes.create_string_buffer(len(encrypted) + 32)
    n = ctypes.c_int()
    last = ctypes.c_int()
    try:
        if lib.EVP_DecryptInit_ex(ctx, lib.EVP_aes_256_cbc(), None, key, iv) != 1:
            raise ValueError("SESSION_CHIFFREMENT_INVALIDE")
        if lib.EVP_DecryptUpdate(ctx, out, ctypes.byref(n), encrypted, len(encrypted)) != 1:
            raise ValueError("SESSION_CHIFFREMENT_INVALIDE")
        if lib.EVP_DecryptFinal_ex(ctx, ctypes.byref(out, n.value), ctypes.byref(last)) != 1:
            raise ValueError("SESSION_CHIFFREMENT_INVALIDE")
        return out.raw[: n.value + last.value].decode("utf-8")
    finally:
        lib.EVP_CIPHER_CTX_free(ctx)


def extraire(cloud):
    """Retourne le jeton à l'appelant explicite ; aucun journal ni cache."""
    if not isinstance(cloud, dict) or len(cloud) > 20:
        raise ValueError("SESSION_FORMAT_NON_PRIS_EN_CHARGE")
    key, iv, library = _materiel()
    identity = library.stat()
    lib = ctypes.CDLL(str(library))
    after = library.stat()
    if (identity.st_dev, identity.st_ino, identity.st_size, identity.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or hashlib.sha256(library.read_bytes()).hexdigest() != BIBLIOTHEQUE_SHA:
        raise ValueError("SESSION_LOGICIEL_MODIFIE")
    lib.EVP_CIPHER_CTX_new.restype = ctypes.c_void_p
    lib.EVP_aes_256_cbc.restype = ctypes.c_void_p
    lib.EVP_DecryptInit_ex.argtypes = [ctypes.c_void_p] * 5
    lib.EVP_DecryptUpdate.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    lib.EVP_DecryptFinal_ex.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    lib.EVP_CIPHER_CTX_free.argtypes = [ctypes.c_void_p]
    try:
        tokens = [v for k, v in cloud.items() if _dechiffrer(k, key, iv, lib) == "access_token"]
        if len(tokens) != 1:
            raise ValueError("SESSION_JETON_ABSENT")
        token = _dechiffrer(tokens[0], key, iv, lib)
        if not token.startswith("eyJ") or len(token) > 16384 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("SESSION_JETON_INVALIDE")
        return token
    except Exception:
        raise ValueError("SESSION_EXTRACTION_REFUSEE") from None
