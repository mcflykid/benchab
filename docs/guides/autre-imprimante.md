# Adapter à une autre imprimante

Le parcours actuel qualifie uniquement la Kobra S1. Copier le [profil TOML](../../config/kobra-s1.toml)
dans `prive/machines/`, puis renseigner dimensions utiles, marges, pas de plaques,
buse, slicer et limites. Sélectionner le fichier par `BENCHAB_MACHINE` et vérifier
`uv run benchab configuration`. Ne pas modifier les profils installés.

Créer des profils explicitement nommés dans les fichiers de travail. Vérifier
coordonnées CLI/interface, champs filaments, matériaux et G-code embarqué.
Exécuter les tests, préparer les témoins S/M/L, rouvrir les fichiers tranchés et
faire mesurer les impressions par l’utilisateur. Documenter les écarts et les
limites physiques avant toute qualification. Un chargement TOML ou un tranchage
réussi ne qualifie pas la nouvelle machine. Aucun mécanisme de connexion machine
n’est nécessaire pour cette procédure.

Le [parcours des premiers essais](premiers-essais.md) relie témoins numériques,
mesures physiques et saisie du retour.
