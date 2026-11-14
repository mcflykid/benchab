// L’état de lecture est lié au SHA de la référence. Aucun profil ni maillage n’est réécrit.
const categoriesAtelier = {surplomb: 'Surplombs', epaisseur: 'Épaisseur', assise: 'Assise'};
const categorieRepere = {
  surplomb_geometrique: 'surplomb', surplomb_connexe: 'surplomb',
  epaisseur_locale: 'epaisseur', assise_locale: 'assise',
};
const couleursAtelier = {surplomb: 0xf6aa33, epaisseur: 0xc35189, assise: 0x42cbb2};
const cleAtelier = 'benchab-preparation:' + D.source.sha256;
let etatAtelier = {};
let plateauAtelier = null;
let axesAtelier = null;
let repereAtelier = null;
let atelierCharge = false;
let comparaisonAtelier = D.atelier?.comparaison;
let diagnosticRetour = null;
let photoRetour = null;
let tranchageEnCours = false;
let comparaisonInitiale = null;
let tracesAppuis = null;
let poseAppuis = null;
let choixAppuis = [];
const boutonsIntentions = [
  ['atelierComparerRapide', 'rapide'],
  ['atelierComparerHautDebit', 'haut-debit'],
  ['atelierComparerSupports', 'supports'],
  ['atelierComparerResistance', 'resistance'], ['atelierComparerSilence', 'silencieux'],
  ['atelierComparerRemplissage', 'remplissage'], ['atelierComparerFinition', 'finition'],
  ['atelierComparerCouches', 'couches-variables'],
];
const cleIntentions = 'benchab-intentions-v1:' + D.source.sha256;
const cleDernieresIntentions = 'benchab-dernieres-intentions-v1';
let provenanceContraintes = null;

function contraintesAtelier() {
  const usage = $('atelierUsage').value;
  const exposition = [...$('atelierContraintes').querySelectorAll('input:checked')].map(c => c.value);
  return {usage, exposition, source_sha256: D.source.sha256, origine: 'déclaration utilisateur',
    repris_de_sha256: provenanceContraintes};
}

function actualiserContraintes() {
  const {usage, exposition} = contraintesAtelier();
  const messages = usage ? ['Usage déclaré : ' + usage + '.'] : [];
  if (usage === 'assemblage') messages.push('Vérifier les jeux et les surfaces d’appui sur le modèle.');
  if (exposition.length) messages.push('Exposition : ' + exposition.join(', ') +
    '. Compatibilité matière non vérifiée. Vérifier la fiche du filament pour les conditions d’usage.');
  if (usage || exposition.length) messages.push(provenanceContraintes ? 'Contraintes reprises explicitement.' :
    'Contraintes déclarées dans cet atelier.');
  $('atelierContraintesEtat').textContent = messages.join(' ');
}

function installerContraintes() {
  const bloc = $('atelierContraintes'), reprise = $('atelierReprendreIntentions');
  let sauvegarde;
  try {
    sauvegarde = JSON.parse(localStorage.getItem(cleIntentions) || localStorage.getItem(cleDernieresIntentions) || 'null');
  } catch (_erreur) { /* Stockage facultatif. */ }
  reprise.hidden = !/^[a-f0-9]{64}$/.test(sauvegarde?.source_sha256 || '');
  if (!reprise.hidden && sauvegarde.source_sha256 !== D.source.sha256) {
    reprise.textContent = 'Reprendre les contraintes du modèle précédent';
    bloc.open = true;
  }
  bloc.onchange = actualiserContraintes;
  $('atelierEnregistrerIntentions').onclick = () => {
    sauvegarde = contraintesAtelier();
    try {
      localStorage.setItem(cleIntentions, JSON.stringify(sauvegarde));
      localStorage.setItem(cleDernieresIntentions, JSON.stringify(sauvegarde));
      reprise.hidden = false;
      reprise.textContent = 'Reprendre les contraintes sauvegardées';
      actualiserContraintes();
      $('atelierContraintesEtat').append(' Contraintes sauvegardées sur ce poste.');
    } catch (_erreur) { $('atelierContraintesEtat').textContent = 'Stockage local indisponible.'; }
  };
  reprise.onclick = () => {
    provenanceContraintes = sauvegarde.repris_de_sha256 || sauvegarde.source_sha256;
    $('atelierUsage').value = sauvegarde.usage || '';
    for (const c of bloc.querySelectorAll('input')) {
      c.checked = Array.isArray(sauvegarde.exposition) && sauvegarde.exposition.includes(c.value);
    }
    actualiserContraintes();
  };
  $('atelierReinitialiserIntentions').onclick = () => {
    provenanceContraintes = null;
    $('atelierUsage').value = '';
    for (const c of bloc.querySelectorAll('input')) c.checked = false;
    actualiserContraintes();
  };
  actualiserContraintes();
}

async function identifierPhotoRetour() {
  const input = $('atelierPhotoDefaut'), fichier = input.files[0];
  const etat = $('atelierPhotoEtat');
  photoRetour = null;
  actualiserRetour();
  etat.textContent = '';
  if (!fichier) return;
  if (fichier.size > 16 * 1024 * 1024 || !/\.(jpe?g|png|hei[cf])$/i.test(fichier.name)) {
    etat.textContent = 'Photo JPEG, PNG ou HEIC de 16 Mio au plus.';
    return;
  }
  try {
    const empreinte = new Uint8Array(await crypto.subtle.digest('SHA-256', await fichier.arrayBuffer()));
    if (input.files[0] !== fichier) return;
    photoRetour = {sha256: [...empreinte].map(n => n.toString(16).padStart(2, '0')).join(''), octets: fichier.size};
    etat.textContent = 'Empreinte de la photo prête pour le rapport.';
    actualiserRetour();
  } catch (_erreur) {
    etat.textContent = 'Empreinte de la photo indisponible.';
  }
}

