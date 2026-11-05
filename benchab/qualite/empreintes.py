"""

SHA-256 en flux, sans cache, refusant une entrée instable ou un lien final.

La comparaison inode/taille/mtime/ctime détecte les modifications ordinaires,
y compris à taille identique et mtime restauré. Ce n'est pas un instantané
contre un acteur privilégié capable de modifier les métadonnées du système.


"""

import hashlib
import os
import stat
from pathlib import Path


def sha256_fichier(path, taille_bloc=1048576):
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
