# Paliers radiaux de densité

`benchab.conception.paliers_densite.modificateurs_radiaux` prépare une première
forme de champ de densité pour [#83](https://github.com/mcflykid/benchab/issues/83).
L’appel prend un maillage fermé en mm, un centre explicite dans son repère et
un à cinq paliers. Chaque palier indique `rayon_mm` et `densite`, par exemple
`{"rayon_mm": 7, "densite": "35%"}`. Les rayons croissent avec un pas minimal de
0,2 mm ; la densité est une valeur entière en pourcentage. La matière hors du
dernier rayon n’est pas modifiée.

Le premier palier est l’intersection de la pièce et d’une sphère maillée.
Chaque palier suivant retire la région intérieure déjà attribuée. Le contrôle
vérifie les recouvrements en volume et le confinement à la pièce. Une région
trop petite, vide ou invalide donne NON VÉRIFIÉ et aucun candidat partiel.
Aucun ordre de priorité entre modificateurs n’est requis.

Le résultat fournit des volumes `modifier_part` pour un nouveau projet, sans
écrire dans le projet d’entrée. Un témoin synthétique vérifie que le producteur
3MF conserve les trois rôles et leurs densités. PASS signifie que ces
coquilles ont passé le contrôle géométrique numérique. Leur effet dans le
G-code doit être mesuré séparément. Les champs de distance aux surfaces,
la simulation de contraintes et la tenue physique ne sont pas établis par
cette étape.
