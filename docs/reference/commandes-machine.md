# Commandes machine au démarrage

Le contrôle d'envoi compare M201, M203, M204 et M205 aux vecteurs du mode
machine déclaré et aux limites sourcées de `config/kobra-s1.toml`. Il ne confond
jamais ces vecteurs avec des listes de filaments.

Les températures G9111, M104 et M140 sont comparées au filament initial et au
type de plateau déclarés dans le 3MF. Une attente inconnue reste NON VÉRIFIÉE.
Un écart connu donne FAIL ; le contrôle ne corrige jamais le G-code.

La comparaison s'arrête à la première couche. Elle ne prouve ni l'état réel de
l'imprimante ni un changement manuel de mode intervenu pendant l'impression.
Source : profil système installé du fabricant, empreinte et date dans la
configuration machine ; [contrat des réglages](reglages-et-integrite.md).

La déclaration d’envoi peut demander le contrôle de structure avec
`"format_demarrage": "asn-s1-2.0.0.3"`. Pour une Kobra S1 et le générateur
AnycubicSlicerNext 2.0.0.3 déclaré dans le G-code, BENCHAB vérifie le bloc
d’en-tête, le marqueur de première couche et la présence de G9111, M201, M203,
M204 et M205 avec des paramètres avant cette couche. Cette liste correspond
aux témoins S/M/L relus le 11/11/2026 et ne décrit pas tous les démarrages
possibles. Générateur ou modèle non reconnu : NON VÉRIFIÉ.

Le [guide Anycubic sur G9111](https://wiki.anycubic.com/en/software-and-app/new-page-anycubic-slicer-beta%28orca-version%29/g9111-command-customization-guide)
documente la Kobra S1 ; il exclut notamment la S1 Max. La [table Anycubic](https://wiki.anycubic.com/fr/error-codes)
rapproche 10537 d’un écart de diamètre de buse, 10133 de commandes nécessaires
absentes et 10116 d’un fichier tranché anormal. Ces codes concernent notamment
la famille S1. Leur déclenchement par ces témoins et le firmware qui les
émettrait restent NON VÉRIFIÉS.
