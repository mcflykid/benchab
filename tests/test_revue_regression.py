"""Une vitesse seule ne prouve pas l'appui d'une paroi en surplomb."""

import pytest

from benchab.controles.revue_locale import juger


@pytest.mark.parametrize("vitesse", [15, 100])
def test_vitesse_et_temps_ne_justifient_pas_un_surplomb_sans_appui(vitesse):
    assert (
        juger(
            {
                "type": "Overhang wall",
                "temps_couche_min_s": 100,
                "support_dessous": False,
                "portee_mm": 100,
                "z": [1, 1],
                "vitesse_max": vitesse,
            }
        )
        is None
    )
