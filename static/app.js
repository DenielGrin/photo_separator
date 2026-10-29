const $ = (id) => document.getElementById(id);
let presets = [], examples = [], current = null, cover = null, history = [], filter = 'all', busy = false;
const names = {keep:'Взять',reject:'Отклонить',review:'Проверить'};
function el(tag, text, cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
async function api(path, body, method='POST'){
 const options=body===undefined?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
 const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;
}
function presetChanged(){const p=presets[$('preset').value];$('model').value=p.model;$('price-in').value=p.input_price??'';$('price-out').value=p.output_price??'';$('key').value='';}
function price(id){return $(id).value===''?null:Number($(id).value);}
function setBusy(value){busy=value;for(const id of ['run','save','load-cover'])$(id).disabled=value;renderCards();}
function error(e){$('status').textContent=e.message;}
function renderCards(){
 const cards=$('cards');cards.replaceChildren();if(!current)return;
 for(const row of current.results){if(filter!=='all'&&row.decision!==filter)continue;
  const chosen=(cover?.cover??current.cover).includes(row.url);const card=el('article',undefined,'card '+row.decision+(chosen?' selected':''));
  const img=el('img');img.src=row.url;img.alt='Фото '+(row.index+1);img.loading='lazy';img.referrerPolicy='no-referrer';card.append(img);
  const content=el('div',undefined,'card-content');content.append(el('span',names[row.decision]+(cover?.pinned.includes(row.url)?' · закреплено вручную':chosen?' · обложка':''),'badge'));
  content.append(el('h3',`Фото ${row.index+1} · ${row.assessment?.category??'нет оценки'} · ${row.score??'—'}/100`));
  content.append(el('p',row.assessment?.reason??row.error??row.reason_code));
  content.append(el('p',`${row.reason_code} · ${row.metrics?row.metrics.width+'×'+row.metrics.height:'не загружено'}`));
  const a=el('a','Открыть оригинал');a.href=row.url;a.target='_blank';a.rel='noopener noreferrer';content.append(a);
  if(cover&&cover.known_urls.includes(row.url)){
   const actions=el('div',undefined,'actions');for(const [action,label] of [['pin','Закрепить'],['exclude','Исключить'],['reset','Сбросить']]){
    const b=el('button',label);b.disabled=busy;b.onclick=()=>manual(row.url,action);actions.append(b);
   }content.append(actions);
  }card.append(content);cards.append(card);
 }
}
function render(){
 $('output').hidden=false;$('export').disabled=false;
 const c=current.results.reduce((a,r)=>(a[r.decision]++,a),{keep:0,reject:0,review:0});
 $('summary').textContent=`Взять ${c.keep} · отклонить ${c.reject} · проверить ${c.review}. Обложек ${current.cover.length}/6. ${current.latency_ms/1000} с. Расход ≈ $${current.estimated_cost_usd}${current.cost_complete?'':' + неизвестная часть'}.`+(current.shortfall?' Хороших фото меньше трёх.':'')+(!current.semantic_model_used?' ВНИМАНИЕ: технический baseline не понимает содержание фото.':'');
 $('json').textContent=JSON.stringify(current,null,2);$('saved').textContent=cover?`Сохранено: ${cover.cover.length} фото · ревизия ${cover.revision} · закреплено ${cover.pinned.length} · исключено ${cover.excluded.length}`:'Результат ещё не сохранён. Ручной отбор доступен после сохранения.';renderCards();
 $('comparison').hidden=false;const tbody=$('history');tbody.replaceChildren();for(const r of history){const tr=el('tr');const counts=['keep','reject','review'].map(d=>r.results.filter(x=>x.decision===d).length).join(' / ');for(const txt of [r.model,counts,r.cover.length,(r.latency_ms/1000)+' с','$'+r.estimated_cost_usd+(r.cost_complete?'':' + ?')])tr.append(el('td',txt));const td=el('td');const b=el('button','Показать');b.onclick=()=>{current=r;cover=null;render();};td.append(b);tr.append(td);tbody.append(tr);}
}
$('run').onclick=async()=>{try{
 setBusy(true);$('status').textContent='Загрузка и оценка… Обычно до нескольких минут. Дождитесь завершения перед повторным запуском.';
 const p=presets[$('preset').value];const config={provider:p.provider,model:$('model').value.trim(),input_price:price('price-in'),output_price:price('price-out')};if($('key').value)config.api_key=$('key').value;
 const urls=$('urls').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
 current=await api('/api/select',{restaurant_id:$('restaurant').value.trim(),urls,config,policy:{min_score:Number($('score').value),min_confidence:Number($('confidence').value),people_policy:$('people').value}});
 cover=null;history.push(current);render();$('status').textContent='Оценка завершена. Проверьте причины и спорные фото.';
 }catch(e){error(e);}finally{setBusy(false);}};
$('save').onclick=async()=>{if(!current)return;try{setBusy(true);const path='/api/restaurants/'+encodeURIComponent(current.restaurant_id)+'/cover';const state=await api(path);cover=await api(path,{run_id:current.run_id,expected_revision:state.revision},'PUT');render();}catch(e){error(e);}finally{setBusy(false);}};
$('load-cover').onclick=async()=>{try{if(!current)return;cover=await api('/api/restaurants/'+encodeURIComponent(current.restaurant_id)+'/cover');render();}catch(e){error(e);}};
async function manual(url,action){try{setBusy(true);cover=await api('/api/restaurants/'+encodeURIComponent(current.restaurant_id)+'/cover',{url,action,expected_revision:cover.revision},'PATCH');render();}catch(e){error(e);}finally{setBusy(false);}}
$('export').onclick=()=>{const link=el('a');link.href=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:'application/json'}));link.download=current.run_id+'.json';link.click();URL.revokeObjectURL(link.href);};
for(const b of document.querySelectorAll('[data-filter]'))b.onclick=()=>{filter=b.dataset.filter;renderCards();};
$('preset').onchange=presetChanged;
$('example').onchange=()=>{const x=examples[$('example').value];if(x){$('restaurant').value=x.restaurant_id;$('urls').value=x.urls.join('\n');}};
(async()=>{try{presets=(await api('/api/providers')).presets;presets.forEach((p,i)=>{const o=el('option',p.label);o.value=i;$('preset').append(o);});presetChanged();examples=await api('/api/examples');examples.forEach((x,i)=>{const o=el('option',x.name);o.value=i;$('example').append(o);});}catch(e){error(e);}})();
