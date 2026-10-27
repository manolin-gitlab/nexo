/* Experimental trial relay. No playback credentials in persistent browser storage. */
window.NexoStreaming=(()=>{
'use strict';
let data=null,error='',busy=false;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const shared=()=>document.querySelector('meta[name="nexo-catalog"]')?.content==='server';
async function request(path,body){
 const c=new AbortController(),t=setTimeout(()=>c.abort(),8000);
 try{const r=await fetch('/api/v1/streaming'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,credentials:'same-origin',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer',signal:c.signal});
 if(!r.ok)throw Error(({403:'Cliente sin acceso o sesión de administrador no válida.',409:'El paquete no tiene canales habilitados.',503:'El motor de TV está desactivado o no disponible.'})[r.status]||'El servidor rechazó la operación.');
 if(!r.headers.get('Content-Type')?.startsWith('application/json'))throw Error('Respuesta no válida.');
 const text=await r.text();if(text.length>8e6)throw Error('Respuesta demasiado grande.');return JSON.parse(text);
 }finally{clearTimeout(t);}
}
function render(){
 if(location.hash!=='#streaming')return;
 const view=document.querySelector('#view');if(!view)return;
 const states={starting:'Preparando',live:'En vivo',source_failed:'Fuente caída · reintentando',source_stalled:'Fuente detenida · reintentando',storage_limit:'Límite de almacenamiento',spawn_failed:'No se pudo iniciar',supervisor_unavailable:'Supervisor sin datos'};
 const row=(cells)=>'<tr>'+cells.map(c=>'<td>'+c+'</td>').join('')+'</tr>';
 view.innerHTML=`<section class="panel"><div class="panel-top"><div><h2>Entrega de TV <span class="badge">Experimental · prueba SSH</span></h2><p>Ingesta bajo demanda · un proceso por canal · copia sin recodificar</p></div><button data-stream-refresh class="small" ${busy?'disabled':''}>Actualizar</button></div><div class="stream-body"><p role="status">${esc(error||(data?.enabled?'Motor habilitado. Exporta una M3U desde Clientes y ábrela en VLC en el equipo que mantiene el túnel SSH.':shared()?'Motor desactivado. Actívalo con la guía trial/README.md del repositorio.':'Esta función requiere instalar el entorno de prueba en Debian 13.'))}</p><p>Las sesiones caducan tras 30 segundos sin solicitudes. Al cambiar de canal con el límite alcanzado, detén la sesión anterior aquí o espera su caducidad. Una lista y sus enlaces deben mantenerse privados.</p>${data?.enabled?`<div class="stats"><div class="stat"><div class="stat-label">Canales iniciados</div><div class="stat-value">${data.workers.length} / ${data.max_channels}</div></div><div class="stat"><div class="stat-label">Sesiones</div><div class="stat-value">${data.sessions.length} / ${data.max_sessions}</div></div><div class="stat"><div class="stat-label">Caché observada</div><div class="stat-value">${(data.used_bytes/1024**2).toFixed(1)} MiB</div><small>Umbral ${(data.cache_bytes/1024**2).toFixed(0)} MiB</small></div></div>${data.error||data.storage_blocked?'<p class="error">El supervisor no puede entregar el servicio con normalidad. Revisa fuentes y almacenamiento.</p>':''}<h3>Canales</h3><div class="table-wrap"><table><thead><tr><th>CANAL</th><th>ESTADO</th><th>PID</th><th>FALLOS</th></tr></thead><tbody>${data.workers.map(w=>row([esc(w.channel),esc(states[w.state]||w.state),esc(w.pid||'—'),esc(w.failures)])).join('')||row(['Sin canales solicitados','En reposo','—','—'])}</tbody></table></div><h3>Sesiones</h3><div class="table-wrap"><table><thead><tr><th>CLIENTE</th><th>CANAL</th><th>INICIO</th><th>ACCIÓN</th></tr></thead><tbody>${data.sessions.map(s=>row([esc(s.client),esc(s.channel),esc(new Date(s.started).toLocaleTimeString('es-EC')),`<button class="small" data-stream-stop="${esc(s.id)}">Detener</button>`])).join('')||row(['Sin sesiones activas','—','—','—'])}</tbody></table></div>`:''}<p class="help">Este modo usa Python para servir segmentos a través del túnel. La entrega masiva con Nginx, el balanceo y las pruebas con decodificadores siguen pendientes. No se ha medido una capacidad de usuarios en producción.</p></div></section>`;
}
async function refresh(){if(busy||!shared())return;busy=true;try{data=await request('');error='';}catch(e){data=null;error=e.message;}finally{busy=false;render();}}
async function exportPlaylist(client){
 const status=await request('');
 if(!status.enabled)return false;
 const result=await request('/playlist',{client_id:client.id});
 if(typeof result.playlist!=='string'||!result.playlist.startsWith('#EXTM3U\n'))throw Error('Lista no válida.');
 download('nexo-'+client.name.replace(/[^a-z0-9_-]/gi,'_')+'.m3u',result.playlist,'audio/x-mpegurl');
 toast('Lista protegida exportada. Vigencia del enlace: 7 días; se comprueba el acceso en cada segmento.');return true;
}
document.addEventListener('click',async e=>{if(e.target.closest('[data-stream-refresh]'))refresh();const b=e.target.closest('[data-stream-stop]');if(b){b.disabled=true;try{await request('/stop',{session_id:b.dataset.streamStop});await refresh();}catch(e){toast(e.message);b.disabled=false;}}});
window.addEventListener('hashchange',()=>{if(location.hash==='#streaming')refresh();});
document.addEventListener('DOMContentLoaded',()=>{if(location.hash==='#streaming')refresh();setInterval(()=>{if(!document.hidden&&location.hash==='#streaming')refresh();},5000);});
return {render,refresh,isPage:p=>p==='streaming',exportPlaylist};
})();