function actualiserRetour() {
  const champ = $('atelierDiagnosticRetour'), defaut = $('atelierDefaut').value;
  diagnosticRetour = null;
  $('atelierConfirmerObservation').hidden = true;
  $('atelierVerifierDefaut').hidden = true;
  champ.textContent = '';
  if (!defaut) return;
  const visibles = diagnostics.filter(d => pieces.some(p => p.nom === d.piece && p.mesh?.visible));
  const nomPiece = $('atelierListePiece').value || repereAtelier?.piece;
  const piece = nomPiece ? visibles.find(d => d.piece === nomPiece) :
    visibles.length === 1 ? visibles[0] : null;
  if (!piece) {
    champ.textContent = 'Sélectionnez la pièce touchée dans « Risques par pièce ».';
    return;
  }
  const hypotheses = {
    decollement: ['Contact au plateau insuffisant', 'Première couche ou température du plateau'],
    'sous-extrusion': ['Débit demandé supérieur au plafond', 'Alimentation du filament ou température de buse'],
    filage: ['Température de buse ou rétraction', 'Filament humide'],
  }[defaut];
  const assise = piece.calques.assise.statut;
  const preuve = comparaisonAtelier?.source_sha256 === D.source.sha256 ? comparaisonAtelier : null;
  const debit = preuve?.reference.debit_plaques.find(p => p.plaque === piece.plaque);
  const depassement = debit?.depassement_etabli === true;
  const indice = defaut === 'decollement' && assise === 'WARN' ? 'Assise signalée sur la carte.'
    : defaut === 'sous-extrusion' && depassement ? 'Un dépassement de débit est relevé sur cette plaque.'
      : 'Cause non vérifiée sur cette pièce.';
  diagnosticRetour = {
    statut: 'NON VÉRIFIÉ', defaut, hypotheses, indice, piece: piece.piece,
    plaque: piece.plaque, source_sha256: D.source.sha256,
    preparation: D.atelier.contexte?.preparation ?? null,
    filaments: D.atelier.reglages.find(r => r.cle === 'filament_settings_id')?.valeur ?? null,
    photo: photoRetour,
    preuve_debit: defaut === 'sous-extrusion' && depassement ? {
      plaque: piece.plaque, depassement_etabli: true, moteur_sha256: preuve.moteur_sha256 ?? null,
    } : null,
    zone: repereAtelier?.piece === piece.piece ? {
      id: repereAtelier.id, type: repereAtelier.type, point_mm: repereAtelier.point_modele,
    } : null,
  };
  $('atelierConfirmerObservation').hidden = !D.atelier.contexte;
  if (!tranchageEnCours && defaut === 'sous-extrusion' && depassement && D.atelier.contexte && D.atelier.variante_locale) {
    $('atelierVerifierDefaut').hidden = false;
    diagnosticRetour.variante_proposee = 'Plafonds ciblés de cette référence';
  }
  champ.textContent = indice + ' À examiner : ' + hypotheses.join(' · ') + '.';
}

async function confirmerObservation(evenement) {
  evenement.preventDefault();
  if (!diagnosticRetour) return;
  const champ = $('atelierConstat'), etat = $('atelierEtatObservation');
  const texte = champ.value.trim();
  if (!texte) { champ.focus(); return; }
  const bouton = evenement.submitter || evenement.currentTarget.querySelector('button');
  const {piece, defaut, zone, photo} = diagnosticRetour;
  bouton.disabled = true;
  try {
    const reponse = await fetch('/__preparation_observation', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...D.atelier.contexte, sha256: D.source.sha256,
        piece, defaut, zone: zone?.id ?? null, photo, texte}),
    });
    const resultat = await reponse.json();
    if (!reponse.ok) throw new Error(resultat.erreur);
    if (champ.value.trim() === texte) champ.value = '';
    const lien = document.createElement('a');
    lien.href = $('atelierObservations').href + '-' + resultat.id;
    lien.textContent = 'Voir l’observation enregistrée';
    etat.replaceChildren(lien);
  } catch (erreur) {
    etat.textContent = erreur.message || 'Enregistrement indisponible.';
  } finally {
    bouton.disabled = false;
  }
}

function memoriserAtelier() {
  if (!atelierCharge) return;
  const etat = {
    calques: [...$('atelierCalques').querySelectorAll('input:checked')].map(c => c.value),
    piece: $('atelierListePiece').value, plateau: !plateauAtelier || plateauAtelier.visible,
    axes: !axesAtelier || axesAtelier.visible,
    onglet: document.querySelector('#plaques button[aria-pressed=true]')?.dataset.k,
  };
  try { localStorage.setItem(cleAtelier, JSON.stringify(etat)); } catch (_e) { /* Stockage facultatif hors ligne. */ }
}

