'use strict';
window.NexoUpdates=(()=>{
let data=null,error='',busy=false,lastRefresh=0;
const shared=()=>document.querySelector('meta[name="nexo-catalog"]')?.content==='server';
const clean=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const isPage=p=>p==='actualizaciones';
function render(){
 if(!isPage(location.hash.slice(1)))return;
 const titles={unchecked:'Comprobación pendiente',current:'Tu instalación está al día',available:'Hay cambios disponibles',checking:'Comprobando GitHub…',error:'No se pudo comprobar',incompatible:'Actualización requiere revisión manual',unconfigured:'Actualizaciones sin configurar'};
 const state=error?'error':data?.checking?'checking':data?.state||'unchecked';
 const badge=data?.available?'Nueva revisión':'Estado del servicio';
 document.querySelector('#view').innerHTML=`<section class="panel update-panel"><div class="panel-top"><div><h2>Actualizaciones</h2><p>Origen: manolin-gitlab/nexo · rama main</p></div><span class="badge">${badge}</span></div><div class="update-body"><h3>${titles[state]||'Estado desconocido'}</h3><p>${clean(error||data?.message||(!shared()?'La comprobación está disponible en una instalación con backend.':'Se comprueban revisiones de GitHub; esta página nunca instala ni reinicia servicios por sí sola.'))}</p><dl><dt>Versión instalada</dt><dd>${clean(data?.installed_version||'—')}</dd><dt>Revisión instalada</dt><dd>${clean(data?.installed_commit?.slice(0,12)||'Sin registrar')}</dd><dt>Versión disponible</dt><dd>${clean(data?.latest_version||'—')}</dd><dt>Revisión en GitHub</dt><dd>${clean(data?.latest_commit?.slice(0,12)||'—')}</dd><dt>Última comprobación</dt><dd>${data?.checked_at?clean(new Date(data.checked_at).toLocaleString('es-EC')):'—'}</dd></dl><div class="toolbar"><button class="primary" data-update-check ${busy||!shared()?'disabled':''}>${busy?'Comprobando…':'Comprobar ahora'}</button>${data?.changes_url?.startsWith('https://github.com/manolin-gitlab/nexo/')?`<a class="link-button" href="${clean(data.changes_url)}" target="_blank" rel="noopener noreferrer">Ver cambios en GitHub →</a>`:''}</div>${data?.available&&data.compatible?`<div class="update-instructions"><h3>Instalar desde SSH</h3><p>Ejecuta este comando como root. Crea un respaldo, conserva el catálogo y las credenciales, reinicia los servicios Nexo y revierte si falla la comprobación de salud. Habrá una interrupción breve.</p><textarea readonly aria-label="Comando de actualización">${clean(data.command)}</textarea><button class="small" data-update-copy>Copiar comando</button></div>`:''}<p class="help">Se consulta GitHub con una caché de 15 minutos. Una falla de red se muestra como desconocida. La entrega HLS experimental se activa por separado en el entorno de prueba.</p></div></section>`;
}
async function refresh(force=false){
 if(!shared()||busy)return;busy=true;lastRefresh=Date.now();error='';render();
 const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),8000);
 try{const response=await fetch('/api/v1/updates'+(force?'/check':''),{method:force?'POST':'GET',credentials:'same-origin',cache:'no-store',redirect:'error',headers:force?{'Content-Type':'application/json'}:{},body:force?'{}':undefined,signal:controller.signal});if(!response.ok)throw Error('El servicio no pudo consultar las actualizaciones.');const raw=await response.text();if(raw.length>32000)throw Error('Respuesta demasiado grande.');const next=JSON.parse(raw);if(typeof next.state!=='string')throw Error('Respuesta no válida.');data=next;}catch(e){error=e.name==='AbortError'?'El servicio no respondió.':e.message;}finally{clearTimeout(timer);busy=false;document.querySelectorAll('[data-update-badge]').forEach(n=>n.textContent=data?.available?' · disponible':'');render();}
}
document.addEventListener('click',async event=>{if(event.target.closest('[data-update-check]'))refresh(true);if(event.target.closest('[data-update-copy]')){const input=document.querySelector('[aria-label="Comando de actualización"]');try{await navigator.clipboard.writeText(input.value);event.target.textContent='Copiado';}catch{input.focus();input.select();}}});
document.addEventListener('DOMContentLoaded',()=>{refresh();setInterval(()=>{if(!document.hidden&&(isPage(location.hash.slice(1))||Date.now()-lastRefresh>=60000))refresh();},5000);});
return {isPage,render};
})();
