// Appels uniquement sur action explicite ; le serveur conserve le contrôle d’origine avant lecture Cloud.
const form=document.getElementById("cloud-form"),token=document.getElementById("cloud-token"),machine=document.getElementById("cloud-machine"),out=document.getElementById("cloud-resultat"),direct=document.getElementById("cloud-button"),local=document.getElementById("cloud-session-button"),account=document.getElementById("cloud-account-button");
async function lireCloud(mode){
  const sessionLocale=mode==="session",compte=mode==="compte";
  direct.disabled=true;local.disabled=true;account.disabled=true;out.textContent="Connexion en lecture seule…";let garder=false;
  const demande={machine:machine.value||null};
  if(compte){demande.compte=true;token.value="";}else if(sessionLocale){demande.session_slicer=true;token.value="";}else{demande.jeton=token.value;}
  try{
    const response=await fetch("/__cloud_lecture",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(demande)});
    const data=await response.json();out.textContent=JSON.stringify(data,null,2);
    if(response.ok&&data.statut==="CHOISIR_MACHINE"){
      machine.replaceChildren();
      const choisir=document.createElement("option");choisir.value="";choisir.textContent="Choisir une imprimante";machine.append(choisir);
      for(const imprimante of data.machines){const option=document.createElement("option");option.value=String(imprimante.id);option.textContent=(imprimante.modele||"Modèle non identifié")+" — "+String(imprimante.id);machine.append(option);}
      garder=!sessionLocale&&!compte;direct.textContent="Lire cette imprimante avec mon jeton";local.textContent="Lire cette imprimante via ma session locale";account.textContent="Lire cette imprimante avec mon compte";machine.focus();
    }
  }catch(error){out.textContent="Cloud indisponible : attendre quelques secondes avant une nouvelle lecture. Aucune commande machine envoyée.";}
  finally{if(!garder)token.value="";direct.disabled=false;local.disabled=false;account.disabled=false;}
}
form.addEventListener("submit",event=>{event.preventDefault();lireCloud("jeton");});
local.addEventListener("click",()=>lireCloud("session"));
account.addEventListener("click",()=>lireCloud("compte"));