function restaurerOngletAtelier() {
  const onglet = [...$('plaques').children].find(b => b.dataset.k === etatAtelier.onglet);
  atelierCharge = true;
  if (onglet) onglet.click();
  actualiserAtelier();
  if (comparaisonInitiale) {
    const mode = comparaisonInitiale;
    comparaisonInitiale = null;
    void trancherAtelier(mode, {automatique: true});
  }
}

function afficherCalquesAtelier() {
  const actifs = new Set([...$('atelierCalques').querySelectorAll('input:checked')].map(c => c.value));
  const piece = $('atelierListePiece').value;
  for (const p of pieces) for (const o of p.mesh?.children || []) {
    const a = o.userData.anomalie;
    if (a) o.visible = actifs.has(categorieRepere[a.type]) && (!piece || piece === a.piece);
  }
  for (const bouton of $('anomalies').children) {
    const a = anomalies.find(q => q.id === bouton.dataset.anomalie);
    const p = pieces.find(q => q.nom === a?.piece);
    bouton.hidden = !(a && actifs.has(categorieRepere[a.type]) && (!piece || piece === a.piece) && p?.mesh?.visible);
  }
  if (repereAtelier && (!actifs.has(categorieRepere[repereAtelier.type]) ||
      (piece && piece !== repereAtelier.piece) || !pieces.find(p => p.nom === repereAtelier.piece)?.mesh?.visible)) {
    $('diagnostic').hidden = true;
    repereAtelier = null;
  }
}

function actualiserAtelier() {
  afficherCalquesAtelier();
  actualiserRetour();
  visibiliteAppuis();
  const piece = $('atelierListePiece').value;
  const orientables = D.atelier.pieces_orientables || [];
  $('atelierComparerOrientation').disabled = tranchageEnCours ||
    (!orientables.includes(piece) && !(orientables.length === 1 && !piece));
  const visibles = new Set(pieces.filter(p => p.mesh?.visible).map(p => p.nom));
  const choisi = nom => visibles.has(nom) && (!piece || piece === nom);
  $('atelierCartes').replaceChildren();
  for (const d of diagnostics.filter(q => choisi(q.piece))) {
    const a = document.createElement('article');
    const titre = document.createElement('strong');
    titre.textContent = d.piece + (d.plaque != null ? ' • Plaque ' + d.plaque : '');
    a.append(titre);
    if (d.affichage_allege) {
      const note = document.createElement('p');
      note.textContent = 'Vue allégée. Les mesures disponibles partent du maillage source (' +
        nombre(d.faces_source) + ' triangles).';
      a.append(note);
    }
    for (const [cle, mesure] of Object.entries(d.calques)) {
      const p = document.createElement('p');
      const valeurs = [mesure.aire_mm2 == null ? '' : nombre(mesure.aire_mm2) + ' mm² de contact.',
        mesure.surface_surplomb_mm2 == null ? '' : nombre(mesure.surface_surplomb_mm2) + ' mm² de surplombs.',
        mesure.marge_centre_mm == null ? '' : 'Marge ' + nombre(mesure.marge_centre_mm) + ' mm.',
        mesure.minimum_echantillonne_mm == null ? '' : 'Minimum observé ' + nombre(mesure.minimum_echantillonne_mm) + ' mm.'];
      p.textContent = [categoriesAtelier[cle] + ' · ' + mesure.statut, ...valeurs, mesure.detail].filter(Boolean).join(' ');
      a.append(p);
    }
    if (d.orientation_indicative) {
      const o = d.orientation_indicative, p = document.createElement('p');
      p.textContent = 'Pose sur une autre face · surplombs ' + nombre(o.surface_surplomb_source_mm2) +
        ' → ' + nombre(o.surface_surplomb_alternative_mm2) + ' mm² · assise ' +
        nombre(o.assise_alternative_mm2) + ' mm². Estimation géométrique.';
      a.append(p);
    }
    $('atelierCartes').append(a);
  }
  $('atelierPropositions').replaceChildren();
  for (const q of D.atelier.propositions.filter(q => choisi(q.piece))) {
    const a = document.createElement('article');
    const titre = document.createElement('strong'), p = document.createElement('p'), ref = document.createElement('p');
    titre.textContent = q.titre;
    p.textContent = q.piece + ' · ' + q.raison;
    ref.textContent = q.reference + ' dans la référence · ' + (q.actuel ?? 'Non renseigné');
    a.append(titre, p, ref);
    $('atelierPropositions').append(a);
  }
  $('atelierAucune').hidden = $('atelierPropositions').childElementCount > 0;
  memoriserAtelier();
}

function visibiliteAppuis() {
  if (!tracesAppuis) return;
  const onglet = document.querySelector('#plaques button[aria-pressed=true]');
  const visible = (!onglet || onglet.dataset.k === '0' || onglet.textContent === 'Plaque 1') &&
    +$('eclate').value === 0;
  tracesAppuis.visible = visible;
  if (poseAppuis) poseAppuis.visible = visible;
  $('atelierAppuisEtat').textContent = visible ? tracesAppuis.userData.legende :
    'Superposition masquée. Sélectionnez la plaque 1 avec la vue éclatée à 0 %.';
}

