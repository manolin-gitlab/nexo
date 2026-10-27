/* Shared, dependency-free telemetry contract and calculations. */
(function(root){
'use strict';
const numeric=(v,min=0,max=Infinity)=>typeof v==='number'&&Number.isFinite(v)&&v>=min&&v<=max;
function snapshot(d,now=Date.now()){
 if(!d||d.schema_version!==1||!Number.isFinite(Date.parse(d.timestamp)))throw Error('Formato de métricas no compatible.');
 if(d.streaming!==undefined&&(!d.streaming||typeof d.streaming!=='object'||['live','starting','failed'].some(k=>!Number.isInteger(d.streaming[k])||d.streaming[k]<0||d.streaming[k]>32)))throw Error('Estado de señales inválido.');
 const age=now-Date.parse(d.timestamp);if(age>20000||age< -10000)throw Error('La muestra está vencida o el reloj del servidor no está sincronizado.');
 if(!d.server||typeof d.server.name!=='string'||!d.cpu||!d.network||!d.connections||!Array.isArray(d.disks))throw Error('La muestra está incompleta.');
 const optional=(v,min=0,max=Infinity)=>v===null||numeric(v,min,max);
 if(!optional(d.cpu.percent,0,100)||!optional(d.network.rx_bps)||!optional(d.network.tx_bps)||!optional(d.network.capacity_bps)||!optional(d.connections.established))throw Error('Valores de métricas inválidos.');
 if(d.memory&&(!numeric(d.memory.total_bytes)||!numeric(d.memory.used_bytes)||!numeric(d.memory.used_percent,0,100)))throw Error('Memoria inválida.');
 if(d.disks.length>100||d.disks.some(v=>!v||typeof v.path!=='string'||!numeric(v.total_bytes)||!numeric(v.used_bytes)||!optional(v.used_percent,0,100)))throw Error('Disco inválido.');
 if(!Array.isArray(d.connections.peers)||d.connections.peers.length>1000||d.connections.peers.some(p=>!p||typeof p.ip!=='string'||!numeric(p.count,1)||!Number.isInteger(p.count)))throw Error('Conexiones inválidas.');
 if(d.sessions!==null&&(!Array.isArray(d.sessions)||d.sessions.length>10000||d.sessions.some(s=>!s||typeof s.id!=='string'||typeof s.ip!=='string'||!optional(s.lat,-90,90)||!optional(s.lon,-180,180)||!optional(s.bitrate_bps))))throw Error('Sesiones inválidas.');
 if(d.processes!==undefined&&(!Array.isArray(d.processes)||d.processes.length>200||d.processes.some(p=>!p||!Number.isInteger(p.pid)||p.pid<1||typeof p.name!=='string'||!optional(p.cpu_percent,0,100)||!numeric(p.rss_bytes))))throw Error('Procesos inválidos.');
 if(!Array.isArray(d.errors)||!Array.isArray(d.network.interfaces))throw Error('Diagnóstico inválido.');
 if(d.cpu.load_average!==undefined&&(!Array.isArray(d.cpu.load_average)||d.cpu.load_average.length!==3||d.cpu.load_average.some(v=>!numeric(v))))throw Error('Carga inválida.');
 for(const k of ['runnable_tasks','blocked_tasks'])if(d.cpu[k]!==undefined&&d.cpu[k]!==null&&(!Number.isInteger(d.cpu[k])||d.cpu[k]<0))throw Error('Planificador inválido.');
 for(const k of ['iowait_percent','steal_percent'])if(d.cpu[k]!==undefined&&!optional(d.cpu[k],0,100))throw Error('Espera de CPU inválida.');
 if(d.memory)for(const k of ['available_bytes','shmem_bytes','swap_total_bytes','swap_used_bytes'])if(d.memory[k]!==undefined&&!optional(d.memory[k]))throw Error('Detalle de memoria inválido.');
 if(d.pressure!==undefined){if(!d.pressure||typeof d.pressure!=='object'||Array.isArray(d.pressure))throw Error('Presión inválida.');for(const resource of ['cpu','memory','io']){const p=d.pressure[resource];if(p===null||p===undefined)continue;if(typeof p!=='object'||!p.some)throw Error('Presión inválida.');for(const key of ['some','full'])if(p[key]!==undefined){const row=p[key];if(!row||['avg10','avg60','avg300'].some(k=>!numeric(row[k],0,100))||!numeric(row.total_us))throw Error('Presión inválida.');}}}
 if(d.mount_checks!==undefined&&(!Array.isArray(d.mount_checks)||d.mount_checks.length>100||d.mount_checks.some(v=>!v||typeof v.path!=='string'||![true,false,null].includes(v.mounted)||(v.filesystem!==null&&typeof v.filesystem!=='string'))))throw Error('Montajes inválidos.');
 for(const disk of d.disks){if(disk.inodes_used_percent!==undefined&&!optional(disk.inodes_used_percent,0,100))throw Error('Inodos inválidos.');if(disk.filesystem!==undefined&&disk.filesystem!==null&&typeof disk.filesystem!=='string')throw Error('Sistema de archivos inválido.');if(disk.storage_kind!==undefined&&!['memory','filesystem','unknown'].includes(disk.storage_kind))throw Error('Tipo de almacenamiento inválido.');}
 return d;
}
function alerts(d){
 if(!d)return [];
 const a=[];
 if(d.cpu.percent>=85)a.push({level:d.cpu.percent>=95?'critical':'warning',title:'CPU elevada',detail:`${d.cpu.percent.toFixed(1)}% de utilización`});
 if(d.memory&&d.memory.used_percent>=90)a.push({level:d.memory.used_percent>=95?'critical':'warning',title:'Memoria alta',detail:`${d.memory.used_percent.toFixed(1)}% de RAM utilizada`});
 const load=d.cpu.load_average?.[2],cpus=d.server.cpu_count;
 if(numeric(load)&&numeric(cpus,1)&&load>cpus)a.push({level:'warning',title:'Carga sostenida elevada',detail:`15 min: ${load.toFixed(2)} / ${cpus} CPU · ${(load/cpus).toFixed(2)} tareas/CPU; incluye esperas`});
 for(const disk of d.disks){if(disk.used_percent>=90)a.push({level:disk.used_percent>=95?'critical':'warning',title:disk.storage_kind==='memory'?'tmpfs cerca de su límite':'Disco con poco espacio',detail:`${disk.path} · ${disk.used_percent.toFixed(1)}% utilizado`});if(disk.inodes_used_percent>=90)a.push({level:'warning',title:'Pocos inodos disponibles',detail:`${disk.path} · ${disk.inodes_used_percent.toFixed(1)}% utilizado`});}
 for(const mount of d.mount_checks||[])if(mount.mounted!==true)a.push({level:mount.mounted===false?'critical':'warning',title:mount.mounted===false?'Montaje esperado ausente':'Montaje sin verificar',detail:mount.path});
 const n=d.network;if(n.capacity_bps>0&&Math.max(n.rx_bps??0,n.tx_bps??0)/n.capacity_bps>=.85)a.push({level:'warning',title:'Red cerca de su capacidad',detail:'85% o más de la capacidad configurada'});
 if(d.sessions_status==='stale'||d.sessions_status==='error')a.push({level:'warning',title:'Sesiones IPTV sin actualizar',detail:'Revisa la integración del servicio de streaming.'});
 if(d.errors.length)a.push({level:'warning',title:'Métricas parciales',detail:d.errors.join(', ')});
 return a;
}
function maskIP(ip){if(ip.includes(':'))return ip.split(':').slice(0,2).join(':')+':••••';return ip.split('.').slice(0,3).join('.')+'.••';}
function project(lat,lon){return [(lon+180)/360*1000,(90-lat)/180*450];}
function demo(now=Date.now()){
 const t=now/1000;
 const cpu=36+9*Math.sin(t/16),mem=57+3*Math.sin(t/50),tx=(162+24*Math.sin(t/22))*1e6;
 const places=[['Quito',-0.18,-78.47],['Guayaquil',-2.17,-79.92],['Bogotá',4.71,-74.07],['Lima',-12.05,-77.04],['Madrid',40.42,-3.7],['Miami',25.76,-80.19]];
 const sessions=places.map(([location,lat,lon],i)=>({id:'demo-'+i,client:'Cliente de ejemplo '+(i+1),channel:['Canal cultural','Noticias de ejemplo','Señal educativa'][i%3],ip:'203.0.113.'+(10+i),started_at:new Date(now-(i+3)*60000).toISOString(),bitrate_bps:4e6+i*200000,location,lat,lon}));
 return {schema_version:1,agent_version:'demo',timestamp:new Date(now).toISOString(),server:{id:'demo',name:'Nodo de demostración',os:'Linux · simulado',kernel:'—',cpu_count:16,uptime_seconds:7*86400,lat:-.18,lon:-78.47},cpu:{percent:cpu,load_average:[2.7,2.4,2.2],runnable_tasks:3,blocked_tasks:0,iowait_percent:.2,steal_percent:0},memory:{total_bytes:32*1024**3,used_bytes:32*1024**3*mem/100,available_bytes:32*1024**3*(1-mem/100),used_percent:mem,shmem_bytes:2*1024**3,swap_total_bytes:8*1024**3,swap_used_bytes:0},pressure:{cpu:{some:{avg10:1.2,avg60:.8,avg300:.4,total_us:12000}},memory:null,io:null},mount_checks:[{path:'/srv/stream-cache',mounted:true,filesystem:'tmpfs'}],disks:[{path:'/',total_bytes:1024**4,used_bytes:1024**4*.43,available_bytes:1024**4*.57,used_percent:43,filesystem:'ext4',storage_kind:'filesystem',inodes_used_percent:4},{path:'/srv/stream-cache',total_bytes:24*1024**3,used_bytes:2*1024**3,available_bytes:22*1024**3,used_percent:8.33,filesystem:'tmpfs',storage_kind:'memory',inodes_used_percent:1}],network:{rx_bps:48e6+8e6*Math.sin(t/20),tx_bps:tx,capacity_bps:1e9,interfaces:[{name:'eth0',rx_bps:48e6,tx_bps:tx}]},connections:{source:'linux_tcp',ports:[80,443],established:24,peers:sessions.map((s,i)=>({ip:s.ip,count:[6,5,4,4,3,2][i]})),truncated:false},sessions,sessions_status:'available',sessions_timestamp:new Date(now).toISOString(),errors:[]};
}
const api={snapshot,alerts,maskIP,project,demo,numeric};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.NexoCore=api;
})(typeof window!=='undefined'?window:globalThis);
