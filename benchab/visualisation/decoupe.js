// Ce fragment partage scène, caméra, pose et composants avec la visionneuse.
if (D.decoupe) {
  const styleDec=document.createElement('style');styleDec.textContent=`
    #decoupeIntelligente{display:block;width:100%;min-width:0;box-sizing:border-box;overflow-wrap:anywhere}
    #decoupeIntelligente details,#decoupeIntelligente label{min-width:0;max-width:100%}
    #decoupeIntelligente select{display:block;width:100%;min-width:0;box-sizing:border-box}
    #decoupeIntelligente input{box-sizing:border-box}
    #decoupeIntelligente button{max-width:100%;white-space:normal}
    #decoupeIntelligente pre{overflow-wrap:anywhere;font-size:11px}
  `;document.head.append(styleDec);
  const dc = structuredClone(D.decoupe.plan), catalogue = D.decoupe.catalogue;
  const panneau = document.createElement('fieldset'); panneau.id = 'decoupeIntelligente';
  const legende = document.createElement('legend'); legende.textContent = 'Découpe intelligente'; panneau.append(legende);
  document.querySelector('aside, .panneau, #panneau').prepend(panneau);
  const etatDec = document.createElement('p'); etatDec.id = 'etatDecoupe'; etatDec.setAttribute('role','status');
  etatDec.textContent = D.decoupe.rapport.statut+' — '+(D.decoupe.rapport.detail || 'Géométrie calculée, tenue physique non vérifiée.');
  panneau.append(etatDec);
  const ligneN = document.createElement('label'); ligneN.textContent = 'Nombre de pièces';
  const nombreDec = document.createElement('input'); nombreDec.type='number';nombreDec.min=1;nombreDec.max=12;
  nombreDec.value=dc.nombre_pieces;nombreDec.setAttribute('aria-label','Nombre de pièces');ligneN.append(nombreDec);panneau.append(ligneN);
  nombreDec.oninput=()=>{dc.nombre_pieces=Number(nombreDec.value);etatDec.textContent='Nombre modifié : proposer à nouveau ou adapter les coupes avant recalcul.';};
  if(dc.volumes_source?.length>1){
    const zone=document.createElement('details'),titre=document.createElement('summary');titre.textContent='Volumes identifiables dans la source';zone.append(titre);panneau.append(zone);
    const texte=document.createElement('p');texte.textContent=dc.volumes_source.join(', ')+' — '+dc.frontieres_source.statut+' : '+dc.frontieres_source.raison;zone.append(texte);
    const voir=document.createElement('label'),cv=document.createElement('input');cv.type='checkbox';voir.append(cv,' Afficher les volumes source colorés');zone.append(voir);
    cv.onchange=()=>{for(const p of pieces){if(p.groupe==='Volumes source'){p.base_cachee=!cv.checked;p.cache=!cv.checked;}if(p.groupe==='Découpe')p.cache=cv.checked;}maj();};
    const choix=document.createElement('label'),ch=document.createElement('input');ch.type='checkbox';ch.checked=!!dc.separer_volumes_source;choix.append(ch,' Séparer selon les volumes source');zone.append(choix);
    ch.onchange=()=>{dc.separer_volumes_source=ch.checked;dc.coupes=[];dc.nombre_pieces=dc.volumes_source.length;nombreDec.value=dc.nombre_pieces;formulairesDec();dessinerDec();etatDec.textContent='Mode source modifié : confirmer les recouvrements puis recalculer. Aucune frontière partagée n’est devinée.';};
    const ordre=document.createElement('input');ordre.type='text';ordre.value=(dc.priorite_volumes||dc.volumes_source).join(', ');ordre.setAttribute('aria-label','Ordre d’attribution de la matière partagée');ordre.style.width='100%';zone.append(ordre);
    boutonDec(zone,'Confirmer cet ordre d’attribution',()=>{dc.priorite_volumes=ordre.value.split(',').map(v=>v.trim());etatDec.textContent='Ordre confirmé explicitement ; recalculer et inspecter les frontières.';});
  }
  const listeDec=document.createElement('div');panneau.append(listeDec);
  const originalDec=document.createElement('label'),co=document.createElement('input');co.type='checkbox';originalDec.append(co,' Superposer le modèle d’origine');panneau.append(originalDec);
  co.onchange=()=>{const p=pieces.find(p=>p.modele==='Source_originale');if(p){p.base_cachee=!co.checked;p.cache=!co.checked;maj();}};
  const traitsDec=new THREE.Group();scene.add(traitsDec);
  let poignetsDec=[],glisseDec=null;
  const rayonDec=new THREE.Raycaster();
  function boutonDec(parent,texte,fn){const b=document.createElement('button');b.type='button';b.className='sec';b.textContent=texte;b.onclick=fn;parent.append(b);return b;}
  function champDec(parent,texte,valeur,fn){const l=document.createElement('label');l.textContent=texte.startsWith('Coupe ')?texte.split(' ').at(-1):texte;
    const e=document.createElement('input');e.type='number';e.step='any';e.value=valeur;e.style.width='100%';e.style.minWidth='0';e.setAttribute('aria-label',texte);
    l.style.display='grid';l.style.minWidth='0';
    e.oninput=()=>{const v=Number(e.value);if(e.value===''||!Number.isFinite(v)){etatDec.textContent='Coordonnée finie requise';return;}fn(v);dessinerDec();};l.append(e);parent.append(l);return e;}
  function dessinerDec(){
    for(const objet of [...traitsDec.children]){traitsDec.remove(objet);objet.geometry?.dispose();objet.material?.dispose();}
    poignetsDec=[];traitsDec.visible=Number($('eclate').value)===0;
    dc.coupes.forEach((coupe,ci)=>{
      if(!coupe.points?.length)return;
      const pts=coupe.points.map(p=>toWorld(p)),courbe=new THREE.CatmullRomCurve3(pts,true,'catmullrom',.5);
      const contours=coupe.type==='courbe'?[courbe.getPoints(pts.length*4)]:(coupe.boucles?.length?coupe.boucles:[coupe.points]).map(b=>{const ps=b.map(p=>toWorld(p));return [...ps,ps[0]];});
      for(const points of contours){const ligne=new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color:0xff7b31,depthTest:false}));ligne.renderOrder=20;traitsDec.add(ligne);}
      pts.forEach((p,pi)=>{const point=new THREE.Mesh(new THREE.SphereGeometry(D.taille*.006,10,8),new THREE.MeshBasicMaterial({color:0xffd329,depthTest:false}));
        point.position.copy(p);point.userData={coupe:ci,point:pi};point.renderOrder=21;traitsDec.add(point);poignetsDec.push(point);});
      // Seules les positions recalculées sur ce plan servent de repères 3D.
      const calcul=D.decoupe.rapport.coupes?.[ci]?.connecteur;
      const cleClip=c=>JSON.stringify([c?.type,c?.origine,c?.normale,c?.connecteur]);
      if(calcul?.placement&&cleClip(coupe)===cleClip(D.decoupe.plan.coupes[ci])){
        for(const joint of calcul.joints){
          const repere=new THREE.Mesh(new THREE.SphereGeometry(D.taille*.008,12,10),new THREE.MeshBasicMaterial({color:0x13dcc6,depthTest:false}));
          repere.position.copy(toWorld(joint.position_mm));repere.renderOrder=22;traitsDec.add(repere);
        }
      }
    });
  }
  function editePoint(ci,pi,axe,v){dc.coupes[ci].points[pi][axe]=v;dc.coupes[ci].type='courbe';
    etatDec.textContent='Point modifié : surface courbe sélectionnée. Recalculer pour vérifier les volumes.';}
  // Les entrées UV appartiennent au plan de coupe ; le moteur seul contrôle la matière et les gardes.
  function formulairePlacementClip(bloc,coupe,i,dernier){
    if(coupe.type!=='plan'||!['clip-languette','clip-double'].includes(coupe.connecteur.type))return;
    const zone=document.createElement('fieldset'),titre=document.createElement('legend');
    titre.textContent='Positions des clips';zone.append(titre);bloc.append(zone);
    const mode=document.createElement('select');mode.setAttribute('aria-label','Placement des clips coupe '+(i+1));
    for(const [v,t] of [['','Automatique par îlot'],['ligne','Ligne dans le plan'],['grille','Grille dans le plan'],['manuel','Coordonnées manuelles']]){
      const choix=document.createElement('option');choix.value=v;choix.textContent=t;mode.append(choix);
    }
    const placement=coupe.connecteur.placement;mode.value=placement?.mode||'';zone.append(mode);
    const marque=()=>{etatDec.textContent='Positions modifiées : recalculer avant export.';};
    const positionsCalculees=dernier?.connecteur?.placement?.positions_uv_mm;
    mode.onchange=()=>{
      if(!mode.value)delete coupe.connecteur.placement;
      else if(mode.value==='manuel')coupe.connecteur.placement={mode:'manuel',positions_uv_mm:structuredClone(positionsCalculees||[[0,0]])};
      else if(mode.value==='ligne')coupe.connecteur.placement={mode:'ligne',centre_uv_mm:[0,0],colonnes:2,pas_u_mm:14};
      else coupe.connecteur.placement={mode:'grille',centre_uv_mm:[0,0],colonnes:2,lignes:2,pas_u_mm:14,pas_v_mm:14};
      marque();formulairesDec();dessinerDec();
    };
    if(!placement)return;
    const texte=document.createElement('p');
    texte.textContent='U et V sont en millimètres à partir de l’origine du plan. Les repères turquoise apparaissent après calcul.';
    zone.append(texte);
    if(placement.mode==='manuel'){
      placement.positions_uv_mm.forEach((p,j)=>{
        const ligne=document.createElement('div');zone.append(ligne);
        ['U','V'].forEach((axe,k)=>champDec(ligne,axe+' du clip '+(j+1)+' coupe '+(i+1),p[k],v=>{p[k]=v;marque();}));
        if(placement.positions_uv_mm.length>1)boutonDec(ligne,'Retirer le clip '+(j+1),()=>{
          placement.positions_uv_mm.splice(j,1);marque();formulairesDec();dessinerDec();
        });
      });
      if(placement.positions_uv_mm.length<9)boutonDec(zone,'Ajouter un clip manuel',()=>{
        const [u,v]=placement.positions_uv_mm.at(-1);placement.positions_uv_mm.push([u+14,v]);
        marque();formulairesDec();dessinerDec();
      });
      return;
    }
    ['U','V'].forEach((axe,k)=>champDec(zone,'Centre '+axe+' (mm) coupe '+(i+1),placement.centre_uv_mm[k],v=>{placement.centre_uv_mm[k]=v;marque();}));
    const champs=[['colonnes','Colonnes',true],['pas_u_mm','Pas U (mm)',false]];
    if(placement.mode==='grille')champs.push(['lignes','Lignes',true],['pas_v_mm','Pas V (mm)',false]);
    for(const [cle,label,entier] of champs){
      const entree=champDec(zone,label+' coupe '+(i+1),placement[cle],v=>{placement[cle]=v;marque();});
      if(entier){entree.step=1;entree.min=1;entree.max=9;}else{entree.min=.01;entree.max=1000;}
    }
  }
  function formulairesDec(){
    listeDec.replaceChildren();
    dc.coupes.forEach((coupe,i)=>{
      const bloc=document.createElement('details');bloc.open=i===0;listeDec.append(bloc);
      const titre=document.createElement('summary');titre.textContent='Coupe '+(i+1);bloc.append(titre);
      const contexte=document.createElement('p'),dernier=D.decoupe.rapport.coupes?.[i];contexte.textContent=dernier?'Cible calculée : '+dernier.cible+' ; '+dernier.pieces.length+' volumes issus de la coupe.':'Cible automatique : plus grand volume traversé, à vérifier après recalcul.';bloc.append(contexte);
      const type=document.createElement('select');type.setAttribute('aria-label','Type de coupe '+(i+1));
      for(const [v,t] of [['plan','Plane'],['courbe','Courbe fermée']]){const o=document.createElement('option');o.value=v;o.textContent=t;type.append(o);}
      type.value=coupe.type||'plan';type.onchange=()=>{coupe.type=type.value;etatDec.textContent='Type modifié : recalcul requis.';formulairesDec();dessinerDec();};bloc.append(type);
      for(const cle of ['origine','normale']){
        coupe[cle] ||= cle==='normale'?[0,0,1]:(coupe.points?.length?[0,1,2].map(k=>coupe.points.reduce((n,p)=>n+p[k],0)/coupe.points.length):[...D.centre]);
        const row=document.createElement('div');row.style.display='grid';row.style.gridTemplateColumns='repeat(3,minmax(0,1fr))';row.style.gap='5px';bloc.append(row);
        ['X','Y','Z'].forEach((axe,k)=>champDec(row,cle+' '+axe+' coupe '+(i+1),coupe[cle][k],v=>{
          const delta=v-coupe[cle][k];coupe[cle][k]=v;
          if(cle==='origine')for(const p of coupe.points||[])p[k]+=delta;
          if(cle==='origine')for(const boucle of coupe.boucles||[])for(const p of boucle)p[k]+=delta;
          etatDec.textContent='Plan modifié : recalculer pour actualiser la section sur le modèle.';
        }));
      }
      const select=document.createElement('select');select.setAttribute('aria-label','Connecteur coupe '+(i+1));
      for(const [k,v] of Object.entries(catalogue)){const o=document.createElement('option');o.value=k;o.textContent=v;select.append(o);}
      coupe.connecteur ||= {type:'douille'};if(coupe.connecteur.type===undefined)coupe.connecteur.type='douille';select.value=coupe.connecteur.type;select.onchange=()=>{
        const ancien=coupe.connecteur.type;
        if((ancien==='vis-ecrou')!==(select.value==='vis-ecrou')){
          // Un diamètre ou jeu édité pour une autre forme n'est pas une cote de visserie.
          for(const cle of ['diametre','longueur','mesure_jeu','source_cotes','lamage_diametre_mm','lamage_profondeur_mm',
                            'ecrou_sur_plats_mm','ecrou_profondeur_mm','ecrou_orientation_deg'])delete coupe.connecteur[cle];
        }
        coupe.connecteur.type=select.value;if(!select.value.startsWith('clip-'))delete coupe.connecteur.placement;
        etatDec.textContent='Connecteur modifié : recalcul requis.';formulairesDec();dessinerDec();
      };bloc.append(select);
      const visserie=coupe.connecteur.type==='vis-ecrou';
      const dimensions=visserie?
        [['diametre','','Diamètre nominal de la vis (mm)'],['paroi_min',1.2,'Paroi minimale (mm)'],
         ['lamage_diametre_mm','','Diamètre de tête (mm)'],['lamage_profondeur_mm','','Profondeur du lamage (mm)'],
         ['ecrou_sur_plats_mm','','Largeur de l’écrou sur plats (mm)'],['ecrou_profondeur_mm','','Profondeur de l’écrou (mm)'],
         ['ecrou_orientation_deg',0,'Orientation de l’écrou (°)']]:
        [['diametre',6,'Diamètre (mm)'],['longueur',12,'Longueur (mm)'],['paroi_min',1.2,'Paroi minimale (mm)']];
      for(const [cle,val,libelle] of dimensions)champDec(bloc,libelle+' connecteur '+(i+1),coupe.connecteur[cle]??val,v=>{
        coupe.connecteur[cle]=v;etatDec.textContent='Cote modifiée : recalcul requis.';
      });
      if(visserie){
        // La source et les cotes de visserie restent à déclarer, sans dimensions ISO inventées.
        const etiquette=document.createElement('label'),source=document.createElement('input');
        etiquette.textContent='Source des cotes de la visserie';source.type='text';source.maxLength=512;
        source.value=coupe.connecteur.source_cotes||'';source.setAttribute('aria-label','Source des cotes visserie coupe '+(i+1));
        source.oninput=()=>{coupe.connecteur.source_cotes=source.value;etatDec.textContent='Source modifiée : recalcul requis.';};
        etiquette.append(source);bloc.append(etiquette);
      }
      formulairePlacementClip(bloc,coupe,i,dernier);
      const conseil=document.createElement('p');
      function texteConseil(){const initial=coupe.conseil_connecteur;
        conseil.textContent=initial?(coupe.connecteur.type===initial.type?'Proposition retenue : ':'Choix manuel. Proposition initiale : ')+initial.raison:'Choix manuel ; ajustement à contrôler.';}
      texteConseil();bloc.append(conseil);
      for(const alt of coupe.conseil_connecteur?.alternatives||[]){const p=document.createElement('p');p.textContent=(catalogue[alt.type]||alt.type)+' : '+alt.avantage+' ; '+alt.inconvenient;bloc.append(p);}
      const points=document.createElement('details'),s=document.createElement('summary');s.textContent='Points de contrôle en mm';points.append(s);bloc.append(points);
      (coupe.points||[]).forEach((p,j)=>{const row=document.createElement('div');row.style.display='grid';row.style.gridTemplateColumns='repeat(3,minmax(0,1fr))';row.style.gap='5px';const titrePoint=document.createElement('span');titrePoint.textContent='Point '+(j+1);titrePoint.style.gridColumn='1 / -1';row.append(titrePoint);points.append(row);
        ['X','Y','Z'].forEach((axe,k)=>{const e=champDec(row,'Coupe '+(i+1)+' point '+(j+1)+' '+axe,p[k],v=>{editePoint(i,j,k,v);type.value='courbe';});e.id='decPoint_'+i+'_'+j+'_'+k;});});
      boutonDec(bloc,'Retirer la coupe '+(i+1),()=>{dc.coupes.splice(i,1);dc.nombre_pieces=Math.max(1,dc.nombre_pieces-1);nombreDec.value=dc.nombre_pieces;formulairesDec();dessinerDec();etatDec.textContent='Coupe retirée ; recalcul requis.';});
    });
  }
  const commandes=document.createElement('div');commandes.className='boutons';panneau.append(commandes);
  boutonDec(commandes,'Ajouter une coupe',()=>{
    if(dc.coupes.length>=11){etatDec.textContent='Maximum onze coupes';return;}
    const o=[...D.centre],r=D.taille*.2;
    dc.coupes.push({type:'plan',origine:o,normale:[0,0,1],fermee:true,
      points:Array.from({length:12},(_,i)=>[o[0]+r*Math.cos(i*Math.PI/6),o[1]+r*Math.sin(i*Math.PI/6),o[2]]),connecteur:{type:'aucun'}});
    dc.nombre_pieces=Math.min(12,dc.nombre_pieces+1);nombreDec.value=dc.nombre_pieces;formulairesDec();dessinerDec();etatDec.textContent='Coupe ajoutée au centre ; régler le plan puis recalculer.';
  });
  boutonDec(commandes,'Séparer',()=>{$('eclate').value=.65;maj();dessinerDec();etatDec.textContent='Vue éclatée ; édition des points suspendue.';});
  boutonDec(commandes,'Réassembler',()=>{$('eclate').value=0;maj();dessinerDec();etatDec.textContent='Pièces remises dans leur repère commun ; vérifier le rapport numérique.';});
  async function envoyerDec(action,format){
    if(location.protocol==='file:'){etatDec.textContent='Ouvrir cette page avec le serveur local BENCHAB pour calculer et enregistrer.';return;}
    if([...panneau.querySelectorAll('input[type=number]')].some(e=>e.value===''||!e.checkValidity())){etatDec.textContent='FAIL — Corriger les champs numériques avant le calcul.';return;}
    if(action==='exporter'&&JSON.stringify(dc)!==signatureDec){etatDec.textContent='Choix modifiés : recalculer et enregistrer avant export pour voir la géométrie correspondante.';return;}
    for(const b of commandes.querySelectorAll('button'))b.disabled=true;
    etatDec.textContent='Calcul en cours…';
    try{
      const reponse=await fetch('/__decoupe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({page:location.pathname,action,format,plan:dc})});
      const r=await reponse.json();if(!reponse.ok)throw Error(r.erreur||'Calcul refusé');
      if(r.page){location.assign(r.page);return;}
      etatDec.textContent='Export '+r.statut+' enregistré dans '+r.dossier+' — modèles non tranchés, contrôle d’envoi requis.';
    }catch(e){etatDec.textContent='FAIL — '+e.message;}
    finally{for(const b of commandes.querySelectorAll('button'))b.disabled=false;}
  }
  boutonDec(commandes,'Proposer à nouveau',()=>envoyerDec('proposer'));
  boutonDec(commandes,'Recalculer et enregistrer',()=>envoyerDec('recalculer'));
  boutonDec(commandes,'Exporter STL dans le projet',()=>envoyerDec('exporter','stl'));
  boutonDec(commandes,'Exporter 3MF dans le projet',()=>envoyerDec('exporter','3mf'));
  function rayonEvenementDec(ev){const rect=renderer.domElement.getBoundingClientRect();rayonDec.setFromCamera(new THREE.Vector2((ev.clientX-rect.left)/rect.width*2-1,-(ev.clientY-rect.top)/rect.height*2+1),camera);}
  renderer.domElement.addEventListener('pointerdown',ev=>{
    if(Number($('eclate').value)!==0)return;rayonEvenementDec(ev);
    const h=rayonDec.intersectObjects(poignetsDec,false)[0];if(!h)return;
    glisseDec=h.object.userData;controls.enabled=false;renderer.domElement.setPointerCapture(ev.pointerId);ev.stopImmediatePropagation();
  },true);
  renderer.domElement.addEventListener('pointermove',ev=>{
    if(!glisseDec)return;rayonEvenementDec(ev);
    const origine=pieces.find(p=>p.modele==='Source_originale')?.mesh;
    const h=origine?rayonDec.intersectObject(origine,false)[0]:null;if(!h)return;
    const p=h.point.clone().applyMatrix4(root.matrix.clone().invert()).toArray(),{coupe:ci,point:pi}=glisseDec;
    for(let k=0;k<3;k++){editePoint(ci,pi,k,p[k]);const e=$('decPoint_'+ci+'_'+pi+'_'+k);if(e)e.value=p[k].toFixed(3);}
    dessinerDec();ev.stopImmediatePropagation();
  },true);
  renderer.domElement.addEventListener('pointerup',ev=>{if(!glisseDec)return;glisseDec=null;controls.enabled=true;formulairesDec();ev.stopImmediatePropagation();},true);
  renderer.domElement.addEventListener('pointercancel',()=>{glisseDec=null;controls.enabled=true;},true);
  $('eclate').addEventListener('input',dessinerDec);
  formulairesDec();dessinerDec();
  const signatureDec=JSON.stringify(dc);
  const limite=document.createElement('p');limite.textContent='Points jaunes : glisser sur le modèle ou modifier les coordonnées. Toute modification exige un recalcul. Les jeux non mesurés prennent le haut de la plage d’essai ; aucune tenue physique n’est promise.';panneau.append(limite);
  const bilan=document.createElement('details'),br=document.createElement('summary'),pre=document.createElement('pre');br.textContent='Rapport de découpe et ajustements';pre.textContent=JSON.stringify(D.decoupe.rapport,null,2);pre.style.whiteSpace='pre-wrap';bilan.append(br,pre);panneau.append(bilan);
}