function afficherAppuis() {
  for (const groupe of [tracesAppuis, poseAppuis]) if (groupe) {
    root.remove(groupe);
    for (const objet of groupe.children) {
      if (groupe === tracesAppuis) objet.geometry.dispose();
      objet.material.dispose();
    }
  }
  tracesAppuis = poseAppuis = null;
  const choix = $('atelierAppuisChoix').value;
  const couche = $('atelierAppuisCouche'), etat = $('atelierAppuisEtat');
  if (choix === '') { etat.textContent = ''; return; }
  const {appuis, pose} = choixAppuis[Number(choix)];
  const traces = appuis.superposition.traces.filter(t => String(t.z_mm) === couche.value);
  if (!traces.length) {
    etat.textContent = appuis.superposition.parcours_omis ? 'Tracés hors budget pour ce tranchage.' :
      'Aucun dépôt de support ni parcours de pièce sans appui nominal dans les couches analysées.';
    return;
  }
  tracesAppuis = new THREE.Group();
  for (const [type, couleur] of [['support', 0x26c8ea], ['sans_appui', 0xff9d48]]) {
    const positions = [];
    for (const t of traces.filter(t => t.type === type)) {
      for (let i = 2; i < t.xy_mm.length; i += 2) {
        positions.push(t.xy_mm[i - 2], t.xy_mm[i - 1], t.z_mm, t.xy_mm[i], t.xy_mm[i + 1], t.z_mm);
      }
    }
    if (!positions.length) continue;
    const geometrie = new THREE.BufferGeometry();
    geometrie.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    const ligne = new THREE.LineSegments(geometrie, new THREE.LineBasicMaterial({color: couleur, depthTest: false}));
    ligne.renderOrder = 20;
    tracesAppuis.add(ligne);
  }
  root.add(tracesAppuis);
  if (pose?.transformation_mm) {
    const diagnostic = diagnostics.find(d => d.identite_3mf?.build_item === pose.instance);
    const piece = pieces.find(p => p.nom === diagnostic?.piece);
    if (piece?.mesh) {
      poseAppuis = new THREE.Group();
      const contour = new THREE.Mesh(piece.mesh.geometry, new THREE.MeshBasicMaterial({
        color: 0x77a8ff, wireframe: true, transparent: true, opacity: .23,
      }));
      contour.matrixAutoUpdate = false;
      contour.matrix.set(...pose.transformation_mm.flat()).multiply(piece.mesh.matrix);
      poseAppuis.add(contour);
      root.add(poseAppuis);
    }
  }
  tracesAppuis.userData.legende = 'Plaque 1 · cyan : supports déposés · orange : parcours de pièce sans appui nominal.' +
    (pose ? ' Le maillage filaire indique la pose alternative.' : '') +
    (appuis.superposition.parcours_omis ? ' Vue partielle : budget des tracés atteint.' : '');
  visibiliteAppuis();
}

function actualiserChoixAppuis() {
  const choix = $('atelierAppuisChoix'), couche = $('atelierAppuisCouche');
  const rapports = comparaisonAtelier ? [
    {nom: '3MF d’origine', appuis: comparaisonAtelier.reference.appuis},
    ...comparaisonAtelier.variantes.map(v => ({nom: v.nom, appuis: v.mesures?.appuis, pose: v.pose})),
  ] : [];
  const precedent = choix.options[choix.selectedIndex]?.text;
  const z = couche.value;
  choixAppuis = rapports.filter(r => r.appuis?.superposition);
  choix.replaceChildren(new Option('Masquée', ''));
  choixAppuis.forEach((r, i) => choix.add(new Option(r.nom, String(i))));
  const trouve = [...choix.options].find(o => o.text === precedent);
  if (trouve) choix.value = trouve.value;
  $('atelierAppuis').hidden = !choixAppuis.length;
  choix.onchange = () => {
    const actif = choix.value !== '';
    const altitudes = actif ? [...new Set(choixAppuis[Number(choix.value)].appuis.superposition.traces.map(t => t.z_mm))]
      .sort((a, b) => a - b) : [];
    couche.disabled = !altitudes.length;
    const ancienne = couche.value || z;
    couche.replaceChildren(...altitudes.map(h => new Option(nombre(h) + ' mm', String(h))));
    if (altitudes.includes(Number(ancienne))) couche.value = ancienne;
    afficherAppuis();
  };
  couche.onchange = afficherAppuis;
  choix.onchange();
}

