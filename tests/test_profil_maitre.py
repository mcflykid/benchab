"""Contrat du profil maître avec une configuration entièrement synthétique."""

import pytest

from benchab.controles.capacites_machine import _limites
from benchab.machine.profil_maitre import charger_machine, limites_configurees


def _fait(value, unit, kind="configuration", source_ids=("configuration",)):
    return {
        "value": value,
        "unit": unit,
        "type": kind,
        "source_ids": list(source_ids),
        "date_reference": "2026-11-04",
        "version_reference": "synthetique-v1",
        "confidence": "test synthétique",
        "conditions": "fixture générée pour test",
        "verification_status": "NON_VERIFIE",
    }


def profil_synthetique():
    sources = {
        name: {
            "sha256": "0" * 64,
            "date_reference": "2026-11-04",
            "origine": "fixture synthétique générée dans BENCHAB",
        }
        for name in ("materiel", "configuration")
    }
    return {
        "schema": 1,
        "version": 1,
        "configuration_materielle_id": "kobra-test-v1",
        "sources": sources,
        "machine": {
            "modele": _fait("Anycubic Kobra S1 Combo", "texte", "configuration", ("materiel",)),
            "cinematique": _fait("corexy", "texte", "configuration", ("materiel",)),
            "volume_mm": _fait([250, 250, 250], "mm", "configuration", ("materiel",)),
            "buse_mm": _fait(0.4, "mm", "configuration", ("materiel",)),
            "buse_matiere": _fait("brass", "texte", "configuration", ("materiel",)),
            "chargeur": _fait("ACE 2 Pro", "texte", "configuration", ("materiel",)),
        },
        "configuration_par_defaut": "atelier-test-v1",
        "configurations": {
            "atelier-test-v1": {
                "disponible": True,
                "snapshot_source_id": "configuration",
                "parametres": {
                    "machine_max_speed_x": _fait([100, 80], "mm/s"),
                    "machine_max_speed_y": _fait([90, 85], "mm/s"),
                    "machine_max_speed_z": _fait([20, 20], "mm/s"),
                    "machine_max_speed_e": _fait([35, 35], "mm/s"),
                    "machine_max_acceleration_x": _fait([5000], "mm/s2"),
                    "machine_max_acceleration_y": _fait([4000], "mm/s2"),
                    "machine_max_acceleration_z": _fait([500], "mm/s2"),
                    "machine_max_acceleration_e": _fait([1000], "mm/s2"),
                    "machine_max_acceleration_extruding": _fait([3000], "mm/s2"),
                    "machine_max_acceleration_travel": _fait([4000], "mm/s2"),
                    "machine_max_acceleration_retracting": _fait([2000], "mm/s2"),
                    "nozzle_diameter": _fait([0.4], "mm"),
                    "printable_height": _fait(250, "mm"),
                },
            }
        },
        "constructeur": {},
    }


def test_limites_preservent_mode_index_et_minimum_xy():
    profile = charger_machine(profil_synthetique())
    limits = limites_configurees(profile, mode_index=0)

    assert limits["vitesse_xy_mm_s"] == 90
    assert limits["vitesse_z_mm_s"] == 20
    assert limits["sources"]["vitesse_xy_mm_s"]["mode_index"] == 0
    assert limits["non_verifie"]
    assert _limites(profile)[0]["acceleration_xy_mm_s2"] == 4000


def test_mode_index_absent_est_refuse():
    with pytest.raises(ValueError, match="mode_index absent"):
        limites_configurees(profil_synthetique(), mode_index=2)


def test_profil_maitre_implicite_absent_explicite(monkeypatch):
    monkeypatch.delenv("BENCHAB_MACHINE", raising=False)
    with pytest.raises(ValueError, match="Profil maître absent"):
        charger_machine()
