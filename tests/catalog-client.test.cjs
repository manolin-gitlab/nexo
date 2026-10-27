const {test}=require('node:test');
const assert=require('node:assert/strict');
const {Client}=require('../dist/catalog-client.js');
const catalog=()=>({clients:[],channels:[],packages:[]});
const response=(revision=0)=>new Response(JSON.stringify({revision,catalog:catalog()}),{headers:{'Content-Type':'application/json'}});
test('calls browser fetch with its global receiver',async()=>{
  const client=new Client({fetcher:function(){assert.equal(this,globalThis);return Promise.resolve(response());}});
  await client.load();
});
test('reads server revision and sends conditional same-origin writes',async()=>{
  const calls=[];const client=new Client({fetcher:async(url,options)=>{calls.push({url,options});return response(calls.length-1);}});
  await assert.rejects(client.save(catalog()),/Carga primero/);
  await client.load();await client.save(catalog());
  assert.equal(calls[1].options.headers['If-Match'],'"0"');
  assert.equal(calls[1].options.credentials,'same-origin');
  assert.equal(calls[1].options.redirect,'error');
  assert.equal(client.revision,1);
  assert.deepEqual(JSON.parse(calls[1].options.body),catalog());
});
test('conflicts preserve revision until an explicit refresh',async()=>{
  let conflict=false;
  const client=new Client({fetcher:async()=>conflict?new Response('{}',{status:409}):response(8)});
  await client.load();conflict=true;
  await assert.rejects(client.save(catalog()),e=>e.status===409);
  assert.equal(client.revision,8);
});
test('rejects login HTML and incomplete or failed responses',async()=>{
  for(const fetcher of [async()=>new Response('<html>login</html>'),async()=>new Response('{}',{headers:{'Content-Type':'application/json'}}),async()=>new Response('',{status:503}),async()=>{throw new TypeError('network');}]){
    const client=new Client({fetcher});await assert.rejects(client.load());assert.equal(client.revision,null);
  }
});