function afficherComparaisonAtelier() {
  const bloc = $('atelierComparaison');
  bloc.replaceChildren();
  const decision = $('atelierDecision');
  decision.replaceChildren();
  $('atelierDetailComparaison').hidden = !comparaisonAtelier;
  actualiserChoixAppuis();
  if (!comparaisonAtelier) {
    decision.textContent = D.atelier.etat_comparaison || '';
    if (D.atelier.erreur_comparaison) {
      $('atelierDetailComparaison').hidden = false;
      bloc.textContent = D.atelier.erreur_comparaison;
    }
    return;
  }
  const reference = comparaisonAtelier.reference;
  const variants = comparaisonAtelier.variantes.filter(v => v.mesures && v.ecarts);
  const admissibles = variants.filter(v => v.mesures.debit === 'PASS' &&
    v.commandes_identiques === false && !v.pose &&
    v.mesures.appuis?.parcours_sans_appui <= reference.appuis?.parcours_sans_appui);
  // Le parcours automatique ne recommande qu’une correction de vitesse contrôlée.
  const correction = variants.find(v => v.mesures.debit === 'PASS' && v.commandes_identiques === false &&
    Object.keys(v.reglages).length && Object.keys(v.reglages).every(k => k.endsWith('_speed')));
  const gainDebit = variants.find(v => v.mesures.debit === 'PASS' && v.commandes_identiques === false &&
    Object.keys(v.reglages).join() === 'filament_max_volumetric_speed' &&
    v.ecarts.secondes < 0 && v.ecarts.grammes <= 0);
  if (reference.debit === 'FAIL' && correction) {
    decision.textContent = 'Une variante corrige le dépassement de débit. ' + correction.nom + '.';
  } else if (reference.debit === 'FAIL') {
    decision.textContent = gainDebit ? 'Le profil haut débit déclaré respecte son plafond au tranchage. ' +
      'Gain estimé : ' + nombre(-gainDebit.ecarts.secondes) + ' s.' :
      'Débit trop élevé. Aucune correction vérifiée dans cette comparaison.';
  } else if (reference.debit !== 'PASS') {
    decision.textContent = 'Le débit de la référence reste à vérifier.';
  } else if (!comparaisonAtelier.variantes.length) {
    decision.textContent = 'Les réglages d’origine respectent le plafond de débit.';
  } else if (!variants.length) {
    decision.textContent = 'Le débit d’origine respecte le plafond. Les variantes n’ont pas abouti.';
  } else if (admissibles.some(v => v.ecarts.secondes < 0 && v.ecarts.grammes <= 0)) {
    decision.textContent = 'Une variante réduit le temps estimé. Consultez les appuis avant de choisir.';
  } else {
    decision.textContent = 'Conserver les réglages d’origine.';
  }
  const titre = document.createElement('h2');
  titre.textContent = comparaisonAtelier.tranchage_existant
    ? 'Bilan du G-code fourni' : 'Estimations du trancheur';
  bloc.append(titre);
  const p = document.createElement('p');
  const ref = comparaisonAtelier.reference;
  const duree = s => {
    const secondes = Math.round(Math.abs(s));
    const heures = Math.floor(secondes / 3600), minutes = Math.floor(secondes / 60) % 60;
    return [heures && heures + ' h', minutes && minutes + ' min', secondes % 60 + ' s'].filter(Boolean).join(' ');
  };
  const signe = n => n > 0 ? '+' : n < 0 ? '−' : '';
  // U25 compare le débit commandé au plafond déclaré de chaque outil.
  const debit = m => {
    const plaques = (m.debit_plaques || []).filter(p => p.pic_mm3_s != null).map(p =>
      'plaque ' + p.plaque + ' : pic ' + nombre(p.pic_mm3_s) + ' mm³/s' +
      (p.plafonds_mm3_s.length ? ' (plafond' + (p.plafonds_mm3_s.length > 1 ? 's ' : ' ') +
        p.plafonds_mm3_s.map(nombre).join(' / ') + ' mm³/s)' : ''));
    return 'Débit ' + m.debit + (plaques.length ? ' · ' + plaques.join(' · ') : '');
  };
  const depots = m => {
    const bilan = m.depots_par_filament;
    if (!bilan) return '';
    const masses = bilan.lignes.filter(d => d.grammes != null).map(d =>
      'plaque ' + d.plaque + ', filament ' + d.filament + ' : ' + nombre(d.grammes) + ' g');
    const statut = bilan.statut === 'PASS' ? '' : ' (lecture partielle ou indisponible)';
    return ' Dépôts commandés' + statut + (masses.length ? ' · ' + masses.join(' · ') : '') + '.';
  };
  p.textContent = '3MF d’origine · ' + duree(ref.secondes) + ' · ' + nombre(ref.grammes) + ' g · ' +
    debit(ref) + '.' + depots(ref);
  bloc.append(p);
  for (const v of comparaisonAtelier.variantes) {
    const ligne = document.createElement('p');
    const roles = {
      inner_wall_speed: 'Paroi intérieure', outer_wall_speed: 'Paroi extérieure',
      sparse_infill_speed: 'Remplissage clairsemé', internal_solid_infill_speed: 'Remplissage plein interne',
      gap_infill_speed: 'Comblement des interstices', top_surface_speed: 'Surfaces supérieures',
    };
    const reglages = Object.entries(v.reglages).map(([k, valeur]) => {
      const ecart = (v.differences_3mf || []).find(d => d.chemin === 'projet/' + k);
      const avant = ecart && ecart.present_avant;
      const typeSupport = x => x === 'tree(auto)' ? 'arborescents' : 'classiques';
      if (k === 'layer_height') return 'Hauteur de couche : ' +
        (avant ? nombre(Number(ecart.avant)) + ' → ' : '') + nombre(Number(valeur)) + ' mm';
      if (k === 'enable_support') return valeur === '1' ? 'Supports activés' : 'Supports désactivés';
      if (k === 'wall_loops') return 'Parois : ' + (avant ? ecart.avant + ' → ' : '') + valeur;
      if (k === 'top_shell_layers') return 'Couches supérieures pleines : ' + (avant ? ecart.avant + ' → ' : '') + valeur;
      if (k === 'top_shell_thickness') return 'Épaisseur des dessus : ' + (avant ? ecart.avant + ' → ' : '') + valeur + ' mm';
      if (k === 'sparse_infill_density') return 'Densité : ' + (avant ? ecart.avant + ' → ' : '') + valeur;
      if (k === 'filament_max_volumetric_speed') return 'Plafond filament : ' +
        (avant ? ecart.avant.join(' / ') + ' → ' : '') + valeur.join(' / ') + ' mm³/s';
      if (k === 'support_type') return 'Type de supports : ' +
        (avant ? typeSupport(ecart.avant) + ' → ' : '') + typeSupport(valeur) + ' automatiques';
      return (roles[k] || k) + ' : ' +
        (avant ? nombre(Number(ecart.avant)) + ' → ' : '') + nombre(Number(valeur)) + ' mm/s';
    });
    const titre = reglages.length ? v.nom + ' (' + reglages.join(' ; ') + ')' : v.nom;
    if (v.erreur) ligne.textContent = titre + ' · ' + v.erreur;
    else {
      const e = v.ecarts, m = v.mesures;
      ligne.textContent = titre + ' · ' + duree(m.secondes) + ' · ' + nombre(m.grammes) +
        ' g. Écart : ' + signe(e.secondes) + duree(e.secondes) + ' · ' +
        signe(e.grammes) + nombre(Math.abs(e.grammes)) + ' g. ' + debit(m) + '.';
      if (v.commandes_identiques) ligne.textContent += ' Le G-code contient les mêmes commandes que la référence.';
      if (v.motif_export_refuse) ligne.textContent += ' Export refusé. ' + v.motif_export_refuse;
      ligne.textContent += depots(m);
      if ('plaques_avec_supports' in m && ('enable_support' in v.reglages || 'support_type' in v.reglages)) {
        ligne.textContent += m.plaques_avec_supports.length
          ? ' Dépôts de support sur les plaques ' + m.plaques_avec_supports.join(', ') + ' (' +
            nombre(m.trajets_supports_mm) + ' mm de trajet observé).'
          : m.supports_verifies ? ' Aucun dépôt de support relevé.' : ' Dépôts de support non vérifiés.';
      }
      if (v.pose && m.supports_verifies && ref.supports_verifies) {
        ligne.textContent += ' Trajets déposés en support : ' + nombre(ref.trajets_supports_mm) +
          ' → ' + nombre(m.trajets_supports_mm) + ' mm.';
      }
      if (v.pose && m.estimations_plaques && ref.estimations_plaques) {
        const autre = m.estimations_plaques.filter(p => p.plaque !== v.pose.plaque);
        if (autre.length) {
          const stables = autre.every(p => ref.estimations_plaques.some(r =>
            r.plaque === p.plaque && r.secondes === p.secondes && r.grammes === p.grammes));
          ligne.textContent += stables ? ' Mêmes estimations sur les autres plaques.'
            : ' Les estimations changent aussi sur une autre plaque. Voir le rapport par plaque.';
        }
      }
      if (v.autres_plaques?.length) {
        ligne.textContent += v.autres_plaques.every(p => p.commandes_identiques)
          ? ' Mêmes commandes sur les autres plaques.'
          : ' Des commandes changent aussi sur une autre plaque.';
      }
      if (m.appuis?.statut === 'PASS' && ref.appuis?.statut === 'PASS') {
        ligne.textContent += ' Parcours sans appui nominal : ' + ref.appuis.parcours_sans_appui +
          ' → ' + m.appuis.parcours_sans_appui + '.';
        if (m.appuis.parcours_sans_appui > ref.appuis.parcours_sans_appui) {
          ligne.textContent += ' Cette variante augmente le nombre de parcours sans appui nominal.';
        }
        if (ref.appuis.portee_libre_max_mm != null && m.appuis.portee_libre_max_mm != null) {
          ligne.textContent += ' Portée libre maximale : ' + nombre(ref.appuis.portee_libre_max_mm) +
            ' → ' + nombre(m.appuis.portee_libre_max_mm) + ' mm.';
        }
      } else if (m.appuis) {
        ligne.textContent += ' Comparaison des appuis non vérifiée.';
      }
      if (v.export && m.debit !== 'FAIL' && !v.export.limite_navigateur &&
          (v.export.octets_base64 || location.protocol === 'file:')) {
        const lien = document.createElement('a');
        lien.download = v.export.fichier;
        lien.textContent = 'Télécharger le 3MF de préparation';
        if (v.export.octets_base64) {
          lien.href = '#';
          lien.onclick = evenement => {
            evenement.preventDefault();
            const octets = Uint8Array.from(atob(v.export.octets_base64), c => c.charCodeAt(0));
            const adresse = URL.createObjectURL(new Blob([octets], {type: 'model/3mf'}));
            const copie = document.createElement('a');
            copie.href = adresse;
            copie.download = lien.download;
            copie.click();
            setTimeout(() => URL.revokeObjectURL(adresse), 1000);
          };
        } else {
          lien.href = v.export.fichier;
        }
        ligne.append(' ', lien);
      } else if (v.export?.limite_navigateur) {
        ligne.append(' Export volumineux. Disponible avec la commande d’export de l’atelier.');
      }
      if (m.hauteurs_gcode) {
        ligne.append(m.hauteurs_variables_observees
          ? ' Le G-code contient ' + m.hauteurs_gcode.distinctes + ' hauteurs de couche distinctes.'
          : ' Effet des hauteurs variables non établi dans le G-code.');
      }
    }
    bloc.append(ligne);
  }
}

