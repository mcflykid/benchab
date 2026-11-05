"""Géométries originales générées par BENCHAB ; aucun modèle de projet."""

import math

import manifold3d as mf

from benchab.conception.decoupe import _vers_trimesh


def figurine():
    corps = mf.Manifold.sphere(15, 32).translate([0, 0, 15])
    cou = mf.Manifold.cylinder(22, 5, 5, 32).translate([0, 0, 24])
    tete = mf.Manifold.sphere(12, 32).translate([0, 0, 54])
    return _vers_trimesh(corps + cou + tete)


def technique():
    # Trois bras perpendiculaires : une pose sur un bout ne suffit pas à
    # supprimer les dessous des deux autres bras.
    x = mf.Manifold.cube([50, 10, 10], True)
    y = mf.Manifold.cube([10, 50, 10], True)
    z = mf.Manifold.cube([10, 10, 50], True)
    return _vers_trimesh(x + y + z)


def trois_branches():
    corps = mf.Manifold.cylinder(8, 20, 20, 48).translate([0, 0, 35])
    for i in range(3):
        angle = 2 * math.pi * i / 3
        corps += mf.Manifold.cylinder(40, 6, 6, 32).translate([14 * math.cos(angle), 14 * math.sin(angle), 0])
    return _vers_trimesh(corps)
