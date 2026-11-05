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