async function trancherAtelier(mode, {automatique = false} = {}) {
  if (tranchageEnCours) return;
  tranchageEnCours = true;
  const bouton = $('atelierComparer'), procede = $('atelierComparerProcede');
  const orientation = $('atelierComparerOrientation');
  const intentions = boutonsIntentions.map(([id]) => $(id));
  const marge = $('atelierMarge'), generer = $('atelierGenerer');
  bouton.disabled = procede.disabled = orientation.disabled = marge.disabled = generer.disabled = true;
  intentions.forEach(b => b.disabled = true);
  comparaisonAtelier = null;
  genererResumeAtelier(!automatique && window.matchMedia('(min-width: 861px)').matches);
  $('atelierDecision').textContent = 'Comparaison en cours…';
  try {
    const reponse = await fetch('/__preparation_comparer', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...D.atelier.contexte, sha256: D.source.sha256, marge: marge.checked, mode,
        ...(mode === 'orientation' ? {piece: $('atelierListePiece').value || D.atelier.pieces_orientables[0]} : {})}),
    });
    const resultat = await reponse.json();
    if (!reponse.ok) throw new Error(resultat.erreur || 'Comparaison indisponible.');
    comparaisonAtelier = resultat;
    $('atelierGenerer').hidden = true;
    afficherComparaisonAtelier();
    actualiserRetour();
  } catch (erreur) {
    $('atelierDecision').textContent = 'Comparaison indisponible.';
    $('atelierDetailComparaison').hidden = false;
    $('atelierDetailComparaison').open = true;
    $('atelierComparaison').textContent = erreur.message;
    if (automatique) {
      const relancer = $('atelierGenerer');
      relancer.textContent = 'Relancer la comparaison';
      relancer.hidden = false;
    }
  } finally {
    tranchageEnCours = false;
    bouton.disabled = procede.disabled = orientation.disabled = marge.disabled = generer.disabled = false;
    intentions.forEach(b => b.disabled = false);
    actualiserAtelier();
  }
}

