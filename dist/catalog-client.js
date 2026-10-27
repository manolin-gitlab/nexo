/* Same-origin shared catalog client; no tokens or browser-persistent copies. */
(function(root){
'use strict';
class CatalogError extends Error {
  constructor(message,status=0){super(message);this.status=status;}
}
class Client {
  constructor({base='',fetcher=fetch,headers={}}={}){this.base=base;this.fetcher=fetcher.bind(root);this.headers=headers;this.revision=null;}
  async request(method,catalog){
    const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),8000);
    try{
      const headers={...this.headers};const options={method,headers,credentials:'same-origin',cache:'no-store',redirect:'error',referrerPolicy:'same-origin',signal:controller.signal};
      if(method==='PUT'){
        if(this.revision===null)throw new CatalogError('Carga primero el catálogo del servidor.');
        headers['Content-Type']='application/json';headers['If-Match']='"'+this.revision+'"';options.body=JSON.stringify(catalog);
      }
      const response=await this.fetcher(this.base+'/api/v1/catalog',options);
      if(!response.ok){
        const messages={401:'La sesión de administrador caducó. Recarga e inicia sesión.',403:'El servidor rechazó el origen de esta página.',409:'Otro navegador cambió el catálogo. Actualiza los datos antes de volver a guardar.',413:'El catálogo supera los 5 MB.',400:'El servidor rechazó los datos del catálogo.'};
        throw new CatalogError(messages[response.status]||'El servidor no pudo completar la operación.',response.status);
      }
      if(!response.headers.get('Content-Type')?.startsWith('application/json'))throw new CatalogError('Respuesta de catálogo no válida.');
      const raw=await response.text();if(raw.length>5_000_000)throw new CatalogError('Respuesta de catálogo demasiado grande.');
      const data=JSON.parse(raw);
      if(!Number.isSafeInteger(data.revision)||data.revision<0||!data.catalog)throw new CatalogError('Respuesta de catálogo incompleta.');
      this.revision=data.revision;return data.catalog;
    }catch(error){if(error instanceof CatalogError)throw error;throw new CatalogError(error.name==='AbortError'?'El servidor no respondió en 8 segundos.':'No se pudo acceder al catálogo del servidor.');}
    finally{clearTimeout(timeout);}
  }
  load(){return this.request('GET');}
  save(catalog){return this.request('PUT',catalog);}
}
const api={Client,CatalogError};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.NexoCatalog=api;
})(typeof window!=='undefined'?window:globalThis);
