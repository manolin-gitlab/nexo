const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync(require.resolve('../dist/streaming.js'),'utf8');
const core=require('../dist/monitor-core.js');
function setup(response){
 const view={innerHTML:''},events={};const context={location:{hash:'#streaming'},document:{hidden:false,querySelector:s=>s==='#view'?view:{content:'server'},addEventListener:(n,f)=>events[n]=f},setTimeout,clearTimeout,setInterval:()=>{},AbortController,
 fetch:async()=>new Response(JSON.stringify(response),{headers:{'Content-Type':'application/json'}})};
 context.window=context;context.addEventListener=()=>{};vm.runInNewContext(code,context);return {api:context.NexoStreaming,view};
}
test('relay view distinguishes disabled state from a measured idle relay',async()=>{
 const disabled=setup({enabled:false});await disabled.api.refresh();assert.match(disabled.view.innerHTML,/Motor desactivado/);assert.doesNotMatch(disabled.view.innerHTML,/0 \/ 8/);
 const idle=setup({enabled:true,workers:[],sessions:[],max_channels:8,max_sessions:64,used_bytes:0,cache_bytes:512*1024**2});await idle.api.refresh();assert.match(idle.view.innerHTML,/0 \/ 8/);assert.match(idle.view.innerHTML,/Sin sesiones activas/);
});
test('relay view escapes channel/client values and explains experimental limits',async()=>{
 const ui=setup({enabled:true,workers:[{channel:'<script>bad</script>',state:'live',pid:123,failures:0}],sessions:[],max_channels:8,max_sessions:64,used_bytes:0,cache_bytes:1024});await ui.api.refresh();assert.doesNotMatch(ui.view.innerHTML,/<script>/);assert.match(ui.view.innerHTML,/&lt;script&gt;/);assert.match(ui.view.innerHTML,/entrega masiva con Nginx/i);
});
test('signal counters are optional and reject malformed values',()=>{
 const data=core.demo();data.streaming={live:0,starting:1,failed:0};assert.equal(core.snapshot(data),data);data.streaming.live=-1;assert.throws(()=>core.snapshot(data));data.streaming.live=Infinity;assert.throws(()=>core.snapshot(data));
});