function genererResumeAtelier(focus = true) {
  const changements = D.atelier.vitesses.filter(v => v.apres_mm_s < v.avant_mm_s);
  const alertes = diagnostics.filter(d => Object.values(d.calques).some(c => c.statut === 'WARN')).length;
  const incomplet = diagnostics.some(d => Object.values(d.calques).some(c => c.statut === 'NON VÉRIFIÉ'));
  $('atelierBilan').textContent = alertes ? (alertes === 1 ? '1 pièce à vérifier.' : alertes + ' pièces à vérifier.') :
    incomplet ? 'Certaines zones restent à vérifier.' : 'Aucun risque géométrique signalé.';
  const liste = $('atelierVitesses'), ecarts = $('atelierExplication');
  liste.replaceChildren();
  ecarts.replaceChildren();
  const note = document.createElement('p');
  note.textContent = 'Référence : ' + D.atelier.reference + '. ' + D.atelier.note_vitesse;
  ecarts.append(note);
  if (!D.atelier.vitesses.length) liste.textContent = D.atelier.note_vitesse;
  for (const v of D.atelier.vitesses) {
    const p = document.createElement('p');
    p.textContent = v.titre + ' · ' + (v.apres_mm_s == null ? v.raison :
      nombre(v.apres_mm_s) + ' mm/s' + (v.apres_mm_s === v.avant_mm_s ? ' (valeur du fichier)' : ''));
    liste.append(p);
    if (v.apres_mm_s < v.avant_mm_s) {
      const raison = document.createElement('p');
      raison.textContent = v.titre + ' · ' + nombre(v.avant_mm_s) + ' → ' + nombre(v.apres_mm_s) +
        ' mm/s. Plafond de ' + nombre(v.plafond_mm3_s) + ' mm³/s déclaré pour ' + v.filament +
        ', avec une ligne de ' + nombre(v.largeur_mm) + ' mm et une couche de ' + nombre(v.hauteur_mm) + ' mm.';
      ecarts.append(raison);
    }
  }
  afficherComparaisonAtelier();
  $('atelierResultat').hidden = false;
  if (focus) $('atelierTitreResume').focus();
}

