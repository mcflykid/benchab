"""Charge le profil unique ; aucune lecture de TOML machine n'existait dans le paquet."""

import os
import tomllib
from importlib.resources import files
from pathlib import Path


def charger():
    """Charge le TOML machine explicite, embarqué ou du dépôt, dans cet ordre."""
    explicite = os.environ.get("BENCHAB_MACHINE")
    if explicite:
        with Path(explicite).open("rb") as flux:
            return tomllib.load(flux)
    embarque = files("benchab").joinpath("reference/kobra-s1.toml")
    if embarque.is_file():
        return tomllib.loads(embarque.read_text())
    source = Path(__file__).resolve().parents[2] / "config/kobra-s1.toml"
    with source.open("rb") as flux:
        return tomllib.load(flux)
