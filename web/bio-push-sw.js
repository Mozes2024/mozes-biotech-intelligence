/* BIO push handling only. Never caches research snapshots or invents market events. */
self.addEventListener('push',event=>event.waitUntil((async()=>{
 let x;try{x=event.data.json()}catch(_){return}
 if(!x||typeof x.id!=='string'||typeof x.title!=='string'||typeof x.body!=='string')return;
 const target=new URL(x.url||'./#alerts',self.registration.scope);
 if(target.origin!==self.location.origin||!target.pathname.startsWith('/mozes-biotech-intelligence/'))return;
 const cache=await caches.open('mozes-push-receipts-v1');
 const ids=Array.isArray(x.ids)&&x.ids.length?x.ids.filter(id=>typeof id==='string').slice(0,20):[x.id];
 const receipt=id=>new Request(new URL('__push_receipt__/'+encodeURIComponent(id),self.registration.scope));
 const unseen=[];for(const id of ids)if(!await cache.match(receipt(id)))unseen.push(id);
 if(!unseen.length)return;
 const partial=unseen.length<ids.length;
 await self.registration.showNotification(partial?`MOZES - ${unseen.length} אירועים מאומתים חדשים`:x.title,{body:partial?'פתח את רשימת ההתראות לפרטי האירועים החדשים.':x.body,tag:x.id,lang:'he',dir:'rtl',data:{url:target.href}});
 for(const id of unseen)await cache.put(receipt(id),new Response(String(Date.now())));
 const entries=await cache.keys();for(const old of entries.slice(0,-150))await cache.delete(old);
})()));
self.addEventListener('notificationclick',event=>{event.notification.close();event.waitUntil((async()=>{
 const target=event.notification.data?.url||new URL('./#alerts',self.registration.scope).href;
 const tabs=await self.clients.matchAll({type:'window',includeUncontrolled:true});
 const tab=tabs.find(t=>t.url.startsWith(self.registration.scope));if(tab){await tab.navigate(target);await tab.focus()}else await self.clients.openWindow(target);
})())});
