"""Contrôle le plan et les octets conservés lors du complément des vecteurs."""

import json
from zipfile import ZipFile

from benchab.fabrication.vecteurs_machine import CONFIG, canoniser


def test_copie_machine_exacte_ou_refusee(tmp_path):
    """Un complément sûr ne touche que le JSON projet et un écart ambigu bloque la copie."""
    source = tmp_path / "source.3mf"
    sortie = tmp_path / "sortie.3mf"
    donnees = {
        "printer_settings_id": "Anycubic Kobra S1 0.4 nozzle",
        "machine_max_jerk_x": ["15"],
    }
    for bloquer in (False, True):
        with ZipFile(source, "w") as archive:
            archive.comment = b"temoin"
            archive.writestr("3D/3dmodel.model", b"<model/>")
            archive.writestr(CONFIG, json.dumps({
                **donnees, **({"machine_max_speed_x": ["600"]} if bloquer else {}),
            }))
        avant = source.read_bytes()
        destination = tmp_path / "refusee.3mf" if bloquer else sortie
        rapport = canoniser(source, destination, prive=tmp_path)
        assert source.read_bytes() == avant
        if bloquer:
            assert rapport["statut"] == "NON VÉRIFIÉ"
            assert not destination.exists()
        else:
            assert rapport["statut"] == "PASS"
            with ZipFile(sortie) as archive:
                assert archive.comment == b"temoin"
                assert archive.read("3D/3dmodel.model") == b"<model/>"
                assert json.loads(archive.read(CONFIG)) == {**donnees, "machine_max_jerk_x": ["15"] * 3}
