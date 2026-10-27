const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const core=require('../dist/monitor-core.js');
async function boot(host='localhost',protocol='http:',marker=true){
  const ready=[],calls=[],fields=new Map();
  const $=selector=>{if(!fields.has(selector))fields.set(selector,{addEventListener:()=>{}});return fields.get(selector);};
  const ctx={window:{NexoServers:{isPage:()=>false,record:()=>{throw Error('Local test endpoint must not enter HTTPS inventory');}}},
    document:{addEventListener:(name,fn)=>{if(name==='DOMContentLoaded')ready.push(fn);},querySelector:()=>marker?{content:'loopback'}:null},
    location:{hash:'#qa-no-draw',hostname:host,protocol,origin:protocol+'//'+host+':8088'},localStorage:{getItem:()=>''},
    NexoCore:core,NexoShell:{sync:()=>{}},$: $,esc:String,performance,AbortController,setTimeout,clearTimeout,setInterval:()=>1,clearInterval:()=>{},
    fetch:async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify(core.demo()));}};
  vm.createContext(ctx);vm.runInContext(fs.readFileSync('dist/monitor.js','utf8'),ctx);for(const fn of ready)fn();
  await new Promise(resolve=>setImmediate(resolve));return {ctx,calls};
}
test('test installer automatically reads its own authenticated loopback metrics',async()=>{
  const {ctx,calls}=await boot();assert.equal(ctx.window.NexoMonitor.current().mode,'live');
  assert.equal(calls[0].url,'http://localhost:8088/v1/snapshot');
  assert.equal(calls[0].options.credentials,'same-origin');assert.equal(calls[0].options.headers.Authorization,undefined);
});
test('test metadata does not enable automatic HTTP monitoring on other hosts',async()=>{
  for(const args of [['terminal.local'],['localhost','https:'],['localhost','http:',false]]){
    const {ctx,calls}=await boot(...args);assert.equal(ctx.window.NexoMonitor.current().mode,'idle');assert.equal(calls.length,0);
  }
});
