"""Calcule les empreintes SHA-256 en flux, sans cache ni suivi du lien final.

La comparaison inode/taille/mtime/ctime détecte les modifications ordinaires,
y compris à taille identique et mtime restauré. Ce n'est pas un instantané
contre un acteur privilégié capable de modifier les métadonnées du système.
"""

import hashlib
import os
import stat
from pathlib import Path


def sha256_fichier(path, taille_bloc=1048576):
    """Calculez le SHA-256 hexadécimal d’un fichier régulier lu par blocs.

    Entrées : ``path`` est le chemin source ; ``taille_bloc`` est un nombre d’octets
    strictement positif (1 048 576 par défaut). Sortie : 64 caractères hexadécimaux.
    Un lien final, un fichier spécial ou une mutation est refusé avec ``ValueError`` ;
    une source absente ou inaccessible conserve l’exception système ``OSError``.

    La source et ses métadonnées ne sont jamais écrites ; l’empreinte n’est pas une
    garantie contre un acteur privilégié capable de modifier les métadonnées du système.
    """
    if type(taille_bloc) is not int or taille_bloc <= 0:
        raise ValueError("taille de bloc strictement positive requise")
    path = Path(path)
    if path.is_symlink():
        raise ValueError("empreinte : lien symbolique refusé")
    # Résoudre le parent une fois, notamment l’alias système /var sur macOS.
    path = path.parent.resolve(strict=True) / path.name

    def signature(stat_result):
        return (
            stat_result.st_dev,
            stat_result.st_ino,
            stat_result.st_size,
            stat_result.st_mtime_ns,
            stat_result.st_ctime_ns,
        )

    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as flux:
        avant = os.fstat(flux.fileno())
        if not stat.S_ISREG(avant.st_mode):
            raise ValueError("empreinte : fichier régulier requis")
        h = hashlib.sha256()
        for bloc in iter(lambda: flux.read(taille_bloc), b""):
            h.update(bloc)
        apres = os.fstat(flux.fileno())
    courant = path.lstat()
    if signature(avant) != signature(apres) or signature(apres) != signature(courant):
        raise ValueError("empreinte : fichier modifié pendant la lecture : " + str(path))
    return h.hexdigest()