function installerAtelier() {
  document.body.classList.add('preparation');
  for (const id of ['atelierGauche', 'atelierResume', 'atelierOutils', 'atelierHome']) $(id).hidden = false;
  const inspection = $('atelierInspection');
  inspection.hidden = false;
  for (const element of [...inspection.parentElement.children].slice(
    [...inspection.parentElement.children].indexOf(inspection) + 1
  )) inspection.append(element);
  $('atelierReference').textContent = D.atelier.reference;
  $('contexte').hidden = true;
  const limites = D.atelier.debit_declare || [];
  $('atelierDebitDeclare').textContent = limites.length ? 'Plafonds de débit déclarés · ' + limites.map(d =>
    d.filament + ' : ' + (d.plafond_mm3_s == null ? 'non renseigné' : nombre(d.plafond_mm3_s) + ' mm³/s')
  ).join(' · ') : 'Plafond de débit non renseigné.';
  if (D.atelier.export_reference?.statut === 'PASS' && location.protocol === 'file:') {
    const lien = $('atelierExportReference');
    lien.href = lien.download = D.atelier.export_reference.fichier;
    lien.hidden = false;
  }
  if (D.atelier.contexte) {
    const observations = $('atelierObservations');
    observations.href = '/atelier?' + new URLSearchParams({projet: D.atelier.contexte.projet}) + '#observations';
    observations.hidden = false;
  }
  $('atelierSeuil').textContent = D.atelier.seuil;
  $('atelierSansProfil').hidden = D.atelier.reglages.length > 0;
  const plafondPropose = D.atelier.vitesses.some(v => v.apres_mm_s < v.avant_mm_s);
  $('atelierMarge').closest('label').hidden = !plafondPropose;
  const vitesses = plafondPropose || D.atelier.variante_locale;
  const modeConseil = D.atelier.mode_conseille;
  const action = $('atelierGenerer');
  action.hidden = true;
  if (D.atelier.contexte && !comparaisonAtelier) comparaisonInitiale = modeConseil;
  action.onclick = () => trancherAtelier(modeConseil);
  $('atelierTranchage').hidden = !D.atelier.contexte ||
    !(vitesses || D.atelier.variantes_procede || D.atelier.pieces_orientables?.length ||
      D.atelier.intentions_comparables?.length);
  $('atelierComparer').hidden = !vitesses;
  $('atelierComparerProcede').hidden = !D.atelier.variantes_procede;
  $('atelierComparer').onclick = () => trancherAtelier('vitesses');
  $('atelierComparerProcede').onclick = () => trancherAtelier('qualite-supports');
  $('atelierComparerOrientation').hidden = !D.atelier.pieces_orientables?.length;
  $('atelierComparerOrientation').onclick = () => trancherAtelier('orientation');
  for (const [id, mode] of boutonsIntentions) {
    $(id).hidden = !D.atelier.intentions_comparables?.includes(mode);
    $(id).onclick = () => trancherAtelier(mode);
  }
  $('atelierInfo').onclick = () => {
    const ecarts = $('atelierEcarts');
    ecarts.hidden = !ecarts.hidden;
    $('atelierInfo').setAttribute('aria-expanded', String(!ecarts.hidden));
  };
  $('atelierRapport').onclick = () => {
    const a = document.createElement('a');
    const url = URL.createObjectURL(new Blob([JSON.stringify({
      schema: 1, sha256: D.source.sha256, reference: D.atelier, diagnostics, comparaison: comparaisonAtelier,
      diagnostic_retour: diagnosticRetour,
      contraintes: contraintesAtelier(),
    }, (cle, valeur) => cle === 'octets_base64' ? undefined : valeur, 2)], {type: 'application/json'}));
    a.href = url; a.download = 'rapport-benchab.json'; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  $('atelierDefaut').onchange = actualiserRetour;
  $('atelierPhotoDefaut').onchange = identifierPhotoRetour;
  $('atelierConfirmerObservation').onsubmit = confirmerObservation;
  $('atelierVerifierDefaut').onclick = () => trancherAtelier('diagnostic-debit');
  installerContraintes();
  for (const q of D.atelier.reglages) {
    const ligne = document.createElement('div'), terme = document.createElement('dt'), valeur = document.createElement('dd');
    terme.textContent = q.libelle; valeur.textContent = Array.isArray(q.valeur) ? q.valeur.join(' · ') : String(q.valeur);
    ligne.append(terme, valeur); $('atelierReglages').append(ligne);
  }
  try { etatAtelier = JSON.parse(localStorage.getItem(cleAtelier)) || {}; } catch (_e) { /* État neuf. */ }
  for (const [cle, titre] of Object.entries(categoriesAtelier)) {
    const label = document.createElement('label'), c = document.createElement('input');
    label.className = 'c'; c.type = 'checkbox'; c.value = cle;
    c.checked = !Array.isArray(etatAtelier.calques) || etatAtelier.calques.includes(cle);
    label.append(c, document.createTextNode(titre)); $('atelierCalques').append(label);
    c.onchange = actualiserAtelier;
  }
  for (const p of pieces) $('atelierListePiece').append(new Option(p.nom, p.nom));
  if (pieces.some(p => p.nom === etatAtelier.piece)) $('atelierListePiece').value = etatAtelier.piece;
  $('atelierListePiece').onchange = actualiserAtelier;
  const [lo, hi] = D.bornes, taille = Math.max(10, Math.ceil(Math.max(hi[0] - lo[0], hi[1] - lo[1]) / 10) * 10);
  plateauAtelier = new THREE.GridHelper(taille, Math.min(50, Math.ceil(taille / 10)), 0x557086, 0x8397ae);
  plateauAtelier.rotation.x = Math.PI / 2;
  plateauAtelier.position.set((hi[0] + lo[0]) / 2, (hi[1] + lo[1]) / 2, -0.03);
  axesAtelier = new THREE.AxesHelper(Math.max(5, Math.min(taille / 4, 35)));
  axesAtelier.position.set(lo[0], lo[1], 0);
  root.add(plateauAtelier, axesAtelier);
  function boutonVue(id, objet, valeur) {
    objet.visible = valeur;
    $(id).setAttribute('aria-pressed', String(valeur));
    $(id).onclick = () => {
      objet.visible = !objet.visible; $(id).setAttribute('aria-pressed', String(objet.visible)); memoriserAtelier();
    };
  }
  boutonVue('atelierPlateau', plateauAtelier, etatAtelier.plateau !== false);
  boutonVue('atelierAxes', axesAtelier, etatAtelier.axes !== false);
  $('atelierRotation').onclick = () => {
    controls.autoRotate = !controls.autoRotate;
    $('atelierRotation').setAttribute('aria-pressed', String(controls.autoRotate));
  };
  $('atelierImporter').hidden = location.protocol === 'file:' || location.origin === 'null';
  genererResumeAtelier(false);
}

if (D.atelier) installerAtelier();
