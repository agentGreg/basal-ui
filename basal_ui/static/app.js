
'use strict';
const $ = (s, root=document) => root.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const gb = n => n == null ? '—' : `${(Number(n) / 1e9).toFixed(1)} GB`;
let modelData = [], hw = null, examples = [], pollTimer = null, loadedMedia = [], selectedMedia = [], mediaSelection = 0, statusSequence = 0;
function httpFailure(status,message) {
  const hint={422:t('Invalid request: revise the input.'),503:t('Service unavailable: if GPU memory is exhausted, shorten the input or unload another model before retrying manually.'),500:t('Internal server error: inspect model logs and request ID.')}[status];
  return `${status?`HTTP ${status}. `:''}${hint?hint+' ':''}${message}`;
}
async function api(path, options={}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json',...(options.headers||{})}, ...options});
  const text = await response.text(); let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!response.ok) {  // basal-ui: {"detail": msg}; basal-serve 422: {"error": msg, "detail": [{"msg"}]}
    const detail = typeof data?.detail === 'string' ? data.detail : typeof data?.error === 'string' ? data.error : Array.isArray(data?.detail) ? data.detail.map(d => d?.msg).filter(Boolean).join('; ') : null;
    const message=detail||data?.error?.message||(typeof data==='string'?data:JSON.stringify(data,null,2))||response.statusText;
    const id=response.headers.get('x-request-id');
    throw new Error(`${httpFailure(response.status,message)}${id?` [request ${id}]`:''}`);
  }
  return data;
}
function toast(message, type='success') {
  const node=document.createElement('div'); node.className=`toast ${type}`; node.textContent=message;
  $('#toastStack').append(node); setTimeout(()=>node.remove(),4000);
}
function confirmDialog({title,description,confirmLabel=t('Confirm'),destructive=false}) {
  return new Promise(resolve=>{
    const dialog=$('#confirmDialog'), accept=$('#confirmAccept'), cancel=$('#confirmCancel');
    $('#confirmTitle').textContent=title; $('#confirmDescription').textContent=description;
    accept.textContent=confirmLabel; accept.classList.toggle('destructive',destructive);
    dialog.showModal();
    const finish=value=>{dialog.close();accept.removeEventListener('click',yes);cancel.removeEventListener('click',no);dialog.removeEventListener('close',closed);resolve(value);};
    const yes=()=>finish(true),no=()=>finish(false),closed=()=>finish(false);
    accept.addEventListener('click',yes);cancel.addEventListener('click',no);dialog.addEventListener('close',closed);
  });
}
function applyTheme(theme) {
  const value=['light','dark','system'].includes(theme)?theme:'system';
  document.documentElement.dataset.theme=value==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):value;
  document.documentElement.classList.toggle('dark',document.documentElement.dataset.theme==='dark');
  document.querySelectorAll('.side-nav button').forEach(b=>{if(document.documentElement.dataset.theme==='dark')b.style.setProperty('color','#edf0e9','important');else b.style.removeProperty('color');});
  localStorage.setItem('basal-ui.theme',value);
}
document.querySelectorAll('[data-theme-choice]').forEach(b=>b.onclick=()=>applyTheme(b.dataset.themeChoice));
applyTheme(localStorage.getItem('basal-ui.theme')||'system');
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{if((localStorage.getItem('basal-ui.theme')||'system')==='system')applyTheme('system');});
document.addEventListener('click',e=>{const b=e.target.closest('[data-close]');if(b)$('#'+b.dataset.close).close();});
document.addEventListener('keydown',e=>{
  const typing=e.target.matches('input,textarea,select,[contenteditable=true]');
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter'&&!$('#playground').hidden){e.preventDefault();$('#sendButton').click();}
  else if(!typing&&e.key==='?'&&!e.ctrlKey&&!e.metaKey&&!e.altKey){e.preventDefault();$('#shortcutsDialog').showModal();}
  else if(e.altKey&&!e.ctrlKey&&!e.metaKey&&/^Digit[0-9]$/.test(e.code)){const target=document.querySelector(`.side-nav [data-shortcut="${e.code.slice(5)}"]`);if(target){e.preventDefault();showView(target.dataset.view,true);}}
});
document.addEventListener('change',event=>{
  if(event.target.matches('input[type=checkbox]'))event.target.setAttribute('aria-checked',String(event.target.checked));
});
document.addEventListener('keydown',event=>{
  if(event.key==='Enter'&&event.target.matches('input[type=checkbox][role=switch]')) {
    event.preventDefault(); setChecked(event.target,!event.target.checked);
    event.target.dispatchEvent(new Event('change',{bubbles:true}));
  }
});
document.querySelectorAll('.side-nav [data-view]:not([data-view=backends])').forEach((b,i)=>b.dataset.shortcut=String(i+1));
$('.side-nav [data-view=backends]').dataset.shortcut='0';
function setChecked(input, value) {
  input.checked=Boolean(value);
  input.setAttribute('aria-checked',String(input.checked));
}
document.querySelectorAll('input[type=checkbox]').forEach(input=>{input.setAttribute('role','switch');setChecked(input,input.checked);});
document.querySelectorAll('.icon-button').forEach(button=>{
  const icon=button.id==='logsToggle'
    ?'<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 4h12v12H4zM7 8h6M7 11h6M7 14h3"/></svg>'
    :'<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="7" width="10" height="11" rx="1"/><path d="M4 13V3h10"/></svg>';
  button.className='outline-action button-icon icon-button';
  button.innerHTML=icon+`<span>${esc(button.textContent.trim())}</span>`;
});
function renderHardware(data) {
  hw = data;
  $('#deviceName').textContent = data.device_name || t('Unknown device');
  $('#platform').textContent = data.platform || '—';
  $('#hardwareBudget').textContent = gb(data.memory_bytes);
  $('#availableMemory').textContent = gb(data.available_bytes);
  $('#systemRam').textContent = gb(data.system_ram_bytes);
  $('#engines').innerHTML = (data.engines || []).map(x => `<span class="chip">${esc(x)}</span>`).join('') || `<span class="muted">${t('No engines detected')}</span>`;
}
function familyOrder(family) {
  return ({'basal-2.0':6,'basal-omni-1.1':5,'basal-omni-1.0':4,'basal-omni':3,'basal-1.5':2,'basal-1.0':1})[family] || 0;
}
function tierOrder(tier) { return ({pico:0,mini:1,'1.5B':2,'4.5B':3,max:4,ultra:5})[tier] ?? 9; }
let backendModelFilter=null;
function renderModels(data) {
  modelData = data.models || [];
  renderDiskModels(); renderCalibrationModels(); renderInspectModels();
  const suggestions = data.suggestions || [];
  renderSuggestions(suggestions);
  const overviewSuggestion=$('#overviewSuggested'), firstSuggestion=$('#suggested .suggestion-card');
  overviewSuggestion.replaceChildren(firstSuggestion?firstSuggestion.cloneNode(true):Object.assign(document.createElement('p'),{className:'muted',textContent:t('No suggested model fits right now.')}));
  const slots = new Map(suggestions.map(s => [s.id, s.slot]));
  const older = $('#olderToggle').checked, showBad = $('#notRunnableToggle').checked, downloadedOnly = $('#downloadedToggle').checked;
  const list = modelData.filter(m => (!backendModelFilter||backendModelFilter.ids.has(m.id)) && (older || !['basal-1.0','basal-1.5'].includes(m.family)) && (showBad || m.runnable) && (!downloadedOnly || m.downloaded));
  list.sort((a,b) => familyOrder(b.family)-familyOrder(a.family) || tierOrder(a.tier)-tierOrder(b.tier) || a.port.localeCompare(b.port));
  const groups = new Map(); for (const m of list) { if (!groups.has(m.family)) groups.set(m.family, []); groups.get(m.family).push(m); }
  if (!list.length) { $('#modelsTable').innerHTML = `<div class="empty">${t('No models match these filters.')}</div>`; return; }
  let html = '';
  const tableHead=`<table class="model-variants"><colgroup><col style="width:20%"><col style="width:13%"><col style="width:9%"><col style="width:11%"><col style="width:8%"><col style="width:9%"><col style="width:30%"></colgroup><thead><tr><th>${t('Tier')}</th><th>${t('Format')}</th><th>${t('Size')}</th><th>${t('Est. memory')}</th><th>${t('Fit')}</th><th>${t('Fit now')}</th><th>${t('Action')}</th></tr></thead><tbody>`;
  for (const [family, rows] of groups) {
    html += `<section class="model-family-group"><div class="disk-group-heading"><h4>${esc(family)}</h4><span class="muted">${tp(rows.length,'{n} variant','{n} variants',{n:rows.length})}</span></div><div class="table-wrap">${tableHead}`;
    for (const m of rows) {
      const classes = `fit-${esc(m.fit || 'unknown')}`;
      const nowClasses = `fit-${esc(m.fit_now || 'unknown')}`;
      const reasonText = ts(m.reason, m.reason_code, m.reason_params);
      const reason = !m.runnable && m.reason ? `<span class="reason" title="${esc(reasonText)}">${esc(reasonText)}</span>` : '';
      const slot = slots.get(m.id);
      const mediaReasonText = ts(m.media_reason, m.media_reason_code, m.media_reason_params);
      const mediaReason = !m.media_mode && (m.media || []).length ? `<small class="muted">${esc(mediaReasonText || '')}</small>` : '';
      const downloadStatus=statusCache?.downloads?.[m.repo], loaded=statusCache?.models?.find(x=>x.id===m.id);
      const downloadButton=downloadControl(m,downloadStatus);
      // Models: download, set up and delete; loading is the Model manager's (renderDiskModels)
      const actions=`${!m.downloaded?downloadButton:''}${m.runnable?optionsButton(m.id,false,'save'):''}${loaded?`<span class="chip">${t('loaded')}</span>`:m.downloaded?deleteButton(m.id):''}`;
      html += `<tr class="${m.runnable?'':'not-runnable'}"><td class="model-tier"><span class="model-name">${esc(m.tier)}</span>${reason}${mediaReason}</td><td class="model-format">${esc(m.label)} ${m.community?`<span class="chip">${t('community')}</span>`:''}${m.revision?`<span class="chip">${t('revision {revision}',{revision:esc(m.revision)})}</span>`:''}${m.downloaded?`<small class="model-downloaded muted">${t('On disk')}${m.downloaded_bytes==null?'':` · ${gb(m.downloaded_bytes)}`}</small>`:''}</td><td class="model-size">${gb(m.weights_bytes)}</td><td class="model-size">${gb(m.est_memory_bytes)}</td><td><span class="badge ${classes}">${esc(t(m.fit || 'unknown'))}</span></td><td><span class="badge ${nowClasses}">${esc(t(m.fit_now || 'unknown'))}</span></td><td><div class="model-actions"><button type="button" class="model-card-open" data-id="${esc(m.id)}" aria-label="${t('Model information')}">ⓘ</button>${actions}</div></td></tr>`;
    }
    html += '</tbody></table></div></section>';
  }
  $('#modelsTable').innerHTML = html;
}
const deleteButton=id=>`<button type="button" class="delete-model" data-id="${esc(id)}">${t('Delete')}</button>`;
// the model buttons of every list (Models, Model manager, the Overview's copy of a suggestion card), bound once
document.addEventListener('click',e=>{
  const b=e.target.closest('.download-model,.unload-model,.delete-model,.load-model');
  if(!b||b.disabled)return;
  const id=b.dataset.id;
  if(b.classList.contains('download-model'))act(async()=>{await api('/api/download',{method:'POST',body:JSON.stringify({id})});await refreshStatus();});
  else if(b.classList.contains('unload-model'))act(()=>unloadOne(id));
  else if(b.classList.contains('delete-model'))act(()=>deleteModel(id));
  else loadModel(id,b.dataset.media==='true');
});
async function deleteModel(id) {
  const model=modelData.find(m=>m.id===id);
  if(!model||!await confirmDialog({title:t('Delete model from disk?'),description:t('{model} will be removed from disk.',{model:`${model.family} ${model.tier} (${model.label})`}),confirmLabel:t('Delete'),destructive:true}))return;
  await api('/api/models/delete',{method:'POST',body:JSON.stringify({id})}); toast(t('Model deleted')); await loadModels();
}
// "basal-2.0" / "basal-omni-1.0" written the way basal.si5.pl writes the family names
function familyMark(family) {
  const parts = String(family).match(/^basal-(omni-)?(.+)$/);
  if (!parts) return esc(family);
  return parts[1] ? `basal<span class="family-suffix">-omni</span><small>${esc(parts[2])}</small>` : `basal<span class="family-suffix">-${esc(parts[2])}</span>`;
}
const ARROW = '<svg class="icon" viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10h11m-4-4 4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
function downloadControl(m, progress) {
  if(progress&&!progress.error)return `<button class="download-model" data-id="${esc(m.id)}" disabled>${t('Downloading…')}</button><small class="muted download-progress">${gb(progress.done_bytes)} / ${gb(progress.total_bytes)}</small>`;
  return `<button class="download-model" data-id="${esc(m.id)}">${t('Download')}</button>${progress?.error?`<span class="reason">${esc(progress.error)}</span>`:''}`;
}
function renderSuggestions(suggestions) {
  if (!suggestions.length) { $('#suggested').innerHTML = `<div class="empty">${t('Nothing fits the memory available now; free memory or use All models')}</div>`; return; }
  const models = new Map(modelData.map(m => [m.id, m]));
  $('#suggested').innerHTML = `<div class="suggestion-grid">${suggestions.map(s => {
    const m = models.get(s.id);
    if (!m) return '';
    const why = esc(ts(s.why, s.why_code, s.why_params)).replace(/(--[a-z][\w-]*(?: [a-z][\w.-]*)?)/g, '<code>$1</code>');
    const download=statusCache?.downloads?.[m.repo], loaded=statusCache?.models?.find(x=>x.id===m.id);
    const action=`<div class="controls">${loaded?`<span class="chip">${t('loaded')}</span>`:!m.downloaded?downloadControl(m,download):''}${optionsButton(m.id,false,'save')}${m.downloaded&&!loaded?deleteButton(m.id):''}</div>${m.downloaded&&!loaded?`<a class="dashboard-link" href="#manager">${t('Load in Model manager')}${ARROW}</a>`:''}`;
    return `<article class="suggestion-card${m.family==='omni'?' omni':''}"><div class="eyebrow">${esc(t(s.slot.replace('_',' ')))}</div><h3>${familyMark(m.family)}</h3><button type="button" class="model-card-open" data-id="${esc(m.id)}" aria-label="${t('Model information')}">ⓘ</button><div class="suggestion-tier">${esc(m.tier)} · ${esc(m.label)} ${m.community?`<span class="chip">${t('community')}</span>`:''}${m.revision?`<span class="chip">${t('revision {revision}',{revision:esc(m.revision)})}</span>`:''}</div><div><span class="suggestion-meta">${gb(m.weights_bytes)} → ${gb(m.est_memory_bytes)} ${t('est. memory')}</span></div><div><span class="badge fit-${esc(m.fit_now || 'unknown')}">${esc(t(m.fit_now || 'unknown'))}</span></div><p class="why">${why}</p>${m.downloaded?`<div class="suggestion-downloaded">✓ ${gb(m.downloaded_bytes)} ${t('downloaded')}</div>`:''}${action}</article>`;
  }).join('')}</div>`;
}
document.addEventListener('click',e=>{const b=e.target.closest('.model-card-open');if(b)act(()=>openModelCard(b.dataset.id));});
async function openModelCard(id) {
  const c=await api(`/api/models/card?id=${encodeURIComponent(id)}`);
  const scheme=c.quantisation?.scheme, sameScheme=[c.port,c.label].some(x=>String(x||'').toLowerCase()===String(scheme||'').toLowerCase());
  // family, tier and format are the title; the quantisation only where it says more than the format
  const assessmentReason=ts(c.assessment?.reason,c.assessment?.reason_code,c.assessment?.reason_params);
  const facts=[['Quantisation',sameScheme?null:scheme],['Size',gb(c.size_bytes)],['Media',(c.media||[]).join(', ')||t('none')],['Mode',`${c.assessment?.mode||'—'} · ${c.assessment?.runnable?t('runnable'):assessmentReason||t('not runnable')}`],['Serving',c.quantisation?.engine],['Fit',c.assessment?.fit],['Fit now',c.assessment?.fit_now],['Estimated memory',gb(c.assessment?.est_memory_bytes)],['Downloaded',c.downloaded?t('yes'):t('no')],['Local directory',c.local_dir],['Cached revisions',(c.revisions||[]).join(', ')]];
  const entry=c.loaded, shipped=c.shipped_calibration;
  $('#modelCardTitle').textContent=[c.family,c.tier,c.label].filter(Boolean).join(' · ')||c.id;
  $('#modelCardRepo').innerHTML=`<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.repo)}</a> · ${esc(c.author||'')}${c.community?` <span class="chip">${t('community')}</span>`:''}${c.base_repo?` · ${t('base')} ${esc(c.base_repo)}${c.base_revision?` @ ${esc(c.base_revision)}`:''}`:''}`;
  const shown=facts.filter(([,v])=>v!=null&&v!==''&&v!=='—').map(([k,v])=>[k,k==='Fit'||k==='Fit now'?t(v):v]), wide=new Set(['Local directory','Cached revisions']);  // compact: facts this checkpoint does not have are left out; paths and hashes take a whole row
  $('#modelCardBody').innerHTML=`<dl class="model-card-facts">${shown.map(([k,v])=>`<dt${wide.has(k)?' class="wide"':''}>${esc(t(k))}</dt><dd${wide.has(k)?' class="wide"':''}>${esc(v)}</dd>`).join('')}</dl>${c.quantisation?.description?`<h3>${t('Quantisation')}</h3><p>${esc(c.quantisation.description)}</p>`:''}<h3>${t('Shipped calibration')}</h3>${shipped?.error?`<p class="reason">${esc(shipped.error)}</p>`:shipped?`${shipped.fitted_on&&typeof shipped.fitted_on==='object'?`<p>${t('Fitted on {date} · {decisions} decisions',{date:esc(shipped.fitted_on.data||'—'),decisions:esc(shipped.fitted_on.decisions??'—')})}</p>`:''}<div class="chips">${thresholdCell(shipped.thresholds)}</div>`:`<p class="muted">${t('None')}</p>`}<details><summary>${t('Prompt format')}</summary><pre>${esc(JSON.stringify(c.basal_json?.prompt_format??c.basal_json??null,null,2))}</pre></details>${entry?`<details><summary>${t('Loaded entry')}</summary><pre>${esc(JSON.stringify(Object.fromEntries(['mode','orders','prompt_format','layout','vllm_preset','certified','context'].filter(k=>entry[k]!=null).map(k=>[k,entry[k]])),null,2))}</pre></details>`:''}${c.readme?`<details><summary>${t('README')}</summary><pre>${esc(c.readme)}</pre></details>`:''}`;  // the long JSON / README blocks start collapsed
  $('#modelCardDialog').showModal();
  $('#modelCardBody').scrollTop=0;  // the dialog is reused: a new card starts at its top
}
function renderDiskModels() {
  const rows = modelData.filter(m => m.downloaded), total = rows.reduce((sum,m) => sum + (Number(m.downloaded_bytes) || 0),0);
  const groups=new Map();
  for(const model of rows){
    const type=model.family||'Other';
    if(!groups.has(type))groups.set(type,[]);
    groups.get(type).push(model);
  }
  $('#diskModels').innerHTML=rows.length?[...groups].sort(([a],[b])=>familyOrder(b)-familyOrder(a)||a.localeCompare(b)).map(([type,models])=>{
    const bytes=models.reduce((sum,m)=>sum+(Number(m.downloaded_bytes)||0),0);
    return `<section class="disk-group"><div class="disk-group-heading"><h4>${esc(t(type))}</h4><span class="muted">${tp(models.length,'{n} model','{n} models',{n:models.length})} · ${gb(bytes)}</span></div>${models.map(m => `<div class="disk-row"><div class="disk-info"><strong>${esc(m.tier)} · ${esc(m.label)}</strong>${m.community?`<span class="chip">${t('community')}</span>`:''}<span class="muted">${gb(m.downloaded_bytes)}</span><button type="button" class="model-card-open" data-id="${esc(m.id)}" aria-label="${t('Model information')}">ⓘ</button></div><div class="disk-actions">${diskActions(m)}</div></div>`).join('')}</section>`;  // the family is the group's heading
  }).join(''):`<div class="empty">${t('Nothing downloaded yet')}</div>`;
  $('#diskTotal').textContent = t('Total: {size} in {models}',{size:gb(total),models:tp(rows.length,'{n} model','{n} models',{n:rows.length})});
  $('#diskSummary').textContent=t('{models} on disk',{models:tp(rows.length,'{n} model','{n} models',{n:rows.length})});
  $('#diskSummaryMeta').textContent=t('{size} total',{size:gb(total)});
}
// Model manager: load (text or media, or with options) and unload the models on disk
function diskActions(m) {
  const loaded=statusCache?.models?.find(x=>x.id===m.id);
  if(loaded)return `<span class="chip">${esc(t(loaded.state==='ready'?'loaded':loaded.state))}</span><button type="button" class="unload-model" data-id="${esc(m.id)}">${t('Unload')}</button>`;
  if(!m.runnable){const reason=ts(m.reason,m.reason_code,m.reason_params)||t('not runnable here');return `<span class="reason" title="${esc(reason)}">${esc(reason)}</span>`;}
  const media=m.media_mode?`<button type="button" class="load-model load-media-model" data-id="${esc(m.id)}" data-media="true" title="${esc(mediaTitle(m.media||[]))}">${t('Load with media')}</button>`:'';
  const noMediaText=ts(m.media_reason,m.media_reason_code,m.media_reason_params);
  const noMedia=!m.media_mode&&(m.media||[]).length?`<small class="muted">${esc(noMediaText||'')}</small>`:'';
  return `<button type="button" class="load-model primary" data-id="${esc(m.id)}">${t('Load')}</button>${media}${optionsButton(m.id)}${noMedia}`;
}
async function loadSettings() {
  try {
    const settings = await api('/api/settings');
    $('#modelsDir').value = settings.models_dir;
    $('#defaultModelsDirHint').textContent = t('Default: {path}',{path:settings.default_models_dir});
    setChecked($('#checkUpdates'),settings.check_updates!==false);
  } catch (e) { $('#modelsDirError').textContent = e.message; }
}
async function refreshUpdates() {
  try {
    const d=await api('/api/updates');
    const parts=[];
    if(d.newer)parts.push(`<div class="card"><button type="button" data-dismiss-update aria-label="${t('Dismiss')}">×</button><strong>${t('basal-ui {latest} is available — you have {installed}',{latest:esc(d.latest),installed:esc(d.installed)})}</strong> <a href="${esc(d.url)}" target="_blank" rel="noopener">${t('Release notes')}</a></div>`);
    const engine=d.engine||{};
    if(engine.newer)parts.push(`<div class="card"><button type="button" data-dismiss-update aria-label="${t('Dismiss')}">×</button><strong>${engine.latest_supported?t('basal engine {latest} is available — you have {installed}',{latest:esc(engine.latest),installed:esc(engine.version)}):t('basal engine {latest} is released; this basal-ui supports {supported}',{latest:esc(engine.latest),supported:esc(engine.supported)})}</strong> <a href="${esc(engine.url)}" target="_blank" rel="noopener">${t('Release notes')}</a></div>`);
    const badge=$('#engineBadge');
    badge.hidden=!engine.experimental;
    if(engine.experimental){badge.textContent=t('experimental engine {version}',{version:`${engine.version}${engine.commit?'@'+engine.commit.slice(0,7):''}`});badge.title=t('basal-ui supports engine {supported}',{supported:engine.supported});}
    if(d.new_models?.length)parts.push(`<div class="card"><button type="button" data-dismiss-update aria-label="${t('Dismiss')}">×</button><strong>${tp(d.new_models.length,'{n} new model','{n} new models',{n:d.new_models.length})} ${t('in the catalog')}</strong> <button type="button" id="showNewModels">${t('View models')}</button></div>`);
    $('#updatesBanner').innerHTML=parts.join('');
    const b=$('#showNewModels');if(b)b.onclick=()=>act(async()=>{await api('/api/updates/seen',{method:'POST'});await refreshUpdates();showView('models',true);selectModelsTab('all');});
  } catch {}
}
$('#checkUpdates').onchange=()=>act(async()=>{await api('/api/settings',{method:'PUT',body:JSON.stringify({check_updates:$('#checkUpdates').checked})});refreshUpdates();});
async function saveModelsDir(value) {
  $('#modelsDirError').textContent = '';
  try {
    await api('/api/settings', {method:'PUT', body:JSON.stringify({models_dir:value})});
    await Promise.all([loadSettings(),loadModels()]); toast(t('Models directory saved'));
  } catch (e) { $('#modelsDirError').textContent = e.message; toast(e.message,'error'); }
}
async function loadModels(refresh=false) {
  $('#modelsTable').innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  $('#suggested').innerHTML = `<div class="empty">${t('Loading suggestions…')}</div>`;
  try { renderModels(await api(`/api/models${refresh?'?refresh=1':''}`)); }
  catch (e) { $('#modelsTable').innerHTML = `<div class="error-box">${esc(e.message)}</div>`; $('#suggested').innerHTML = `<div class="error-box">${esc(e.message)}</div>`; toast(e.message,'error'); }
}
async function loadModel(id, media=false) {
  statusSequence++;
  clearSelectedMedia();
  try { await api('/api/load', {method:'POST', body:JSON.stringify({id,...(media?{media:true}:{})})}); toast(t('Model load started')); await refreshStatus(); }
  catch (e) { showStatusError(e.message); toast(e.message,'error'); }
}
function showStatusError(text) { if(text)toast(text,'error'); }
function mediaTitle(kinds) { return kinds.map(kind => kind === 'image' ? t('images') : kind === 'audio' ? t('audio') : kind).join(` ${t('and')} `); }
function clearSelectedMedia() {
  mediaSelection++;
  selectedMedia.forEach(item => URL.revokeObjectURL(item.url));
  selectedMedia = [];
  $('#mediaInput').value = '';
  renderSelectedMedia();
}
const fileSize = n => n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
function renderSelectedMedia() {
  $('#mediaItems').innerHTML = selectedMedia.map((item,index) => `<div class="media-item">${item.kind === 'image'
    ? `<button type="button" class="media-preview" data-preview="${index}" title="${t('Open preview')}"><img src="${esc(item.url)}" alt="${esc(item.file.name)}"></button>`
    : `<div class="media-audio">${t('audio')}</div>`}<div class="media-meta"><span><span class="media-name" title="${esc(item.file.name)}">${esc(item.file.name)}</span><small data-dims="${index}">${fileSize(item.file.size)}</small></span><button type="button" data-remove="${index}" aria-label="${t('Remove {name}',{name:esc(item.file.name)})}">${t('Remove')}</button></div></div>`).join('');
  $('#mediaItems').querySelectorAll('[data-remove]').forEach(button => button.addEventListener('click', () => {
    URL.revokeObjectURL(selectedMedia[Number(button.dataset.remove)].url);
    selectedMedia.splice(Number(button.dataset.remove),1);
    renderSelectedMedia(); syncRequest();
  }));
  $('#mediaItems').querySelectorAll('[data-preview]').forEach(button => {
    const item = selectedMedia[Number(button.dataset.preview)], img = button.querySelector('img');
    const showDims = () => { item.dims = `${img.naturalWidth}×${img.naturalHeight} · ${fileSize(item.file.size)}`; $(`[data-dims="${button.dataset.preview}"]`).textContent = item.dims; };
    if (img.complete && img.naturalWidth) showDims(); else img.addEventListener('load', showDims, {once:true});
    button.addEventListener('click', () => {
      $('#mediaLightboxImage').src = item.url; $('#mediaLightboxImage').alt = item.file.name;
      $('#mediaLightboxName').textContent = item.file.name; $('#mediaLightboxSize').textContent = item.dims || fileSize(item.file.size);
      $('#mediaLightbox').showModal();
    });
  });
  $('#mediaOptions').hidden = !selectedMedia.some(item => item.kind === 'image');
}
function showMediaError(message='') {
  $('#mediaError').textContent = message;
  $('#mediaError').hidden = !message;
}
async function selectMedia(files) {
  const selection = ++mediaSelection;
  showMediaError();
  if (files.length > 8) { showMediaError(t('at most 8 media items')); return; }
  const supported = new Set(loadedMedia);
  for (const file of files) {
    if (file.size > 20 * 1024 * 1024) { showMediaError(t('{name} is over 20 MiB',{name:file.name})); return; }
    const kind = file.type.startsWith('image/') ? 'image' : file.type.startsWith('audio/') ? 'audio' : null;
    if (!kind) { showMediaError(t('{name} must be an image or audio file',{name:file.name})); return; }
    if (!supported.has(kind)) { showMediaError(t('This model does not support {kind}',{kind})); return; }
  }
  const readFile = file => new Promise((resolve,reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({file,kind:file.type.startsWith('image/')?'image':'audio',data:String(reader.result).split(',')[1]});
    reader.onerror = () => reject(new Error(t('Could not read {name}',{name:file.name})));
    reader.readAsDataURL(file);
  });
  try {
    const filesData = await Promise.all(files.map(readFile));
    if (selection !== mediaSelection) return;
    selectedMedia.forEach(item => URL.revokeObjectURL(item.url));
    selectedMedia = filesData.map(item => ({...item,url:URL.createObjectURL(item.file)}));
    renderSelectedMedia(); syncRequest();
  } catch (error) { if (selection === mediaSelection) showMediaError(error.message); }
}
async function refreshStatus() {
  const sequence = ++statusSequence;
  try {
    const status = await api('/api/status');
    if (sequence === statusSequence) { renderStatus(status); if (serveData) renderServeStatus(status); }
  } catch(e) {
    if (sequence === statusSequence) { showStatusError(e.message); pollTimer=setTimeout(refreshStatus,5000); }
  }
}
async function loadExamples() {
  try {
    examples=await api('/api/examples');
    $('#examples').innerHTML=`<option value="">${t('Load an example…')}</option>`+examples.map((e,i)=>`<option value="${i}">${esc(e.file || e.id || t('Example {number}',{number:i+1}))}${e.file&&e.id?` / ${esc(e.id)}`:''}</option>`).join('');
    renderRequestPresets();
  } catch { examples=[];renderRequestPresets(); }
}
// Question builder: one card per question, a type-specific fieldset; fields the builder does not own stay on the card
// (card._extra) and are merged back, so Edit JSON → builder → JSON keeps them.
const QUESTION_TYPES=['choice','noul','score','multi','act','tool','classify','verify','compress','extract'];
const EVIDENCE_TYPES=['choice','noul','score','act','tool'], MAX_ERROR_TYPES=['act','tool','classify','verify','extract'];
const TASK_TYPES=['classify','verify','compress','extract'], TOOL_EXTRAS=['answer_directly','ask_user','none'];
const SET_KEYS=['objective','threshold','min','max','fallback','fallback_threshold','exclusive'];
let presetData={load:{},request:{}},presetTarget='load';
async function loadPresets() {try{presetData=await api('/api/presets');renderRequestPresets();renderLoadPresets();}catch{}}
function renderRequestPresets() {
  const select=$('#examples'), current=select.value;
  const saved=Object.keys(presetData.request||{});
  select.innerHTML=`<option value="">${t('Load an example…')}</option>`+examples.map((e,i)=>`<option value="${i}">${esc(e.file||e.id||t('Example {number}',{number:i+1}))}${e.file&&e.id?` / ${esc(e.id)}`:''}</option>`).join('')+`<optgroup label="${t('Saved requests')}">${saved.map(n=>`<option value="preset:${esc(n)}">${esc(n)}</option>`).join('')}</optgroup>`;
  if(current&&[...select.options].some(o=>o.value===current))select.value=current;
}
function renderLoadPresets() {
  const select=$('#loadPresetSelect'); if(!select)return;
  select.innerHTML=`<option value="">${t('Choose load preset…')}</option>`+Object.keys(presetData.load||{}).map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');
}
function promptPresetName(target) {
  presetTarget=target; $('#presetNameTitle').textContent=target==='load'?t('Save load preset'):t('Save request preset');
  $('#presetName').value=''; $('#presetNameDialog').showModal(); $('#presetName').focus();
}
$('#presetNameSave').onclick=()=>act(async()=>{
  const name=$('#presetName').value.trim(); if(!name)return;
  let value;
  try { value=presetTarget==='load'?collectOptions():$('#editJsonToggle').checked?JSON.parse($('#requestJson').value):buildRequest(); }
  catch(e) { toast(e.message,'error');$('.q-name:invalid')?.reportValidity();return; }
  presetData=await api('/api/presets',{method:'PUT',body:JSON.stringify({kind:presetTarget,name,value})});
  $('#presetNameDialog').close(); renderLoadPresets();renderRequestPresets();toast(t('Preset saved'));
});
$('#examples').addEventListener('change',e=>{
  const val=e.target.value;
  if(val.startsWith('preset:')){const name=val.slice(7),request=presetData.request?.[name];if(request)setRequest(request);return;}
  const item=examples[Number(val)];if(item?.request){const request={...item.request};delete request.id;delete request.gold;setRequest(request);}else if(item)setRequest({state:item.state,questions:item.questions});
});
async function deletePreset(kind,name) {
  presetData=await api('/api/presets',{method:'PUT',body:JSON.stringify({kind,name,value:null})});
  renderLoadPresets();renderRequestPresets();toast(t('Preset deleted'));
}
$('#exportPresets').onclick=()=>act(async()=>{
  const data=await api('/api/presets'),a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
  a.href=url;a.download='presets.json';a.click();URL.revokeObjectURL(url);
});
$('#importPresetsButton').onclick=()=>$('#importPresets').click();
$('#importPresets').onchange=()=>act(async()=>{
  const file=$('#importPresets').files[0];if(!file)return;
  const data=JSON.parse(await file.text());let count=0;
  for(const kind of ['load','request'])for(const [name,value] of Object.entries(data[kind]||{})){await api('/api/presets',{method:'PUT',body:JSON.stringify({kind,name,value})});count++;}
  await loadPresets();toast(t('Imported {count} presets',{count}));$('#importPresets').value='';
});
$('#examples').insertAdjacentHTML('afterend',`<button type="button" id="saveRequestPreset">${t('Save request…')}</button><button type="button" id="deleteRequestPreset">${t('Delete request')}</button>`);
$('#saveRequestPreset').onclick=()=>promptPresetName('request');
$('#deleteRequestPreset').onclick=()=>act(async()=>{const v=$('#examples').value;if(!v.startsWith('preset:'))return toast(t('Choose a saved request first'),'error');await deletePreset('request',v.slice(7));$('#examples').value='';});
const OWNED={choice:['criteria','levels','option_keys'],noul:['criteria','option_keys'],score:['criteria','levels','option_keys'],
  multi:['criteria','option_keys',...SET_KEYS],act:['criteria','base','costs','defer_action','option_keys'],
  tool:['tools','extra','rank','multi','description_chars',...SET_KEYS],classify:['labels','criteria','multi_label','none','threshold','min','max','objective'],
  verify:['claims','binary'],compress:['unit','threshold','keep_ratio','min_keep'],extract:['schema']};
const KEYED=/^([^\s:]{1,40}):\s*(\S.*)$/;  // "key: description"; a plain option text may contain colons too
const textOf=v=>typeof v==='string'?v:JSON.stringify(v);
const splitLines=text=>String(text||'').split('\n').map(x=>x.trim()).filter(Boolean);
function parseOptions(text, type) {
  const lines=splitLines(text);
  if(type==='noul') { const c={}; if(lines[0]) c.true=lines[0]; if(lines[1]) c.false=lines[1]; return Object.keys(c).length?{criteria:c}:{}; }
  if(type==='score') return {criteria:lines};
  const hasDescriptions=lines.length>0&&lines.every(x=>KEYED.test(x));
  const criteria={};
  lines.forEach((line,i)=>{const m=line.match(KEYED); if(hasDescriptions) criteria[m[1]]=m[2]; else criteria[String(i)]=line;});
  return {criteria,option_keys:hasDescriptions?'show':'hide'};
}
// lines -> {key: description} when every line is "key: description", else the plain lines (a list)
function parseKeyedList(text) {
  const lines=splitLines(text);
  if(lines.length&&lines.every(x=>KEYED.test(x)))return Object.fromEntries(lines.map(x=>{const m=x.match(KEYED);return [m[1],m[2]];}));
  return lines;
}
function keyedText(value, hideKeys=false) {
  if(Array.isArray(value))return value.map(textOf).join('\n');
  if(value&&typeof value==='object')return hideKeys?Object.values(value).map(textOf).join('\n'):Object.entries(value).map(([k,v])=>v==null?String(k):`${k}: ${textOf(v)}`).join('\n');
  return '';
}
const isYesNo=q=>q.base==='noul'||Boolean(q.criteria&&!Array.isArray(q.criteria)&&typeof q.criteria==='object'&&Object.keys(q.criteria).length&&Object.keys(q.criteria).every(k=>k==='true'||k==='false'));
function criteriaText(q, type) {
  const criteria=q.criteria ?? q.levels ?? {};
  if(type==='noul'||(type==='act'&&isYesNo(q)))return Array.isArray(criteria)?'':[criteria.true,criteria.false].filter(x=>x!=null).map(textOf).join('\n');
  return keyedText(criteria, q.option_keys==='hide'&&!Array.isArray(criteria));
}
function parseStructuredText(text) { const trimmed=text.trim(); if(trimmed.startsWith('{')||trimmed.startsWith('[')) { try{return JSON.parse(trimmed);}catch{}} return text; }
let questionFieldId=0;
const qField=(label,input,help='',cls='')=>{const id=`question-field-${++questionFieldId}`;return `<div class="qf${cls?` ${cls}`:''}"><label id="${id}-label" for="${id}">${t(label)}</label>${input.replace(/<(input|textarea|select)\b/,`<$1 id="${id}" aria-labelledby="${id}-label"`)}${help?`<small class="muted">${t(help)}</small>`:''}</div>`};
const qNum=(f,v,attrs='')=>`<input class="input" type="number" data-f="${f}" aria-label="${esc(t(f.replaceAll('_',' ')))}" value="${esc(v??'')}" ${attrs}>`;
const qText=(f,v,ph='')=>`<input class="input" type="text" data-f="${f}" aria-label="${esc(t(f.replaceAll('_',' ')))}" value="${esc(v??'')}" placeholder="${esc(t(ph))}">`;
const qArea=(f,v,ph='',cls='')=>`<textarea class="textarea${cls?` ${cls}`:''}" data-f="${f}" aria-label="${esc(t(f.replaceAll('_',' ')))}" placeholder="${esc(cls==='json'?ph:t(ph))}">${esc(v??'')}</textarea>`;
const qSwitch=(f,v,label,cls='')=>`<label class="qf-switch${cls?` ${cls}`:''}"><input class="switch" type="checkbox" role="switch" data-f="${f}"${v?' checked aria-checked="true"':' aria-checked="false"'}> ${t(label)}</label>`;
const qSelect=(f,options,v)=>`<select class="select" data-f="${f}" aria-label="${esc(t(f.replaceAll('_',' ')))}">${options.map(([value,label])=>`<option value="${esc(value)}"${String(value)===String(v??'')?' selected':''}>${esc(t(label))}</option>`).join('')}</select>`;
// the set options of multi, multi-label classify and multi tool; constraints: fallback and exclusive keys (multi and
// tool only: classify takes objective, threshold, min and max)
function setFields(q, constraints=true) {
  const objective=q.objective||'threshold';
  const extra=constraints?`${qField('Fallback key',qText('fallback',q.fallback,'none'))}${qField('Fallback threshold',qNum('fallback_threshold',q.fallback_threshold,'min="0" max="1" step="0.01"'))}${qField('Exclusive keys',qText('exclusive',(q.exclusive||[]).join(', '),'comma-separated'))}`:'';
  return `<div class="qf-row set-fields">${qField('Objective',qSelect('objective',[['threshold','threshold'],['exact','exact'],['iou','iou'],['f1','f1']],objective))}${qField('Threshold',qNum('threshold',q.threshold??'','min="0" max="1" step="0.01" placeholder="0.5"'),'','set-threshold')}${qField('Min',qNum('min',q.min,'min="0" step="1"'))}${qField('Max',qNum('max',q.max,'min="1" step="1"'))}${extra}</div>`;
}
function costRowsOf(costs) {
  return costs&&typeof costs==='object'&&!(Object.keys(costs).every(k=>k==='wrong'||k==='defer')&&typeof costs.wrong!=='object')
    ?Object.entries(costs).map(([action,row])=>({action,costs:{...(row||{})}})):null;
}
function actFields(card, q) {
  const simple=q.costs&&!costRowsOf(q.costs)?q.costs:{wrong:10,defer:1};
  card._costMode=costRowsOf(q.costs)?'matrix':'simple'; card._costRows=costRowsOf(q.costs)||[]; card._costOutcomes=null;
  return qSwitch('yesno',isYesNo(q),'Yes / no outcomes')+qField('Outcomes',qArea('criteria',criteriaText(q,'act'),'key: description, one outcome per line'),'Yes / no: first line describes true, second false.')
    +`<div class="qf"><span class="qf-label">${t('Costs')}</span><div class="tabs cost-mode"><button type="button" data-cost-mode="simple">${t('Simple')}</button><button type="button" data-cost-mode="matrix">${t('Matrix')}</button></div>`
    +`<div class="qf-row cost-simple">${qField('Wrong action',qNum('cost_wrong',simple.wrong,'step="any"'))}${qField('Defer to a person',qNum('cost_defer',simple.defer,'step="any" placeholder="none"'))}</div>`
    +`<div class="cost-matrix"><div class="table-wrap"><table class="cost-table"></table></div><button type="button" class="add-action">${t('Add action')}</button>${qField('Defer action',qText('defer_action',q.defer_action,'defer or human'),'The action max_error falls back to.')}</div></div>`;
}
function renderTypeFields(card, type, q) {
  let html='';
  if(type==='choice')html=qField('Options / descriptions',qArea('criteria',criteriaText(q,type),'key: description, or one option per line'),'key: description uses explicit keys; plain lines use hidden keys 0…n.');
  else if(type==='noul')html=qField('Yes / no descriptions',qArea('criteria',criteriaText(q,type),'true description\nfalse description'),'Optional: first line describes true, second describes false.');
  else if(type==='score')html=qField('Score levels',qArea('criteria',criteriaText(q,type),'one level per line'),'Ordered from lowest to highest score.');
  else if(type==='multi')html=qField('Labels',qArea('criteria',keyedText(q.criteria),'key: description, or one label per line'),'Each label is its own yes/no decision; the selected set follows the objective.')+setFields(q);
  else if(type==='act')html=actFields(card,q);
  else if(type==='tool'){
    const extra=q.extra??(q.multi?[]:TOOL_EXTRAS);
    html=qField('Tools (JSON array)',qArea('tools',q.tools?JSON.stringify(q.tools,null,2):'','[{"name": "get_weather", "description": "…", "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}}}]','json'),'OpenAI function, OpenAI Responses, MCP tools/list or Anthropic definitions.')
      +`<div class="qf"><span class="qf-label">${t('Other options')}</span><div class="controls">${TOOL_EXTRAS.map(x=>`<label><input class="switch" type="checkbox" role="switch" data-extra="${x}"${extra.includes(x)?' checked aria-checked="true"':' aria-checked="false"'}> ${t(x)}</label>`).join('')}</div></div>`
      +`<div class="qf-row">${qField('Rank',qNum('rank',q.rank,'min="1" step="1" placeholder="off"'),'also the k most probable options')}${qField('Description chars',qNum('description_chars',q.description_chars,'min="0" step="1" placeholder="300"'),'0 = no cap')}</div>`+qSwitch('multi',q.multi,'Several tools (multi)')
      +`<div class="multi-tool-only">${setFields(q)}<small class="muted">${t('The other options (answer directly, ask the user, none) are always exclusive.')}</small></div>`;
  }
  else if(type==='classify')html=qField('Labels',qArea('labels',labelsText(q.labels??q.criteria),'key: description, or one label per line'))+qSwitch('multi_label',q.multi_label,'Several labels (multi-label)')+' '+qSwitch('none',q.none,'Add a "none" label','single-only')+`<div class="multi-only">${setFields(q,false)}</div>`;
  else if(type==='verify')html=qField('Claims',qArea('claims',keyedText(q.claims),'key: claim text, or one claim per line'))+qSwitch('binary',q.binary,'Binary (supported / not supported)');
  else if(type==='compress')html=`<div class="qf-row">${qField('Unit',qSelect('unit',[['sentence','sentence'],['line','line'],['paragraph','paragraph']],q.unit||'sentence'))}${qField('Threshold',qNum('threshold',q.threshold,'min="0" max="1" step="0.05" placeholder="0.5"'))}${qField('Keep ratio',qNum('keep_ratio',q.keep_ratio,'min="0" max="1" step="0.05" placeholder="off"'),'share of the characters kept (overrides threshold)')}${qField('Min keep',qNum('min_keep',q.min_keep,'min="0" step="1" placeholder="1"'))}</div>`;
  else if(type==='extract')html=qField('Schema (JSON)',qArea('schema',q.schema?JSON.stringify(q.schema,null,2):'','{"type": "object", "properties": {"total": {"type": "number"}}}','json'),'string, number, integer, boolean, enum and arrays of enums; nested objects as dotted paths.');
  const me=q.max_error, preset=me==null||Array.isArray(me)?'':[0.01,0.05].includes(Number(me))?String(Number(me)):'custom', maxErrorId=`question-field-${++questionFieldId}`;
  html+=`<div class="qf-row common-fields">${qSwitch('evidence',q.evidence===true,'Evidence spans','evidence-only')}<div class="qf max-error-only"><label for="${maxErrorId}">max_error</label><div class="controls">${qSelect('max_error',[['','none'],['0.01','0.01'],['0.05','0.05'],['custom','custom…']],preset).replace('<select ',`<select id="${maxErrorId}" `)}${qNum('max_error_custom',preset==='custom'?me:'','min="0" max="1" step="0.001"')}</div></div></div><div class="q-error reason wide" role="alert" hidden></div>`;
  $('.q-fields',card).innerHTML=html;
  $('.q-instructions-input',card).placeholder=['tool',...TASK_TYPES].includes(type)?t('optional'):t('What should the model decide?');
  if(type==='act')renderCostMatrix(card);
  updateCardVisibility(card);
}
function actOutcomes(card) {
  if($('[data-f="yesno"]',card)?.checked)return ['true','false'];
  return Object.keys(parseOptions($('[data-f="criteria"]',card)?.value||'','choice').criteria);
}
function readCostRows(card) {
  return [...card.querySelectorAll('.cost-table tbody tr')].map(tr=>({action:$('.cost-action',tr).value,costs:Object.fromEntries([...tr.querySelectorAll('.cost-cell')].map(i=>[i.dataset.outcome,i.value]))}));
}
function renderCostMatrix(card, force=false) {
  const outcomes=actOutcomes(card), table=$('.cost-table',card);
  if(!table)return;
  if(!force&&JSON.stringify(outcomes)===JSON.stringify(card._costOutcomes))return;
  if(!force&&card._costOutcomes)card._costRows=readCostRows(card);  // forced callers set _costRows themselves
  card._costOutcomes=outcomes;
  table.innerHTML=`<thead><tr><th>${t('Action')}</th>${outcomes.map(o=>`<th>${esc(o)}</th>`).join('')}<th></th></tr></thead><tbody>${card._costRows.map((row,i)=>`<tr><td><input class="input cost-action" type="text" aria-label="${t('Action {number}',{number:i+1})}" value="${esc(row.action)}"></td>${outcomes.map(o=>`<td><input class="input cost-cell" type="number" step="any" aria-label="${t('{outcome} cost for {action}',{outcome:esc(o),action:esc(row.action)})}" data-outcome="${esc(o)}" value="${esc(row.costs[o]??'')}"></td>`).join('')}<td><button type="button" class="remove-action" data-row="${i}" aria-label="${t('Remove action {number}',{number:i+1})}">${t('Remove')}</button></td></tr>`).join('')}</tbody>`;
}
function setCostMode(card, mode) {
  if(card._costOutcomes)card._costRows=readCostRows(card);
  if(mode==='matrix'&&card._costMode==='simple'&&!card._costRows.length){  // start from the simple costs
    const outcomes=actOutcomes(card), w=$('[data-f="cost_wrong"]',card).value, d=$('[data-f="cost_defer"]',card).value;
    card._costRows=outcomes.map(y=>({action:y,costs:Object.fromEntries(outcomes.map(z=>[z,z===y?0:w]))}));
    if(d!=='')card._costRows.push({action:'defer',costs:Object.fromEntries(outcomes.map(z=>[z,d]))});
    card._costOutcomes=null;
  }
  card._costMode=mode; renderCostMatrix(card,true); updateCardVisibility(card);
}
function updateCardVisibility(card) {
  const type=$('.q-type',card).value, on=f=>Boolean($(`[data-f="${f}"]`,card)?.checked), show=(sel,visible)=>card.querySelectorAll(sel).forEach(x=>x.hidden=!visible);
  const multiTool=type==='tool'&&on('multi');
  show('.evidence-only',EVIDENCE_TYPES.includes(type)&&!multiTool);
  show('.max-error-only',MAX_ERROR_TYPES.includes(type)&&!multiTool);
  const custom=$('[data-f="max_error_custom"]',card); if(custom)custom.hidden=$('[data-f="max_error"]',card).value!=='custom';
  const objective=$('[data-f="objective"]',card); show('.set-threshold',!objective||objective.value==='threshold');
  show('.multi-only',on('multi_label')); show('.single-only',type==='classify'&&!on('multi_label'));
  show('.multi-tool-only',multiTool);
  if(type==='act'){
    show('.cost-simple',card._costMode==='simple'); show('.cost-matrix',card._costMode==='matrix');
    card.querySelectorAll('[data-cost-mode]').forEach(b=>b.classList.toggle('active',b.dataset.costMode===card._costMode));
  }
}
// every value the user can change on a card (the cost matrix too): equal to the rendered one = nothing was edited
function cardSnapshot(card) {
  return JSON.stringify([...card.querySelectorAll('input,select,textarea')].filter(x=>!x.matches('.q-name'))
    .map(x=>x.type==='checkbox'||x.type==='radio'?x.checked:x.value).concat([card._costMode??null]));
}
// the request a card was made from, to give it back exactly where the user did not edit it: the builder's own text
// forms (lines, defaults left out) cannot hold every request (a list of {name, rubric} labels, option_keys given as
// the default, non-string descriptions)
function rememberOriginal(card, type, q) {
  const value=f=>$(`[data-f="${f}"]`,card)?.value;
  card._orig={type,q:structuredClone(q),snapshot:cardSnapshot(card),
    lines:{criteria:value('criteria'),labels:value('labels'),claims:value('claims'),yesno:$('[data-f="yesno"]',card)?.checked,
           instructions:$('.q-instructions-input',card).value}};
}
const labelObjects=v=>Array.isArray(v)&&v.length>0&&v.every(x=>x&&typeof x==='object'&&!Array.isArray(x)&&'name' in x);
function labelsText(v) {  // classify labels: {key: rubric}, [keys] or [{"name", "rubric" | "description"}]
  if(!labelObjects(v))return keyedText(v);
  return v.map(x=>{const d=x.rubric??x.description;return d==null?String(x.name):`${x.name}: ${textOf(d)}`;}).join('\n');
}
function parseLabels(text, original) {
  if(!labelObjects(original))return parseKeyedList(text);
  const key=original.some(x=>'rubric' in x)||!original.some(x=>'description' in x)?'rubric':'description';
  return splitLines(text).map(line=>{const m=line.match(KEYED);return m?{name:m[1],[key]:m[2]}:{name:line};});
}
function parseQuestionCard(card, type=$('.q-type',card).value) {
  const name=$('.q-name',card).value.trim() || 'question';
  const orig=card._orig&&card._orig.type===type?card._orig:null, box=$('.q-error',card);
  if(orig&&cardSnapshot(card)===orig.snapshot){  // nothing edited: the request as it came
    card._error=null; if(box){box.textContent='';box.hidden=true;}
    return [name,structuredClone(orig.q)];
  }
  const el=f=>$(`[data-f="${f}"]`,card), str=f=>(el(f)?.value??'').trim(), num=f=>str(f)===''?undefined:Number(str(f)), on=f=>Boolean(el(f)?.checked);
  const q={type}, errors=[], set=(k,v)=>{if(v!==undefined&&v!==''&&!(Array.isArray(v)&&!v.length))q[k]=v;};
  const json=(f,check,message)=>{const text=str(f);if(!text){errors.push(t(message));return undefined;}try{const v=JSON.parse(text);if(check(v))return v;}catch{}errors.push(t(message));return undefined;};
  // a field whose text the user did not edit keeps the original's keys and values (none of them when it had none)
  const unedited=f=>orig!=null&&el(f)!=null&&el(f).value===orig.lines[f]&&(f!=='criteria'||type!=='act'||on('yesno')===orig.lines.yesno);
  const keep=(...keys)=>{keys.forEach(k=>{if(k in orig.q)q[k]=structuredClone(orig.q[k]);});};
  if(orig&&$('.q-instructions-input',card).value===orig.lines.instructions)keep('instructions');
  else{
    const instructions=parseStructuredText($('.q-instructions-input',card).value);
    if(instructions!==''||!['tool',...TASK_TYPES].includes(type))q.instructions=instructions;
  }
  const setOptions=(constraints=true)=>{
    const objective=str('objective');
    if(objective&&objective!=='threshold')q.objective=objective; else set('threshold',num('threshold'));
    set('min',num('min')); set('max',num('max'));
    if(!constraints)return;
    set('fallback',str('fallback')); set('fallback_threshold',num('fallback_threshold'));
    set('exclusive',str('exclusive').split(',').map(x=>x.trim()).filter(Boolean));
  };
  if(['choice','noul','score'].includes(type)){if(unedited('criteria'))keep('criteria','levels','option_keys');else Object.assign(q,parseOptions(str('criteria'),type));}
  else if(type==='multi'){if(unedited('criteria'))keep('criteria','option_keys');else set('criteria',parseKeyedList(str('criteria'))); setOptions();}
  else if(type==='act'){
    if(unedited('criteria'))keep('criteria','base','option_keys');
    else if(on('yesno')){const c=parseOptions(str('criteria'),'noul').criteria;if(c)q.criteria=c;else q.base='noul';}
    else Object.assign(q,parseOptions(str('criteria'),'choice'));
    if(card._costMode==='matrix'){
      const rows=readCostRows(card).filter(r=>r.action.trim());
      const missing=rows.some(r=>Object.values(r.costs).some(v=>v===''));
      if(missing)errors.push(t('every cost cell needs a number'));
      q.costs=Object.fromEntries(rows.map(r=>[r.action.trim(),Object.fromEntries(Object.entries(r.costs).filter(([,v])=>v!=='').map(([o,v])=>[o,Number(v)]))]));
      set('defer_action',str('defer_action'));
    } else {
      q.costs={wrong:num('cost_wrong')??0}; if(num('cost_defer')!==undefined)q.costs.defer=num('cost_defer');
    }
  }
  else if(type==='tool'){
    set('tools',json('tools',v=>Array.isArray(v)&&v.length>0,'tools must be a JSON array of tool definitions'));
    const multi=on('multi'), extra=TOOL_EXTRAS.filter(x=>$(`[data-extra="${x}"]`,card)?.checked);
    if(multi){q.multi=true; if(extra.length)q.extra=extra; setOptions();} else if(extra.length!==TOOL_EXTRAS.length)q.extra=extra;
    set('rank',num('rank')); if(num('description_chars')!==undefined&&num('description_chars')!==300)q.description_chars=num('description_chars');
  }
  else if(type==='classify'){
    if(unedited('labels'))keep('labels','criteria','option_keys'); else set('labels',parseLabels(str('labels'),orig?.q.labels));
    if(on('multi_label')){q.multi_label=true;setOptions(false);} else if(on('none'))q.none=true;
  }
  else if(type==='verify'){if(unedited('claims'))keep('claims');else set('claims',parseKeyedList(str('claims'))); if(on('binary'))q.binary=true;}
  else if(type==='compress'){
    if(str('unit')&&str('unit')!=='sentence')q.unit=str('unit');
    if(num('threshold')!==undefined&&num('threshold')!==0.5)q.threshold=num('threshold');
    set('keep_ratio',num('keep_ratio')); if(num('min_keep')!==undefined&&num('min_keep')!==1)q.min_keep=num('min_keep');
  }
  else if(type==='extract')set('schema',json('schema',v=>v&&typeof v==='object'&&!Array.isArray(v)&&v.properties&&typeof v.properties==='object','schema must be a JSON object with properties'));
  const multiTool=type==='tool'&&q.multi;
  if(EVIDENCE_TYPES.includes(type)&&!multiTool&&on('evidence'))q.evidence=true;
  if(MAX_ERROR_TYPES.includes(type)&&!multiTool){const me=str('max_error');if(me==='custom')set('max_error',num('max_error_custom'));else if(me)q.max_error=Number(me);}
  card._error=errors.join('\n')||null;
  if(box){box.textContent=card._error||'';box.hidden=!card._error;}
  return [name,{...card._extra,...q}];
}
function extraOf(q, type) {
  const owned=new Set(['type','instructions','evidence',...OWNED[type]]);
  if(!Array.isArray(q.max_error))owned.add('max_error');  // a list of targets (tasks) is kept as given
  return Object.fromEntries(Object.entries(q||{}).filter(([k])=>!owned.has(k)));
}
function mediaRequestFields() {
  const fields = {};
  if (selectedMedia.length) fields.media = selectedMedia.map(({kind,data}) => ({type:kind,data}));
  if (selectedMedia.some(item => item.kind === 'image')) fields.detail = $('#mediaDetail').value;
  return fields;
}
// request-level options of the playground head; in Edit JSON mode they patch the JSON
function requestOptionFields() {
  const fields={};
  if($('#factsToggle').checked)fields.facts='auto';
  if($('#langSelect').value!=='auto')fields.lang=$('#langSelect').value;
  if(!$('#thinkSelect').hidden&&$('#thinkSelect').value)fields.think=$('#thinkSelect').value;
  if(!$('#thinkTextLabel').hidden&&$('#thinkTextToggle').checked)fields.think_text=true;
  if(!$('#earlyExitSelect').hidden&&$('#earlyExitSelect').value)fields.early_exit=$('#earlyExitSelect').value;
  return fields;
}
const REQUEST_OPTION_KEYS=['facts','lang','think','think_text','early_exit'];
// think / early_exit appear for a model loaded with Thinking / with early-exit levels (--mode fast-exit)
function setPlaygroundExperimental(model) {
  const think=Boolean(model?.options?.think), levels=model?.entry?.early_exit||[], select=$('#earlyExitSelect');
  $('#thinkSelect').hidden=$('#thinkTextLabel').hidden=!think;
  if(JSON.stringify([...select.options].map(o=>o.value))!==JSON.stringify(['',...levels]))select.innerHTML=['',...levels].map(l=>`<option value="${esc(l)}">${l?t('Early exit: {level}',{level:esc(l)}):t('Early exit: server default')}</option>`).join('');
  select.hidden=!levels.length;
}
function buildRequest() {
  const questions=Object.create(null), names=new Set();
  for(const card of document.querySelectorAll('.question')) {
    const input=$('.q-name',card), name=input.value, duplicate=names.has(name), invalid=!name.trim()||duplicate;
    input.setCustomValidity(!name.trim()?t('Enter a question name.'):duplicate?t('Question names must be unique.'):'');
    if(invalid)input.setAttribute('aria-invalid','true');else input.removeAttribute('aria-invalid');
    names.add(name);
    const [,q]=parseQuestionCard(card);if(!invalid)questions[name]=q;
  }
  if([...document.querySelectorAll('.q-name')].some(input=>!input.checkValidity()))throw new Error(t('Question names must be non-blank and unique.'));
  const stateInput=$('#stateInput'), state=stateInput._requestState;
  return {state:state!==undefined&&stateInput.value===state.text?state.value:parseStructuredText(stateInput.value),questions,...requestOptionFields(),...mediaRequestFields()};
}
function syncRequest(force=false, keys=['media','detail']) {
  const textarea = $('#requestJson');
  if ($('#editJsonToggle').checked && !force) {
    let request;
    try { request = JSON.parse(textarea.value); }
    catch (e) { showMediaError(e.message); return; }
    if (!request || typeof request !== 'object' || Array.isArray(request)) { showMediaError(t('request JSON must be an object')); return; }
    keys.forEach(k=>delete request[k]);
    const fields={...requestOptionFields(),...mediaRequestFields()};
    keys.forEach(k=>{if(k in fields)request[k]=fields[k];});
    textarea.value = JSON.stringify(request, null, 2);
    showMediaError();
  } else if (force || !$('#editJsonToggle').checked) {
    try { textarea.value = JSON.stringify(buildRequest(), null, 2); }
    catch (e) { showMediaError(e.message); }
  }
}
function addQuestion(name, q={type:'choice',instructions:'',criteria:{}}) {
  if(name===undefined){const names=new Set([...document.querySelectorAll('.q-name')].map(input=>input.value));name='question';for(let i=2;names.has(name);i++)name=`question${i}`;}
  const card=document.createElement('div'); card.className='question'; const fieldId=`question-card-${++questionFieldId}`;
  const type=QUESTION_TYPES.includes(q.type)?q.type:'choice';
  card._extra=extraOf(q,type); card._type=type;
  card.innerHTML=`<div class="q-head"><div><label for="${fieldId}-name">${t('Name')}</label><input id="${fieldId}-name" class="input q-name" type="text" value="${esc(name)}"></div><div><label for="${fieldId}-type">${t('Type')}</label><select id="${fieldId}-type" class="select q-type">${QUESTION_TYPES.map(type=>`<option value="${type}">${type}</option>`).join('')}</select></div><button type="button" class="remove-question">${t('Remove')}</button></div><div class="q-instructions"><label for="${fieldId}-instructions">${t('Instructions')}</label><textarea id="${fieldId}-instructions" class="textarea q-instructions-input" placeholder="${t('What should the model decide?')}">${esc(typeof q.instructions==='string'?q.instructions:q.instructions==null?'':JSON.stringify(q.instructions))}</textarea></div><div class="q-fields"></div>`;
  $('.q-type',card).value=type;
  renderTypeFields(card,type,q); rememberOriginal(card,type,q);
  card.addEventListener('input',e=>{
    if(e.target.matches('[data-f="criteria"]')&&$('.q-type',card).value==='act')renderCostMatrix(card);
    syncRequest();
  });
  card.addEventListener('change',e=>{
    if(e.target.matches('.q-type')){
      const next=e.target.value, [,current]=parseQuestionCard(card,card._type);
      card._type=next; card._extra=Object.fromEntries(Object.entries(card._extra).filter(([k])=>!OWNED[next].includes(k)));
      renderTypeFields(card,next,{...current,type:next});
    } else {
      if(e.target.matches('[data-f="yesno"]'))renderCostMatrix(card);
      updateCardVisibility(card);
    }
    syncRequest();
  });
  card.addEventListener('click',e=>{
    const b=e.target.closest('button'); if(!b)return;
    if(b.matches('.remove-question')){card.remove();syncRequest();}
    else if(b.dataset.costMode){setCostMode(card,b.dataset.costMode);syncRequest();}
    else if(b.matches('.add-action')){card._costRows=readCostRows(card);card._costRows.push({action:'',costs:{}});renderCostMatrix(card,true);syncRequest();}
    else if(b.matches('.remove-action')){card._costRows=readCostRows(card);card._costRows.splice(Number(b.dataset.row),1);renderCostMatrix(card,true);syncRequest();}
  });
  $('#questions').append(card); syncRequest();
}
function setRequest(req) {
  mediaSelection++;
  selectedMedia.forEach(item=>URL.revokeObjectURL(item.url));selectedMedia=[];$('#mediaInput').value='';renderSelectedMedia();
  const preserveJson=()=>{
    $('#editJsonToggle').checked=true;$('#editJsonArea').hidden=false;$('#builder').hidden=true;
    $('#requestJson').value=JSON.stringify(req,null,2);
    showMediaError(t('This request contains fields or values the Playground builder cannot edit. It remains in Edit JSON mode.'));
  };
  if(!req||typeof req!=='object'||Array.isArray(req)||!Object.hasOwn(req,'state')||req.state==null||
     !Object.hasOwn(req,'questions')||!req.questions||typeof req.questions!=='object'||Array.isArray(req.questions)){preserveJson();return;}
  const known=new Set(['state','questions','facts','lang','think','think_text','early_exit','media','detail','model']);
  const media=Array.isArray(req.media)?req.media:[];
  let restored=[];
  try {
    if(Object.keys(req).some(key=>!known.has(key))||
       Object.values(req.questions).some(q=>!q||typeof q!=='object'||Array.isArray(q)||!QUESTION_TYPES.includes(q.type))||
       (Object.hasOwn(req,'media')&&!Array.isArray(req.media))||
       media.some(item=>!item||typeof item!=='object'||Array.isArray(item)||Object.keys(item).some(k=>!['type','data'].includes(k))||
         !['image','audio'].includes(item.type)||typeof item.data!=='string'||!(/^(?:[A-Za-z0-9+/]+={0,2}|data:[^,]+;base64,[A-Za-z0-9+/]+={0,2})$/).test(item.data))||
       (Object.hasOwn(req,'detail')&&(!['default','high'].includes(req.detail)||(media.length>0&&!media.some(item=>item.type==='image'))))||
       (Object.hasOwn(req,'facts')&&req.facts!=='auto')||(Object.hasOwn(req,'lang')&&!['auto','pl','en'].includes(req.lang))||
       (Object.hasOwn(req,'think')&&!['','off','force'].includes(req.think))||(Object.hasOwn(req,'think_text')&&typeof req.think_text!=='boolean')||
       (Object.hasOwn(req,'model')&&![...$('#playgroundModel').options].some(o=>o.value===req.model))||
       (Object.hasOwn(req,'early_exit')&&!([...$('#earlyExitSelect').options].some(o=>o.value===req.early_exit))))throw new Error();
    for(const item of media){
      const match=item.data.match(/^data:([^;]+);base64,(.*)$/),mime=match?.[1]|| (item.type==='image'?'image/png':'audio/wav'),data=match?.[2]||item.data;
      const bytes=Uint8Array.from(atob(data),char=>char.charCodeAt(0)),file=new File([bytes],`replay.${item.type==='image'?'png':'wav'}`,{type:mime});
      restored.push({kind:item.type,data,file,url:URL.createObjectURL(file)});
    }
  } catch {
    restored.forEach(item=>URL.revokeObjectURL(item.url));
    preserveJson();
    return;
  }
  selectedMedia=restored;renderSelectedMedia();
  $('#editJsonToggle').checked=false;$('#editJsonArea').hidden=true;$('#builder').hidden=false;showMediaError();
  if(req.model&&[...$('#playgroundModel').options].some(o=>o.value===req.model)){
    selectedModel=req.model;localStorage.setItem('basal-ui.model',selectedModel);$('#playgroundModel').value=selectedModel;
    const model=readyModels().find(m=>m.id===selectedModel);setPlaygroundMedia(model?.media||[]);setPlaygroundExperimental(model);
  }
  const stateInput=$('#stateInput');
  stateInput.value=typeof req.state==='string'?req.state:JSON.stringify(req.state??'',null,2);
  stateInput._requestState={text:stateInput.value,value:structuredClone(req.state??'')};
  setChecked($('#factsToggle'),req.facts==='auto'); $('#langSelect').value=['pl','en'].includes(req.lang)?req.lang:'auto';
  $('#thinkSelect').value=['off','force'].includes(req.think)?req.think:''; setChecked($('#thinkTextToggle'),req.think_text===true);
  if([...$('#earlyExitSelect').options].some(o=>o.value===req.early_exit))$('#earlyExitSelect').value=req.early_exit; else $('#earlyExitSelect').value='';
  $('#mediaDetail').value=req.detail||'default';
  $('#questions').innerHTML='';
  for(const [name,q] of Object.entries(req.questions||{})) addQuestion(name,q);
  syncRequest(true);
}
const PLAYGROUND_DRAFT='basal-ui.language-draft';
window.preparePlaygroundLanguageSwitch=()=>{
  const hadMedia=selectedMedia.length>0;
  if($('#editJsonToggle').checked){
    sessionStorage.setItem(PLAYGROUND_DRAFT,JSON.stringify({json:true,text:$('#requestJson').value,hadMedia}));
    return;
  }
  const cards=[...document.querySelectorAll('#questions .question')];
  const stateInput=$('#stateInput'), state=stateInput._requestState&&stateInput.value===stateInput._requestState.text
    ?stateInput._requestState.value:parseStructuredText(stateInput.value);
  const request={state,questions:{},...requestOptionFields(),detail:$('#mediaDetail').value||'default'};
  const names=cards.map(card=>$('.q-name',card).value);
  const controls=cards.map(card=>[...card.querySelectorAll('input,select,textarea')].map(el=>el.type==='checkbox'?{checked:el.checked}:{value:el.value}));
  const costModes=cards.map(card=>card._costMode);
  const costRows=cards.map(card=>card._costMode==='matrix'?readCostRows(card):null);
  cards.forEach((card,i)=>{request.questions[`__language_draft_${i}`]=parseQuestionCard(card)[1];});
  const model=selectedModel||$('#playgroundModel').value;
  if(model)request.model=model;
  sessionStorage.setItem(PLAYGROUND_DRAFT,JSON.stringify({request,hadMedia,names,controls,costModes,costRows}));
};
function restorePlaygroundLanguageDraft(){
  const saved=sessionStorage.getItem(PLAYGROUND_DRAFT);if(!saved)return;
  sessionStorage.removeItem(PLAYGROUND_DRAFT);
  try{
    const draft=JSON.parse(saved);
    if(draft.json){$('#editJsonToggle').checked=true;$('#editJsonArea').hidden=false;$('#builder').hidden=true;$('#requestJson').value=draft.text;}
    else if(draft.request){
      setRequest(draft.request);
      const cards=[...document.querySelectorAll('#questions .question')];
      cards.forEach((card,i)=>{
        if(draft.costModes?.[i]==='matrix'&&$('.q-type',card).value==='act'){
          card._costMode='matrix';
          card._costRows=draft.costRows?.[i]||[];
          card._costOutcomes=null;
          renderCostMatrix(card,true);
        }
        const controls=[...card.querySelectorAll('input,select,textarea')];
        (draft.controls?.[i]||[]).forEach((value,j)=>{
          const el=controls[j];if(!el)return;
          if('checked' in value)el.checked=value.checked;else el.value=value.value;
        });
        $('.q-name',card).value=draft.names?.[i]??$('.q-name',card).value;
        updateCardVisibility(card);
      });
      syncRequest(true);
    }
    if(draft.hadMedia)showMediaError(t('Media must be attached again after changing language.'));
  }catch{}
}
function resultHeadline(name, answer, requestQuestion) {
  const type=answer.type || requestQuestion?.type || 'unknown';
  if(type==='choice') { const key=answer.choice ?? answer.answer ?? ''; const criteria=requestQuestion?.criteria; const desc=criteria && !Array.isArray(criteria) ? criteria[key] : null; const text=withoutKey(desc==null?'':typeof desc==='string'?desc:JSON.stringify(desc),key); return `${key}${text?` · ${text}`:''}`; }
  if(type==='noul') { const p=Number(answer.noul ?? answer.probabilities?.true); return Number.isFinite(p)?t('P(yes) {value}',{value:`${(p*100).toFixed(1)}%`}):t('P(yes) —'); }
  if(type==='score') { const legend=answer.legend; const score=Number(answer.score ?? answer.expected_score); const top=legend&&Number.isFinite(score)?legend[String(Math.round(score))]:null; return `${t('Expected level {value}',{value:Number.isFinite(score)?score.toFixed(2):'—'})}${top?` · ${t('nearest: {value}',{value:top})}`:''}`; }
  return null;
}
// decision safety: deferral, automation against the served model's certified thresholds, calibration validity
const modelEntry=id=>(statusCache?.models||[]).find(m=>m.id===id)?.entry||null;
const uncalibrated=entry=>Boolean(entry?.temperature_per_prim)&&!Object.keys(entry.temperature_per_prim).length;
function decisionBase(answer,q) {
  const type=answer.type||q.type;
  if(type==='tool')return 'choice';  // a single tool decision is deferred against the choice threshold
  if(type!=='act')return type;
  if(q.base)return q.base;
  const keys=q.criteria&&!Array.isArray(q.criteria)?Object.keys(q.criteria):[];
  return keys.length&&keys.every(k=>k==='true'||k==='false')?'noul':'choice';
}
// why the model's certified thresholds do not hold for this answer (null: they do). The thresholds are certified on
// text decisions with the served option orders; a media answer ("text"), one fitted on other option orders or an act
// question shape the calibration was not validated for ("unvalidated") and uncalibrated probabilities get no claim.
function certificateGap(answer, entry) {
  if(entry?.calibration_warning)return `calibration warning: ${entry.calibration_warning}`;
  if(answer.calibration==='text')return t('the thresholds are certified on text decisions, not on media');
  if(answer.calibration==='unvalidated')return t(answer.type==='act'?'the calibration is not validated for this question shape':'the calibration was fitted on other option orders');
  if(answer.calibration!=null&&answer.calibration!=='validated')return `calibration: ${answer.calibration}`;
  if(uncalibrated(entry))return t('the probabilities are not calibrated');
  return null;
}
function automationBadge(answer,q,entry) {
  const type=decisionBase(answer,q);
  const gap=certificateGap(answer,entry);
  if(answer.deferred===true||typeof answer.max_error==='string'||gap)return `<span class="badge fit-tight" title="${esc(gap||t('this answer was deferred or its requested max_error is uncertified'))}">${t('Not certified for this answer — review')}</span>`;
  if(!entry||!['choice','noul','score'].includes(type)||answer.confidence==null)return '';
  const cert=entry.certified||{}, t1=cert['0.01']?.[type], t5=cert['0.05']?.[type];
  if(t1==null&&t5==null)return `<span class="muted">${t('No certificate for this type')}</span>`;
  const c=Number(answer.confidence), shown=t=>t==null?'—':Number(t).toFixed(4);
  const title=t('certified {type} thresholds: 1% error ≥ {one}, 5% error ≥ {five}',{type,one:shown(t1),five:shown(t5)});
  const [cls,text]=t1!=null&&c>=t1?['fit-fits',t('Automatable at 1% error')]:t5!=null&&c>=t5?['fit-tight',t('Automatable at 5% error')]:['fit-too_large',t('Below the certified thresholds — review')];
  return `<span class="badge ${cls}" title="${esc(title)}">${text}</span>`;
}
function calibrationChip(answer,entry) {
  const c=answer.calibration;
  if(c==='validated')return `<span class="badge fit-fits">${t('calibrated')}</span>`;
  if(c==='unvalidated')return `<span class="badge fit-tight">${t(answer.type==='act'?'calibration not validated for this question shape':'unvalidated — fitted on other option orders')}</span>`;
  if(c==='text')return `<span class="badge fit-tight">${t('fitted on text (media request)')}</span>`;
  if(c==='per decision')return `<span class="badge fit-unknown">${t('per decision')}</span>`;
  if(c==null&&uncalibrated(entry))return `<span class="badge fit-tight">${t('uncalibrated probabilities')}</span>`;
  return '';
}
// a task answer's `automated`: {max_error: true (use as is) | false (review) | null (no certificate for the task)}
function automatedChips(automated) {
  if(!automated||typeof automated!=='object')return '';
  return Object.entries(automated).map(([risk,v])=>{
    const at=`${(Number(risk)*100).toFixed(0)}%`;
    return v===true?`<span class="badge fit-fits" title="max_error ${esc(risk)}">${at}: ${t('automated')}</span>`:v===false?`<span class="badge fit-tight" title="max_error ${esc(risk)}">${at}: ${t('review')}</span>`:`<span class="badge fit-unknown" title="max_error ${esc(risk)}">${at}: ${t('no certificate')}</span>`;
  }).join(' ');
}
function safetyStrip(answer,q,entry) {
  const parts=[];
  if(answer.deferred===true)parts.push(`<span class="badge fit-too_large">${t('Deferred to a person')}</span>${answer.refused!=null?` <span class="muted"><code>refused</code>: ${esc(answer.refused)}</span>`:''}`);
  parts.push(automationBadge(answer,q,entry),calibrationChip(answer,entry),automatedChips(answer.automated));
  const html=parts.filter(Boolean).join(' ');
  const reason=typeof answer.max_error==='string'?`<span class="reason">${esc(answer.max_error)}</span>`:'';
  return html||reason?`<div class="chips safety">${html}</div>${reason}`:'';
}
function resultBanners(entry,u) {
  let html=entry?.calibration_warning?`<div class="error-box">${esc(entry.calibration_warning)}</div>`:'';
  const truncated=u?.truncated;
  if(truncated)html+=`<div class="error-box">${t('State truncated to the context: dropped {dropped} of {total} tokens (kept {head} from the start, {tail} from the end)',{dropped:esc(truncated.dropped_tokens),total:esc(truncated.state_tokens),head:esc(truncated.head_tokens),tail:esc(truncated.tail_tokens)})}</div>`;
  return html;
}
const clamp01=v=>Math.max(0,Math.min(1,Number(v)||0));
// an option's description without what only repeats its key: "88412": "88412" → none, "4": "[4] Payment status" →
// "Payment status" (marked elements carry their badge number for the model)
const withoutKey=(text,k)=>{const tag=`[${k}]`;return text===String(k)||text===tag?'':text.startsWith(`${tag} `)?text.slice(tag.length+1):text;};
function labelOf(q,k) {
  const c=q.labels??q.criteria;
  return c&&typeof c==='object'&&!Array.isArray(c)&&c[k]!=null?withoutKey(textOf(c[k]),k)||null:null;
}
// probability bars, most probable first; `marked` = the keys drawn as chosen (default: the most probable)
function probBars(probs, q={}, marked=null) {
  const entries=Object.entries(probs||{}).sort((a,b)=>Number(b[1])-Number(a[1]));
  const chosen=marked??new Set(entries.length?[entries[0][0]]:[]);
  return entries.map(([k,v])=>{const p=clamp01(v), d=labelOf(q,k);return `<div class="prob ${chosen.has(k)?'winner':''}"><span title="${esc(d?`${k} · ${d}`:k)}">${esc(k)}${d?` · ${esc(d)}`:''}</span><div class="bar"><span style="width:${p*100}%"></span></div><strong>${(p*100).toFixed(1)}%</strong></div>`;}).join('');
}
const metaLine=text=>`<div class="answer-meta">${text}</div>`;
const confidenceLine=answer=>metaLine(`${t('Confidence')}: ${answer.confidence==null?'—':pct(answer.confidence)}`);
const chipList=(items,empty)=>items.length?`<div class="chips">${items.map(x=>`<span class="chip">${esc(x)}</span>`).join('')}</div>`:`<div class="muted">${esc(t(empty))}</div>`;
const headlineDiv=text=>`<div class="headline">${text}</div>`;
const RESULT_VIEWS={
  multi(a,q){
    const sel=a.selected||[], objective=a.objective&&a.objective!=='threshold';
    const constraints=Object.entries(a.constraints_applied||{}).map(([k,v])=>`<span class="chip muted">${esc(k)}: ${esc(textOf(v))}</span>`).join('');
    return chipList(sel.map(k=>labelOf(q,k)?`${k} · ${labelOf(q,k)}`:k),'none selected')+metaLine(`${objective?`${t('expected {objective} score {score}',{objective:esc(a.objective),score:num(a.expected_score,3)})} · `:''}${t('set confidence {confidence}',{confidence:pct(a.set_confidence)})}${a.threshold!=null?` · ${t('threshold {value}',{value:esc(a.threshold)})}`:''}`)+(constraints?`<div class="chips">${constraints}</div>`:'')+probBars(a.probabilities,q,new Set(sel));
  },
  act(a,q){
    const costs=Object.entries(a.expected_costs||{}).sort((x,y)=>x[1]-y[1]);
    const table=costs.length?`<table class="mini-table"><thead><tr><th>${t('Action')}</th><th>${t('Expected cost')}</th></tr></thead><tbody>${costs.map(([k,v])=>`<tr${k===a.action?' class="chosen"':''}><td>${esc(k)}</td><td>${num(v,3)}</td></tr>`).join('')}</tbody></table>`:'';
    return headlineDiv(`${esc(a.action)}${a.deferred?` <span class="badge fit-too_large">${t('deferred')}</span>`:''}`)+confidenceLine(a)+table+metaLine(t('Most probable outcome: {answer}',{answer:esc(a.answer)}))+probBars(a.probabilities,q,new Set([a.answer]));
  },
  tool(a){
    const head=a.multi?chipList(a.tools||[],'no tool'):headlineDiv(esc(a.tool??t('no tool')));
    const ranking=a.ranking?`<ol class="ranking">${a.ranking.map(r=>`<li><code>${esc(r.tool)}</code> · ${pct(r.probability)}</li>`).join('')}</ol>`:'';
    return head+`<div class="chips"><span class="chip">${t('call')}: ${a.call?t('true'):t('false')}</span></div>`+metaLine(a.multi?t('set confidence {confidence}',{confidence:pct(a.set_confidence)}):`${t('Confidence')}: ${pct(a.confidence)}`)+ranking+probBars(a.probabilities,{},new Set(a.multi?a.tools||[]:[a.tool]));
  },
  classify(a,q){
    const multi=Array.isArray(a.labels);
    const head=multi?chipList(a.labels,'no label'):headlineDiv(`${esc(a.label)}${labelOf(q,a.label)?` · ${esc(labelOf(q,a.label))}`:''}`);
    return head+metaLine(multi?t('set confidence {confidence}',{confidence:pct(a.set_confidence)}):`${t('Confidence')}: ${pct(a.confidence)}`)+probBars(a.scores,q,new Set(multi?a.labels:[a.label]));
  },
  verify(a,q){
    const claims=q.claims, text=k=>Array.isArray(claims)?claims[Number(k)]:claims?.[k];
    const verdict=v=>`<span class="badge ${v==='supported'?'fit-fits':v==='contradicted'?'fit-too_large':'fit-tight'}">${esc(t(v))}</span>`;
    const rows=Object.entries(a.claims||{}).map(([k,c])=>`<tr><td><code>${esc(k)}</code> ${esc(textOf(text(k)??''))}</td><td>${verdict(c.verdict)}</td><td>${pct(c.confidence)}</td><td>${automatedChips(c.automated)}</td></tr>`).join('');
    return `<div class="table-wrap"><table class="mini-table"><thead><tr><th>${t('Claim')}</th><th>${t('Verdict')}</th><th>${t('Confidence')}</th><th>${t('Automated')}</th></tr></thead><tbody>${rows}</tbody></table></div>`+metaLine(t('All supported: {answer}',{answer:a.all_supported?t('yes'):t('no')})+((a.not_supported||[]).length?` · ${t('not supported: {items}',{items:esc(a.not_supported.join(', '))})}`:''));
  },
  compress(a){
    return `<pre class="compressed">${esc(a.compressed)}</pre>`+metaLine(t('kept {kept} of {total} {unit}s · {compressed}/{original} chars ({ratio})',{kept:(a.kept||[]).length,total:(a.units||[]).length,unit:esc(a.unit),compressed:esc(a.compressed_chars),original:esc(a.original_chars),ratio:esc(a.ratio)}));
  },
  extract(a){
    const rows=Object.entries(a.fields||{}).map(([path,f])=>`<tr><td><code>${esc(path)}</code></td><td>${esc(f.value==null?'—':textOf(f.value))}${f.error?`<span class="reason">${esc(f.error)}</span>`:''}</td><td>${pct(f.confidence)}</td><td>${esc(f.source)}</td><td>${automatedChips(f.automated)}</td></tr>`).join('');
    return `<div class="table-wrap"><table class="mini-table"><thead><tr><th>${t('Field')}</th><th>${t('Value')}</th><th>${t('Confidence')}</th><th>${t('Source')}</th><th>${t('Automated')}</th></tr></thead><tbody>${rows}</tbody></table></div><details><summary>${t('data')}</summary><pre>${esc(JSON.stringify(a.data,null,2))}</pre></details>`;
  },
};
function decisionView(name, a, q) {
  const headline=resultHeadline(name,a,q);
  return (headline?headlineDiv(esc(headline)):'')+confidenceLine(a)+probBars(a.probabilities,q);
}
// the request's state with spans marked: evidence of decisions, kept / dropped compress units, extracted fields
function sourceSpans(answers, request) {
  const spans=[];
  for(const [name,a] of Object.entries(answers||{})) {
    if(!a||typeof a!=='object')continue;
    (Array.isArray(a.evidence)?a.evidence:[]).forEach(e=>spans.push({start:e.start,end:e.end,kind:'evidence',label:name,probability:e.probability}));
    if(a.type==='compress'){const kept=new Set(a.kept||[]);(a.units||[]).forEach((u,i)=>spans.push({...u,kind:kept.has(i)?'kept':'dropped',label:`${name} · ${a.unit} ${i}`}));}
    if(a.type==='extract')Object.entries(a.fields||{}).forEach(([path,f])=>{if(Number.isInteger(f.start)&&Number.isInteger(f.end))spans.push({start:f.start,end:f.end,kind:'field',label:`${name} · ${path}`,probability:f.confidence});});
  }
  return spans.filter(s=>Number.isInteger(s.start)&&Number.isInteger(s.end)&&s.end>s.start);
}
function renderSource(state, spans) {
  const accepted=[];
  for(const s of [...spans].sort((a,b)=>(Number(b.probability)||0)-(Number(a.probability)||0)))  // overlaps: the more probable span
    if(!accepted.some(x=>s.start<x.end&&x.start<s.end))accepted.push(s);
  accepted.sort((a,b)=>a.start-b.start);
  let html='', at=0;
  for(const s of accepted) {
    const title=`${s.label}${s.probability==null?'':` · ${pct(s.probability)}`}`, text=esc(state.slice(s.start,s.end));
    html+=esc(state.slice(at,s.start))+`<mark class="${s.kind}" title="${esc(title)}">${s.kind==='dropped'?`<s>${text}</s>`:text}</mark>`; at=s.end;
  }
  html+=esc(state.slice(at));
  const kinds=[...new Set(accepted.map(s=>s.kind))];
  return `<div class="source-view"><div class="section-head"><strong>${t('Source')}</strong><span class="chips">${kinds.map(k=>`<mark class="${k}">${esc(t(k))}</mark>`).join(' ')}</span></div><pre class="source">${html}</pre></div>`;
}
// thinking (basal/think.py): readout 2 after the reasoning, with the answer it replaced
function thinkBlock(a) {
  const think=a.think, used=a.meta?.think;
  if(think?.skipped)return `<div class="chips"><span class="chip muted">${t('thinking skipped ({reason})',{reason:esc(think.skipped)})}</span></div>`;
  if(!think||think.tokens==null)return used?`<div class="chips"><span class="chip muted">${t('no reasoning')}</span></div>`:'';
  return `<div class="think-block"><span class="chip">${t('reasoned')}</span> ${esc(think.tokens)} ${t('tokens')} · ${num(think.ms,0)} ms · ${t('calibration')} ${esc(t(think.calibration??'—'))}; ${t('without thinking')}: ${pct(think.no_think?.confidence)}${think.text?`<details><summary>${t('Reasoning')}</summary><pre>${esc(think.text)}</pre></details>`:''}</div>`;
}
function renderResults(data, request) {
  const answers=data?.answers || data || {}, entry=modelEntry(request.model); let html='';
  for(const [name,answer] of Object.entries(answers)) {
    const q=request.questions?.[name]||{};
    if(!answer||typeof answer!=='object'){html+=`<article class="answer"><h3>${esc(name)}</h3><pre>${esc(JSON.stringify(answer,null,2))}</pre></article>`;continue;}
    const type=answer.type||q.type||'unknown', view=RESULT_VIEWS[type];
    const body=view?view(answer,q):answer.probabilities&&typeof answer.probabilities==='object'?decisionView(name,answer,q):`<pre>${esc(JSON.stringify(answer,null,2))}</pre>`;
    html+=`<article class="answer"><h3><span>${esc(name)}</span><span class="chip">${esc(type)}</span>${answer.experimental?`<span class="badge experimental">${t('experimental')}</span>`:''}</h3>${safetyStrip(answer,q,entry)}${thinkBlock(answer)}${body}</article>`;
  }
  const spans=typeof request.state==='string'?sourceSpans(answers,request):[];
  const u=data?.usage, mediaUsage=u?.media;
  const mediaSummary=mediaUsage?` · ${t('Media')}: ${mediaUsage.images??0} ${t('images')}, ${mediaUsage.audio??0} ${t('audio')}, ${mediaUsage.media_tokens??0} ${t('tokens')}`:'';
  const thinkUsage=u?.think_questions!=null?` · ${t('Thought on')}: ${u.think_questions} · ${t('Output tokens')}: ${u.output_tokens??0}`:'';
  const extraUsage=[['branches',u?.branches],['second_order_branches',u?.second_order_branches],['lane',u?.lane]].filter(([,v])=>v!=null).map(([k,v])=>`${k}: ${esc(v)}`).join(' · ');
  const usage=u?`<div class="answer-meta">${t('Latency')}: ${u.latency_ms??'—'} ms · ${t('Input tokens')}: ${u.input_tokens??'—'} · ${t('Questions')}: ${u.questions??'—'}${extraUsage?` · ${extraUsage}`:''}${mediaSummary}${thinkUsage}</div>`:'';
  const meter=u&&entry?.context?`<div class="answer-meta">${t('Longest prompt')} ${esc(u.longest_prompt_tokens??'—')} / ${esc(entry.context.limit??'—')} ${t('tokens')} · ${t('policy')} ${esc(entry.context.overflow)}</div>`:'';
  $('#results').innerHTML=`<div class="section-head"><strong>${esc(request.model||'')}</strong>${html&&!request.media?`<button type="button" id="inspectDecision">${t('Inspect this decision')}</button>`:''}</div>`+resultBanners(entry,u)+(html ? usage+meter+html+(spans.length?renderSource(request.state,spans):'') : `<div class="empty">${t('No answers returned.')}</div>`);
  const inspectButton=$('#inspectDecision'); if(inspectButton)inspectButton.onclick=()=>inspectDecision(request);
  $('#rawResponse').textContent=JSON.stringify(data,null,2); $('#rawDetails').hidden=false;
}
function showDecideError(message) { $('#decideError').textContent=message; $('#decideError').hidden=false; }
async function sendDecision() {
  $('#decideError').hidden=true; $('#results').innerHTML='';
  let request;const jsonMode=$('#editJsonToggle').checked;
  try { request=jsonMode?JSON.parse($('#requestJson').value):buildRequest(); }
  catch(e) { showDecideError(jsonMode?t('Invalid JSON: {message}',{message:e.message}):e.message);if(!jsonMode)$('.q-name:invalid')?.reportValidity();return; }
  if(!request||typeof request!=='object'||Array.isArray(request)) { showDecideError(t('The request must be a JSON object.')); return; }
  if(!jsonMode){
    const invalid=[...document.querySelectorAll('.question')].filter(card=>card._error);
    if(invalid.length){showDecideError(invalid.map(card=>`${$('.q-name',card).value||t('question')}: ${card._error}`).join('\n'));return;}
  }
  request.model=selectedModel;
  if($('#editJsonToggle').checked)$('#requestJson').value=JSON.stringify(request,null,2);
  $('#sendButton').disabled=true;
  try { const response=await api('/api/decide',{method:'POST',body:JSON.stringify(request)}); renderResults(response,request); }
  catch(e) { showDecideError(e.message); toast(e.message,'error'); }
  finally { $('#sendButton').disabled=false; }
}
$('#refreshModels').addEventListener('click',()=>loadModels(true));
$('#downloadedToggle').addEventListener('change',rerenderFilters);
$('#saveModelsDir').addEventListener('click',()=>saveModelsDir($('#modelsDir').value));
$('#defaultModelsDir').addEventListener('click',()=>saveModelsDir(null));
let catalogData=null;
const originalRenderModels=renderModels;
renderModels=function(data){catalogData=data; originalRenderModels(data);};
function rerenderFilters(){if(catalogData) originalRenderModels(catalogData);}
$('#olderToggle').addEventListener('change',rerenderFilters);
$('#notRunnableToggle').addEventListener('change',rerenderFilters);
$('#addQuestion').addEventListener('click',()=>addQuestion());
$('#stateInput').addEventListener('input',()=>{$('#stateInput')._requestState=undefined;syncRequest();});
$('#editJsonToggle').addEventListener('change',()=>{
  if($('#editJsonToggle').checked){syncRequest(true);}
  else{
    let request;
    try{request=JSON.parse($('#requestJson').value);}
    catch(e){showMediaError(t('Invalid request JSON: {message}',{message:e.message}));$('#editJsonToggle').checked=true;return;}
    if(!request||typeof request!=='object'||Array.isArray(request)){showMediaError(t('request JSON must be an object'));$('#editJsonToggle').checked=true;return;}
    showMediaError();setRequest(request);
  }
  $('#editJsonArea').hidden=!$('#editJsonToggle').checked;$('#builder').hidden=$('#editJsonToggle').checked;
});
$('#mediaInput').addEventListener('change',event=>selectMedia(Array.from(event.target.files||[])));
$('#mediaDetail').addEventListener('change',()=>syncRequest());
$('#factsToggle').addEventListener('change',()=>syncRequest(false,REQUEST_OPTION_KEYS));
$('#langSelect').addEventListener('change',()=>syncRequest(false,REQUEST_OPTION_KEYS));
['#thinkSelect','#thinkTextToggle','#earlyExitSelect'].forEach(s=>$(s).addEventListener('change',()=>syncRequest(false,REQUEST_OPTION_KEYS)));
$('#mediaLightboxClose').addEventListener('click',()=>$('#mediaLightbox').close());
$('#mediaLightbox').addEventListener('click',e=>{if(e.target===$('#mediaLightbox'))$('#mediaLightbox').close();});
window.addEventListener('beforeunload',()=>selectedMedia.forEach(item=>URL.revokeObjectURL(item.url)));
addQuestion();
$('#sendButton').addEventListener('click',sendDecision);
let serveData=null, serveHost='127.0.0.1', statusCache=null, connectTab='curl';
function connectionEndpoint() {
  const settings=serveData.settings, serving=statusCache?.serving;
  if (serving?.url) return serving.url;
  const host=settings.host==='0.0.0.0'?(serveData.lan_ip||location.hostname):'127.0.0.1';
  return `http://${host}:${settings.port}`;
}
function renderConnect() {
  const settings=serveData.settings, serving=statusCache?.serving;
  const endpoint=connectionEndpoint(), name=selectedModel||readyModels()[0]?.id||'model-id';
  const requireKey=serving?serving.require_key:settings.require_key, mcp=serving?serving.mcp:settings.mcp;
  const key=requireKey?'<your key>':null, auth=key?` -H 'Authorization: Bearer ${key}'`:'';
  const keyArg=key?`, api_key="${key}"`:'';
  const mcpHttp=mcp
    ?JSON.stringify({mcpServers:{basal:{url:`${endpoint}/mcp`,...(key?{headers:{Authorization:`Bearer ${key}`}}:{})}}},null,2)+'\n\n'
    :'';
  const snippets={
    curl:`curl -X POST ${endpoint}/v1/systemone -H 'Content-Type: application/json'${auth} -d '{"model":"${name}","state":"...","questions":{"route":{"type":"choice","instructions":"Choose a route","criteria":{"sales":"sales","support":"support"}}}}'

curl -X POST ${endpoint}/v1/decisions -H 'Content-Type: application/json'${auth} -d '{"model":"${name}","input":"...","questions":[{"type":"choice","name":"route","instructions":"Choose a route","choices":[{"value":"sales"},{"value":"support"}]}]}'`,
    python:`import httpx

response = httpx.post("${endpoint}/v1/systemone", json={
    "model": "${name}",
    "state": "...",
    "questions": {"route": {"type": "choice", "instructions": "Choose a route",
                            "criteria": {"sales": "sales", "support": "support"}}},
}${key?`, headers={"Authorization": "Bearer ${key}"}`:''})
print(response.json())`,
    typesafe:`export TYPESAFE_BASE_URL=${endpoint}
export TYPESAFE_API_KEY=${key||'local'}
export TYPESAFE_DEFAULT_MODEL=${name}
export NO_PROXY=localhost,127.0.0.1`,
    openai:`from openai import OpenAI

client = OpenAI(base_url="${endpoint}/v1", api_key="${key||'local'}")
decision = client.decisions.create(
    model="${name}",
    input="...",
    questions=[{"type": "choice", "instructions": "Choose a route",
                "choices": [{"value": "sales"}, {"value": "support"}]}],
)`,
    litellm:`model_list:
  - model_name: ${name}
    litellm_params:
      model: hosted_vllm/${name}
      api_base: ${endpoint}
      api_key: ${key||'local'}`,
    mcp:mcpHttp+`# stdio bridge
basal-mcp --url ${endpoint} --model ${name}${key?` --api-key ${key}`:''}

The MCP server also provides list_models().`,
  };
  $('#connectCurl').textContent=snippets.curl;
  $('#connectPython').textContent=snippets.python;
  $('#connectTypesafe').textContent=snippets.typesafe;
  $('#connectOpenai').textContent=snippets.openai;
  $('#connectLitellm').textContent=snippets.litellm;
  $('#connectMcp').textContent=snippets.mcp;
  $('#mcpConnectHint').hidden=Boolean(mcp);
  $('#runningServerNote').hidden=!statusCache?.serving?.restart_required;
  $('#connectTabs').querySelectorAll('[data-connect]').forEach(tab=>{
    const active=tab.dataset.connect===connectTab;
    tab.classList.toggle('active',active); tab.setAttribute('aria-selected',String(active));
  });
  document.querySelectorAll('[data-connect-panel]').forEach(panel=>{panel.hidden=panel.dataset.connectPanel!==connectTab;});
}

// the endpoint line of the API server card: where the API answers now, and whether saved settings await a restart
function renderServeStatus(status) {
  const serving=status?.serving;
  const restart=serving?.restart_required?`<span class="badge fit-tight">${t('Restart to apply')}</span>`:'';
  const text=$('#serveStatus .serve-status-text');
  text.innerHTML=serving
    ?`${serving.url?`<span class="mono">${esc(serving.url)}</span>`:`<span>${t('Not running')}</span>`} <span class="chip">${tp(serving.models?.length??0,'{n} model','{n} models')}</span>${serving.require_key?` <span class="chip">${t('key required')}</span>`:''}${serving.mcp?` <span class="chip">${t('mcp')}</span>`:''}${serving.error?` <span class="reason">${esc(serving.error)}</span>`:''} ${restart}`
    :`<span>${t('Only this app can reach the models')}</span>`;
  $('#restartServe').disabled=!(serveData?.settings?.enabled||serving?.running);
  $('#apiSummary').textContent=serving?.running?t('Serving'):serving?.error?t('Error'):t('Off');
  $('#apiSummaryMeta').textContent=`${serving?.url||serving?.error||t('API server is not serving')} · ${tp(serveData?.keys?.length ?? 0,'{n} API keys','{n} API keys')}`;
  renderConnect();
}
async function loadServe(){
  try{
    serveData=await api('/api/serve');
    const s=serveData.settings;
    setChecked($('#serveEnabled'),s.enabled); $('#servePort').value=s.port; setChecked($('#serveRequireKey'),s.require_key);
    $('#serveMaxLen').value=s.max_len; $('#serveCors').value=s.cors_origins.join('\n'); $('#serveLog').value=s.log_decisions;
    setChecked($('#serveMcp'),s.mcp); $('#serveMcp').disabled=!serveData.mcp_available; $('#mcpHint').textContent=serveData.mcp_hint||'';
    setChecked($('#autoLoad'),serveData.auto_load); serveHost=s.host;
    $('#serveLoopback').classList.toggle('active',serveHost==='127.0.0.1');
    $('#serveLan').classList.toggle('active',serveHost==='0.0.0.0');
    $('#serveWarning').hidden=s.host!=='0.0.0.0'||s.require_key;
    renderServeStatus(statusCache);
    $('#serveKeys').innerHTML=serveData.keys.map(k=>`<div class="disk-row"><span class="disk-info">${esc(k.name)} · ${esc(k.prefix)} · ${t('created')} ${esc(new Date(k.created*1000).toLocaleString())} · ${t('last used')} ${k.last_used?esc(new Date(k.last_used*1000).toLocaleString()):t('never')}</span><button type="button" data-revoke="${esc(k.name)}">${t('Revoke')}</button></div>`).join('')||`<p class="muted">${t('No API keys created.')}</p>`;
    $('#serveKeys').querySelectorAll('[data-revoke]').forEach(button=>button.addEventListener('click',async()=>{
      if(await confirmDialog({title:t('Revoke API key?'),description:t('{key} will no longer be able to access this server.',{key:button.dataset.revoke}),confirmLabel:t('Revoke'),destructive:true})){
        try{await api(`/api/serve/keys/${encodeURIComponent(button.dataset.revoke)}`,{method:'DELETE'});toast(t('API key revoked'));await loadServe();}
        catch(error){showServeError(error.message);toast(error.message,'error');}
      }
    }));
    $('#serveError').hidden=true;
    renderConnect();
  }catch(error){showServeError(error.message);}
}
function showServeError(message) { $('#serveError').textContent=message; $('#serveError').hidden=!message; }
$('#serveLoopback').onclick=()=>{serveHost='127.0.0.1';$('#serveWarning').hidden=true;$('#serveLoopback').classList.add('active');$('#serveLan').classList.remove('active');};
$('#serveLan').onclick=()=>{serveHost='0.0.0.0';$('#serveWarning').hidden=$('#serveRequireKey').checked;$('#serveLan').classList.add('active');$('#serveLoopback').classList.remove('active');};
$('#serveRequireKey').addEventListener('change',()=>{$('#serveWarning').hidden=serveHost!=='0.0.0.0'||$('#serveRequireKey').checked;if(serveData)renderConnect();});
$('#connectTabs').querySelectorAll('[data-connect]').forEach(button=>button.addEventListener('click',()=>{connectTab=button.dataset.connect;renderConnect();}));
$('#saveServe').onclick=async()=>{
  try{
    await api('/api/serve',{method:'PUT',body:JSON.stringify({
      enabled:$('#serveEnabled').checked,host:serveHost,port:Number($('#servePort').value),
      require_key:$('#serveRequireKey').checked,max_len:Number($('#serveMaxLen').value),
      cors_origins:$('#serveCors').value.split('\n').map(value=>value.trim()).filter(Boolean),
      log_decisions:$('#serveLog').value,mcp:$('#serveMcp').checked,auto_load:$('#autoLoad').checked
    })});
    showServeError(''); await loadServe(); await refreshStatus();
    toast(statusCache?.serving?.restart_required?t('Settings saved — restart the server to apply'):t('Settings saved'));
  }catch(error){showServeError(error.message);toast(error.message,'error');}
};
$('#createKey').onclick=async()=>{
  try{
    const key=await api('/api/serve/keys',{method:'POST',body:JSON.stringify({name:$('#newKeyName').value})});
    $('#newKeySecret').innerHTML=`<code>${esc(key.key)}</code><p>${t('Copy it now: it is not shown again.')}</p><button type="button" id="copyNewKey">${t('Copy')}</button>`;
    $('#newKeySecret').hidden=false; $('#copyNewKey').onclick=()=>copyText(key.key,t('API key copied'));
    $('#newKeyName').value=''; await loadServe(); toast(t('API key created'));
  }catch(error){showServeError(error.message);toast(error.message,'error');}
};
$('#restartServe').onclick=async()=>{try{await api('/api/serve/restart',{method:'POST'});toast(t('API server restarted'));await refreshStatus();}catch(e){showServeError(e.message);toast(e.message,'error');}};
async function copyText(text, success=t('Copied to clipboard')) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const area=document.createElement('textarea'); area.value=text; area.style.position='fixed'; area.style.opacity='0';
    document.body.append(area);
    let copied=false;
    try { area.select(); copied=document.execCommand('copy'); }
    catch {}
    finally { area.remove(); }
    if(!copied) { toast(t('Copy failed — select the text and copy manually'),'error'); return; }
  }
  toast(success);
}
$('#copyServeUrl').onclick=()=>copyText(connectionEndpoint(),t('Endpoint copied'));
$('#copyServeConnect').onclick=()=>copyText($(`[data-connect-panel="${connectTab}"] code`).textContent,t('Snippet copied'));
// Several models: status rows, memory bar, statistics, benchmarks, History and Logs views.
let selectedModel=localStorage.getItem('basal-ui.model')||'', previousDownloads=new Set(), rowsSignature='', playgroundSignature='';
let statsData={models:{},benchmarks:{}}, statsSignature='', benchSignature='', benchRunning=false;
let historyAfter=null, historyBefore=null, historySource='', historyTimer=null, historyDebounce=null, historyRows=new Map();
const historyOpen=new Map();
let logSource='basal-ui', logAfter=0, logGeneration=null, logTimer=null, shownHash=null;
const readyModels=()=>(statusCache?.models||[]).filter(m=>m.state==='ready');
const pct=v=>v==null?'—':`${(Number(v)*100).toFixed(1)}%`;
const num=(v,digits=1)=>v==null?'—':Number(v).toFixed(digits);
const shortName=id=>String(id).split('/').pop();
const clock=ts=>new Date(ts*1000).toLocaleTimeString([], {hour12:false});
async function act(fn){try{await fn();}catch(e){toast(e.message,'error');}}
function renderStatus(s) {
  statusCache=s;
  const models=s.models||[], ready=readyModels(), downloads=s.downloads||{}, repos=new Set(Object.keys(downloads));
  const finished=[...previousDownloads].some(repo=>!repos.has(repo)); previousDownloads=repos;
  const signature=JSON.stringify([models.map(m=>[m.id,m.state]),downloads]);
  if(finished)loadModels(); else if(signature!==rowsSignature)rerenderFilters();  // loaded chips and download progress in the model lists
  rowsSignature=signature;
  if(!ready.some(m=>m.id===selectedModel)&&ready.length)selectedModel=ready[0].id;
  if(selectedModel)localStorage.setItem('basal-ui.model',selectedModel);
  const busy=models.some(m=>['starting','downloading'].includes(m.state));
  const live=models.filter(answering).length;
  $('#footerState').textContent=ready.length?`${ready.length} ${t('ready')}${live?` · ${t('{n} answering',{n:live})}`:''}${busy?` · ${t('loading…')}`:''}`:busy?t('loading…'):t('idle');
  $('#footerDot').className=`status-dot${ready.length?' ready':models.some(m=>m.state==='error')?' error':''}${live?' active':''}`;
  $('#footerModel').textContent=models.map(m=>shortName(m.id)).join(', ')||t('No model loaded');
  $('#footerModel').title=models.map(m=>m.id).join(', ')||t('No model loaded');
  $('#footerUrl').textContent=s.serving?.url||''; $('#footerUrl').hidden=!s.serving?.url;
  $('#footerUnload').hidden=!models.length; $('#footerUnload').textContent=models.length>1?t('Unload all'):t('Unload');
  renderMemoryBar(s.memory||{},models);
  renderLoadedRows(models);
  renderInference(s);
  renderPlaygroundModels(ready);
  renderBenchmarkState(s.jobs?.benchmark,ready); renderCalibrationState(s.jobs?.calibrate); renderBatchState(s.jobs?.batch,ready);
  renderInspectState(s.inspect,s.jobs?.inspect);
  if(serveData)renderServeStatus(s);
  loadStats();
  const jobsRunning=Object.values(s.jobs||{}).some(j=>j.running);
  // every second while anything is answered and while the Overview (which shows it live) is open
  clearTimeout(pollTimer); pollTimer=setTimeout(refreshStatus,busy||repos.size||jobsRunning||models.some(answering)||!$('#overview').hidden?1000:5000);
}
function renderMemoryBar(memory, models) {
  const budget=Number(memory.budget_bytes)||0, reserve=Number(memory.reserve_bytes)||0, limit=Math.max(1,budget-reserve);
  const sizes=models.map(m=>({m,bytes:Number(m.rss_bytes)||Number(m.est_memory_bytes)||0}));
  const used=sizes.reduce((sum,x)=>sum+x.bytes,0);
  const segments=sizes.map(({m,bytes})=>`<span title="${esc(m.id)} · ${gb(bytes)}${m.rss_bytes?' '+t('resident'):' '+t('estimated')}" style="width:${Math.min(100,bytes/limit*100)}%"></span>`).join('');
  $('#memoryBar').innerHTML=budget?`<div class="memory-head"><span>${t('Memory')}</span><span>${t('{used} / {limit} (budget {budget}, keep {reserve} free) · available now {available}',{used:gb(used),limit:gb(limit),budget:gb(budget),reserve:gb(reserve),available:gb(memory.available_bytes)})}</span></div><div class="progress memory-progress${memory.over?' over':''}">${segments}</div>`:'';
}
// a loaded model answering now: requests basal-ui waits on (inflight) or its basal-serve's running / queued counters
const answering=m=>m.state==='ready'&&((m.inflight||[]).length>0||m.health?.running>0||m.health?.queued>0);
const INFERENCE_SOURCES={playground:'Playground',api:'API server',mcp:'MCP',benchmark:'Benchmark'};
const INFERENCE_JOBS={batch:'Batch',benchmark:'Benchmark',calibrate:'Calibration','backend-test':'Backend test'};
const ago=seconds=>seconds<60?`${Math.max(1,Math.round(seconds))} s`:seconds<3600?`${Math.round(seconds/60)} min`:`${Math.round(seconds/3600)} h`;
function renderInference(s) {
  const rows=(s.models||[]).filter(answering).map(m=>{
    const h=m.health||{}, inflight=m.inflight||[], bySource={};
    inflight.forEach(x=>{bySource[x.source]=(bySource[x.source]||0)+1;});
    // basal-serve's counters are read once a second: zeros next to requests in flight would only be out of date
    const counts=h.running>0||h.queued>0?t('{running} running · {queued} queued',{running:h.running??0,queued:h.queued??0}):tp(inflight.length,'{n} request','{n} requests');
    const sources=Object.entries(bySource).map(([k,n])=>`${t(INFERENCE_SOURCES[k]||k)} ${n}`).join(' · ');
    const longest=inflight.length?t('longest {seconds} s',{seconds:inflight[0].seconds.toFixed(1)}):'';
    return `<div class="inference-row"><span class="status-dot ready active"></span><strong>${esc(m.id)}</strong><span class="muted">${esc([counts,sources,longest].filter(Boolean).join(' · '))}</span></div>`;
  });
  for(const [kind,j] of Object.entries(s.jobs||{})){
    if(!j.running||!INFERENCE_JOBS[kind])continue;
    const share=j.total?Math.min(100,j.done/j.total*100):null;
    rows.push(`<div class="inference-row"><span class="status-dot ready active"></span><strong>${esc(t(INFERENCE_JOBS[kind]))}</strong><span class="muted">${esc([j.model,ts(j.phase,j.phase_code,j.phase_params),j.total?`${j.done} / ${j.total}`:''].filter(Boolean).join(' · '))}</span>${share==null?'':`<div class="progress"><span style="width:${share}%"></span></div>`}</div>`);
  }
  const last=Math.max(0,...Object.values(statsData.models||{}).map(x=>Number(x.last_used)||0));
  const idle=last?t('Idle · last decision {ago} ago',{ago:ago(Date.now()/1000-last)}):t('Idle');
  $('#inferenceNow').innerHTML=rows.length?rows.join(''):`<div class="inference-row idle"><span class="status-dot"></span><span class="muted">${esc(idle)}</span></div>`;
}
function renderLoadedRows(models) {
  $('#loadedModels').innerHTML=models.length?models.map(m=>{
    const stats=statsData.models?.[m.id], p=m.progress;
    const progress=p?`<div class="progress-wrap"><div class="progress"><span style="width:${p.total_bytes?Math.min(100,p.done_bytes/p.total_bytes*100):0}%"></span></div><small>${gb(p.done_bytes)} / ${gb(p.total_bytes)}</small></div>`:'';
    const variant=modelData.find(x=>x.id===m.id), chips=`${m.mode?`<span class="chip">${esc(m.mode)}</span>`:''}${variant?.community?`<span class="chip">${t('community')}</span>`:''}${(m.media||[]).map(x=>`<span class="chip media">${esc(x)}</span>`).join('')}${['prompt_format','layout','orders','vllm_preset','adapter'].filter(k=>m.entry?.[k]!=null).map(k=>`<span class="chip">${esc(k)} ${esc(m.entry[k])}</span>`).join('')}${uncalibrated(m.entry)?`<span class="badge fit-tight" title="${t('temperature_per_prim is {}: the probabilities are the raw model outputs')}">${t('uncalibrated')}</span>`:''}${m.restart_required?`<span class="badge fit-tight">${t('Restart to apply')}</span>`:''}`;
    const health=m.health, load=health?.reachable===false?t('unreachable'):health?.reachable!==true?t('load unavailable'):health.running==null||health.queued==null?t('load counters unavailable'):health.running===0&&health.queued===0?t('idle'):`${health.running} ${t('running')} · ${health.queued} ${t('queued')}`;
    const warning=m.entry?.calibration_warning?`<div class="reason wide">${esc(m.entry.calibration_warning)}</div>`:'';
    const actions=`<button type="button" data-restart="${esc(m.id)}"${['ready','error'].includes(m.state)?'':' disabled'}>${t('Restart')}</button>${['ready','error'].includes(m.state)?optionsButton(m.id,m.media_requested,'restart'):''}<a class="dashboard-link" href="#logs/${esc(encodeURIComponent(m.id))}">${t('Logs')}</a><button type="button" data-unload="${esc(m.id)}">${t('Unload')}</button>`;
    const optionChips=Object.entries(m.options||{}).filter(([k])=>k!=='mode').map(([k,v])=>`<span class="chip" title="${t('load option')}">${esc(v===true?k:v===false?t('no {option}',{option:k}):`${k} ${Array.isArray(v)?v.join('–'):v}`)}</span>`).join('');
    return `<div class="model-row"><div class="model-row-main"><span class="status-dot${m.state==='ready'?' ready':m.state==='error'?' error':''}${answering(m)?' active':''}"></span><span class="state-pill">${esc(t(m.state))}</span><strong>${esc(m.id)}</strong>${chips}${optionChips}<button type="button" class="model-card-open" data-id="${esc(m.id)}" aria-label="${t('Model information')}">ⓘ</button><span class="chip model-load">${esc(load)}</span></div><div class="muted model-row-meta">${stats?t('{estimated} estimated → {resident} resident · {requests} · avg prob {prob}',{estimated:m.est_memory_bytes==null?t('unknown'):gb(m.est_memory_bytes),resident:m.rss_bytes==null?'—':gb(m.rss_bytes),requests:tp(stats.requests,'{n} requests','{n} requests'),prob:pct(stats.avg_best_prob)}):t('{estimated} estimated → {resident} resident',{estimated:m.est_memory_bytes==null?t('unknown'):gb(m.est_memory_bytes),resident:m.rss_bytes==null?'—':gb(m.rss_bytes)})}</div>${progress}${warning}${m.error?`<div class="reason">${esc(m.error)}</div>`:''}<div class="model-row-actions">${actions}</div></div>`;
  }).join(''):`<div class="empty">${t('No model loaded · {link}',{link:`<a href="#manager">${t('Load a model')}</a>`})}</div>`;
  $('#loadedModels').querySelectorAll('[data-restart]').forEach(b=>b.onclick=()=>act(()=>restartOne(b.dataset.restart)));
  $('#loadedModels').querySelectorAll('[data-unload]').forEach(b=>b.onclick=()=>act(()=>unloadOne(b.dataset.unload)));
}
function renderPlaygroundModels(ready) {
  const select=$('#playgroundModel'), signature=JSON.stringify(ready.map(m=>[m.id,m.media]));
  if(signature!==playgroundSignature){
    playgroundSignature=signature;
    select.innerHTML=ready.map(m=>`<option value="${esc(m.id)}">${esc(m.id)}</option>`).join('')||`<option value="">${t('No model loaded')}</option>`;
  }
  select.value=selectedModel; select.disabled=!ready.length;
  $('#playgroundForm').disabled=!ready.length; $('#playgroundHint').hidden=Boolean(ready.length);
  setPlaygroundMedia(ready.find(m=>m.id===selectedModel)?.media||[]);
  setPlaygroundExperimental(ready.find(m=>m.id===selectedModel));
}
function setPlaygroundMedia(kinds) {
  if(JSON.stringify(kinds)===JSON.stringify(loadedMedia))return;
  loadedMedia=kinds; clearSelectedMedia(); syncRequest();
  $('#mediaField').hidden=!kinds.length;
  $('#mediaInput').accept=[kinds.includes('image')?'image/*':'',kinds.includes('audio')?'audio/*':''].filter(Boolean).join(',');
}
$('#playgroundModel').addEventListener('change',e=>{selectedModel=e.target.value;localStorage.setItem('basal-ui.model',selectedModel);const model=readyModels().find(m=>m.id===selectedModel);setPlaygroundMedia(model?.media||[]);setPlaygroundExperimental(model);syncRequest(false,REQUEST_OPTION_KEYS);if(serveData)renderConnect();});
async function unloadOne(id){await api('/api/unload',{method:'POST',body:JSON.stringify({id})});await refreshStatus();}
async function restartOne(id){await api('/api/restart',{method:'POST',body:JSON.stringify({id})});toast(`${t('Restarting {model}',{model:shortName(id)})}`);await refreshStatus();}
$('#footerUnload').onclick=()=>act(async()=>{
  if((statusCache?.models||[]).length>1&&!await confirmDialog({title:t('Unload all models?'),description:t('Every loaded model stops; clients of the API server get 404 until a model is loaded again.'),confirmLabel:t('Unload all'),destructive:true}))return;
  await api('/api/unload',{method:'POST',body:'{}'}); await refreshStatus();
});
let statsSequence=0;
async function loadStats() {
  const sequence=++statsSequence;
  try{
    const data=await api('/api/stats');
    if(sequence!==statsSequence)return;  // a reset or a newer poll answered meanwhile
    statsData=data;
    const signature=JSON.stringify(statsData.models);
    if(signature!==statsSignature){statsSignature=signature;renderStats();if(statusCache)renderLoadedRows(statusCache.models||[]);}
    const bench=JSON.stringify(statsData.benchmarks);
    if(bench!==benchSignature){benchSignature=bench;renderBenchmarkResults();}
  }catch{}
}
function renderStats() {
  const entries=Object.entries(statsData.models||{});
  $('#statsTable').innerHTML=`<div class="section-head"><h3>${t('Statistics')}</h3>${entries.length?`<button type="button" id="resetAllStats" class="destructive">${t('Reset all')}</button>`:''}</div>${entries.length?`<div class="table-wrap"><table><thead><tr><th>${t('Model')}</th><th>${t('Requests')}</th><th>${t('Questions')}</th><th>${t('Avg best-answer prob.')}</th><th>${t('Avg / p95 latency')}</th><th>${t('Input tokens')}</th><th>${t('Last used')}</th><th></th></tr></thead><tbody>${entries.map(([id,x])=>`<tr><td title="${esc(id)}">${esc(shortName(id))}</td><td>${x.requests}${x.errors?` <span class="reason">${tp(x.errors,'{n} errors','{n} errors')}</span>`:''}</td><td>${x.questions}</td><td>${pct(x.avg_best_prob)}</td><td>${num(x.avg_latency_ms,0)} / ${num(x.p95_ms,0)} ms</td><td>${x.input_tokens}</td><td>${x.last_used?esc(new Date(x.last_used*1000).toLocaleString()):'—'}</td><td><button type="button" data-reset="${esc(id)}">${t('Reset')}</button></td></tr><tr class="stats-types"><td colspan="8">${Object.entries(x.by_type||{}).map(([type,v])=>`<span class="chip">${esc(type)} · ${v.questions} · ${pct(v.avg_best_prob)}</span>`).join(' ')||`<span class="muted">${t('no answers yet')}</span>`}</td></tr>`).join('')}</tbody></table></div>`:`<div class="empty">${t('No decisions yet: statistics appear after the first playground, API or MCP decision.')}</div>`}`;
  $('#statsTable').querySelectorAll('[data-reset]').forEach(b=>b.onclick=()=>act(async()=>{if(await confirmDialog({title:t('Reset statistics?'),description:t('The counters of {model} start from zero.',{model:b.dataset.reset}),confirmLabel:t('Reset'),destructive:true})){await api(`/api/stats/${encodeURIComponent(b.dataset.reset)}`,{method:'DELETE'});toast(t('Statistics reset'));await loadStats();}}));
  const all=$('#resetAllStats'); if(all)all.onclick=()=>act(async()=>{if(await confirmDialog({title:t('Reset all statistics?'),description:t('The counters of every model start from zero. Benchmark results stay.'),confirmLabel:t('Reset all'),destructive:true})){await api('/api/stats',{method:'DELETE'});toast(t('Statistics reset'));await loadStats();}});
}
const BENCH_HINTS={benchmark:t('5 warm-up, 30 sequential and 120 concurrent requests (8 at a time); items with a gold answer are scored. Own set: {link}.',{link:'<code>docs/BENCHMARK_SETS.md</code>'}),
  loadtest:t('basal-loadtest against the model: 10 warm-up, sequential requests for the latency, then the concurrent ones for the throughput. Synthetic states of about N tokens replace the set when given.'),
  determinism:t('basal-loadtest --determinism: N requests alone, then the same N mixed with N others at the concurrency; a model loaded with Deterministic must return identical probabilities.'),
  modes:t('basal-bench in basal-ui’s process: each mode is loaded, measured and freed in turn (the first is the reference of the agreement). Needs the model’s memory once more (twice for eager-fp32) while it runs.')};
$('#benchmarks').innerHTML=`<div class="section-head"><h3>${t('Benchmarks')}</h3></div><div class="controls"><select id="benchmarkKind" aria-label="${t('Measurement')}"><option value="benchmark">${t('Benchmark (preset)')}</option><option value="loadtest">${t('Load test')}</option><option value="determinism">${t('Determinism check')}</option><option value="modes">${t('Compare engine modes')}</option></select><select id="benchmarkModel" aria-label="${t('Model to benchmark')}"></select><select id="benchmarkSet" aria-label="${t('Benchmark set')}"><option value="bundled">${tp(44,'Bundled examples ({n} item)','Bundled examples ({n} items)')}</option><option value="custom">${t('Custom path…')}</option></select><input id="benchmarkPath" type="text" placeholder="${t('/path/to/set.jsonl or a directory of .jsonl')}" hidden><button type="button" id="runBenchmark">${t('Run')}</button><button type="button" id="cancelBenchmark" hidden>${t('Cancel')}</button><span id="benchmarkProgress" class="muted"></span><a class="dashboard-link" href="#logs/job%3Abenchmark">${t('Logs')}</a></div>
<div class="bench-params" data-kinds="loadtest determinism"><div class="qf-row"><div class="qf"><label>${t('Synthetic tokens')}</label><input type="number" id="bpSynthetic" min="50" step="50" placeholder="${t('use the set')}"></div><div class="qf" data-kinds="loadtest"><label>${t('Sequential')}</label><input type="number" id="bpNSeq" min="1" value="200"></div><div class="qf" data-kinds="loadtest"><label>${t('Concurrent')}</label><input type="number" id="bpNConc" min="1" value="1000"></div><div class="qf"><label>${t('Concurrency')}</label><input type="number" id="bpConcurrency" min="1" value="32"></div><div class="qf" data-kinds="loadtest"><label>${t('Questions per request')}</label><input type="number" id="bpQpr" min="1" max="5" value="1"></div><div class="qf" data-kinds="loadtest"><label>${t('Seed')}</label><input type="number" id="bpSeed" min="0" value="0"></div><div class="qf" data-kinds="loadtest"><label>${t('Long share')}</label><input type="number" id="bpLong" min="0" max="1" step="0.1" value="0"></div><div class="qf" data-kinds="determinism"><label>${t('Requests (N)')}</label><input type="number" id="bpN" min="1" value="50"></div><div class="qf" data-kinds="loadtest" id="bpEarlyExitField" hidden><label>${t('Early exit')}</label><select id="bpEarlyExit"></select></div></div></div>
<div class="bench-params" data-kinds="modes"><div id="bpModes" class="controls"></div><div class="qf-row"><div class="qf"><label>${t('Questions (n)')}</label><input type="number" id="bpModesN" min="10" value="100"></div><div class="qf"><label>${t('Latency samples')}</label><input type="number" id="bpLatN" min="5" value="30"></div></div></div>
<p class="muted" id="benchmarkHint"></p><div id="benchmarkResults"></div>`;
$('#benchmarkSet').value=localStorage.getItem('basal-ui.benchmarkSet')==='custom'?'custom':'bundled';
$('#benchmarkPath').value=localStorage.getItem('basal-ui.benchmarkPath')||'';
$('#benchmarkKind').value=localStorage.getItem('basal-ui.benchmarkKind')||'benchmark';
$('#benchmarkSet').onchange=()=>{localStorage.setItem('basal-ui.benchmarkSet',$('#benchmarkSet').value);updateBenchmarkForm();};
$('#benchmarkPath').oninput=()=>localStorage.setItem('basal-ui.benchmarkPath',$('#benchmarkPath').value);
$('#benchmarkKind').onchange=()=>{localStorage.setItem('basal-ui.benchmarkKind',$('#benchmarkKind').value);benchModelsSignature='';renderBenchmarkState(statusCache?.jobs?.benchmark,readyModels());};
$('#benchmarkModel').onchange=()=>{updateBenchmarkForm();if($('#benchmarkKind').value==='modes')loadComparisonModes();};
let benchModelsSignature='', comparisonFor='';
function updateBenchmarkForm() {
  const kind=$('#benchmarkKind').value;
  document.querySelectorAll('#benchmarks [data-kinds]').forEach(x=>x.hidden=!x.dataset.kinds.split(' ').includes(kind));
  $('#benchmarkSet').hidden=kind==='modes'; $('#benchmarkPath').hidden=kind==='modes'||$('#benchmarkSet').value!=='custom';
  const levels=modelEntry($('#benchmarkModel').value)?.early_exit||[];
  $('#bpEarlyExit').innerHTML=['',...levels].map(l=>`<option value="${esc(l)}">${l||'off'}</option>`).join('');
  $('#bpEarlyExitField').hidden=kind!=='loadtest'||!levels.length;
  $('#benchmarkHint').innerHTML=BENCH_HINTS[kind];
}
async function loadComparisonModes() {
  const id=$('#benchmarkModel').value;
  if(!id||id===comparisonFor)return;
  comparisonFor=id;
  try{
    const d=await api(`/api/modes?id=${encodeURIComponent(id)}`);
    const preset=new Set(d.modes.filter(m=>!['ollama','llamacpp'].includes(m)).slice(0,2));  // external services: opt in
    $('#bpModes').innerHTML=d.modes.map(m=>`<label><input type="checkbox" role="switch" data-mode="${esc(m)}"${preset.has(m)?' checked aria-checked="true"':' aria-checked="false"'}> ${esc(m)}</label>`).join('')||'<span class="muted">No mode can run this model here.</span>';
  }catch(e){comparisonFor='';$('#bpModes').innerHTML=`<span class="reason">${esc(e.message)}</span>`;}
}
const intOf=sel=>$(sel).value===''?undefined:Number($(sel).value);
$('#runBenchmark').onclick=()=>act(async()=>{
  const kind=$('#benchmarkKind').value, body={id:$('#benchmarkModel').value,kind};
  if(kind!=='modes')body.set=$('#benchmarkSet').value==='custom'?$('#benchmarkPath').value.trim():'bundled';
  if(kind==='loadtest')body.params={n_seq:intOf('#bpNSeq'),n_conc:intOf('#bpNConc'),concurrency:intOf('#bpConcurrency'),questions_per_request:intOf('#bpQpr'),seed:intOf('#bpSeed'),long_frac:intOf('#bpLong'),synthetic:intOf('#bpSynthetic')??null,early_exit:$('#bpEarlyExit').value||null};
  if(kind==='determinism')body.params={determinism:intOf('#bpN'),concurrency:intOf('#bpConcurrency'),synthetic:intOf('#bpSynthetic')??null};
  if(kind==='modes')Object.assign(body,{modes:[...document.querySelectorAll('#bpModes [data-mode]:checked')].map(x=>x.dataset.mode),n:intOf('#bpModesN'),lat_n:intOf('#bpLatN')});
  if(body.params)body.params=Object.fromEntries(Object.entries(body.params).filter(([,v])=>v!==undefined));
  await api('/api/benchmark',{method:'POST',body:JSON.stringify(body)});
  toast(t('{benchmark} started',{benchmark:$('#benchmarkKind').selectedOptions[0].textContent})); await refreshStatus();
});
$('#cancelBenchmark').onclick=()=>act(async()=>{await api('/api/benchmark',{method:'DELETE'});await refreshStatus();});
function renderBenchmarkState(bench, ready) {
  const kind=$('#benchmarkKind').value, select=$('#benchmarkModel'), current=select.value;
  const ids=kind==='modes'?modelData.filter(m=>m.downloaded).map(m=>m.id):ready.map(m=>m.id);
  const signature=JSON.stringify([kind,ids]);
  if(signature!==benchModelsSignature){
    benchModelsSignature=signature;
    select.innerHTML=ids.map(id=>`<option value="${esc(id)}">${esc(id)}</option>`).join('');
    if(ids.includes(current))select.value=current;
    updateBenchmarkForm(); if(kind==='modes'){comparisonFor='';loadComparisonModes();}
  }
  const running=Boolean(bench?.running);
  $('#runBenchmark').disabled=running||!ids.length; $('#cancelBenchmark').hidden=!running;
  $('#benchmarkProgress').textContent=!bench?(ids.length?'':kind==='modes'?t('Download a model to compare its modes.'):t('Load a model to benchmark it.')):running?`${shortName(bench.model)} · ${bench.label} · ${ts(bench.phase,bench.phase_code,bench.phase_params)} ${bench.done}/${bench.total}`:bench.phase==='error'?`${t('Failed: {error}',{error:bench.error})}`:bench.phase==='cancelled'?t('Cancelled'):'';
  if(benchRunning&&!running)loadStats();
  benchRunning=running;
}
function benchmarkRow(x) {
  const kind=x.kind||'benchmark', head=`<td title="${esc(x.model)}">${esc(shortName(x.model))}</td><td><span class="chip">${esc(kind)}</span></td><td title="${esc(t(x.set))}">${esc(x.set==='bundled'?t('bundled'):shortName(x.set))}${x.items!=null?` (${x.items})`:''}</td><td>${esc(new Date(x.when*1000).toLocaleString())}</td>`;
  if(kind==='determinism'){const d=x.determinism||{};return `<tr>${head}<td colspan="7"><span class="badge ${d.identical===d.requests?'fit-fits':'fit-tight'}">${t('identical')} ${esc(d.identical)}/${esc(d.requests)}</span> ${t('max |Δp|')} ${esc(d.max_abs_diff)} · ${t('decision flips')} ${esc(d.decision_flips)} · ${t('concurrency')} ${esc(d.concurrency)}</td></tr>`;}
  if(kind==='modes'){
    const f=(v,d=1)=>v==null||Number.isNaN(Number(v))?'—':Number(v).toFixed(d);
    const rows=(x.modes||[]).map(m=>m.error?`<tr><td>${esc(m.mode)}</td><td colspan="8"><span class="reason wide">${esc(m.error)}</span></td></tr>`:`<tr><td>${esc(m.mode)}</td><td>${f(m.lat1_ms)}</td><td>${f(m.lat2_ms)}</td><td>${f(m.lat2_p95_ms)}</td><td>${f(m.dec_s)}</td><td>${m.agree==null?`ref`:f(m.agree,3)}</td><td>${m.acc==null?'—':pct(m.acc)}</td><td>${f(m.load_s)}</td><td>${f(m.mem_gb,2)}</td></tr>`).join('');
    return `<tr>${head}<td colspan="7"><details><summary>${(x.modes||[]).map(m=>esc(m.mode)).join(' vs ')}</summary><table class="mini-table"><thead><tr><th>${t('Mode')}</th><th>${t('lat1 ms')}</th><th>${t('lat2 ms')}</th><th>${t('p95 ms')}</th><th>${t('dec/s')}</th><th>${t('agree')}</th><th>${t('acc')}</th><th>${t('load s')}</th><th>${t('mem GB')}</th></tr></thead><tbody>${rows}</tbody></table></details></td></tr>`;
  }
  return `<tr>${head}<td>${num(x.p50_ms,0)} ms</td><td>${num(x.p95_ms,0)} ms</td><td>${num(x.decisions_per_s)}</td><td>${num(x.questions_per_s)}</td><td>${num(x.input_tokens_per_s,0)}</td><td title="${esc(Object.entries(x.accuracy?.by_file||{}).map(([f,v])=>`${f}: ${pct(v)}`).join(', '))}">${pct(x.accuracy?.overall)}</td><td>${esc(x.errors??'—')}</td></tr>`;
}
function renderBenchmarkResults() {
  const rows=Object.values(statsData.benchmarks||{}).flat().sort((a,b)=>b.when-a.when);
  $('#benchmarkResults').innerHTML=rows.length?`<div class="table-wrap"><table><thead><tr><th>${t('Model')}</th><th>${t('Kind')}</th><th>${t('Set')}</th><th>${t('When')}</th><th>p50</th><th>p95</th><th>${t('dec/s')}</th><th>q/s</th><th>tok/s</th><th>${t('Accuracy')}</th><th>${t('Errors')}</th></tr></thead><tbody>${rows.map(benchmarkRow).join('')}</tbody></table></div>`:`<div class="empty">${t('No benchmark results yet.')}</div>`;
}
// Calibration: basal-calibrate on the user's labelled requests (a temporary uncalibrated child of the model; with task
// questions the engine in basal-ui's process), or a file fitted elsewhere imported
$('#calibrationBlock').innerHTML=`<div class="section-head"><h3>${t('Calibration')}</h3></div><div class="controls"><select id="calibrationModel" aria-label="${t('Model to calibrate')}"></select><input id="calibrationInput" type="text" placeholder="${t('/path/to/labelled.jsonl')}" size="40"><label><input id="calibrationT1" type="checkbox" checked> 1%</label><label><input id="calibrationT5" type="checkbox" checked> 5%</label><button type="button" id="runCalibration">${t('Calibrate')}</button><button type="button" id="cancelCalibration" hidden>${t('Cancel')}</button><span id="calibrationProgress" class="muted"></span><a class="dashboard-link" href="#logs/job%3Acalibrate">${t('Logs')}</a></div><div class="controls"><input id="calibrationImportPath" type="text" placeholder="${t('/path/to/CALIBRATION.custom.json fitted elsewhere')}" size="40" aria-label="${t('Calibration file to import')}"><button type="button" id="importCalibration">${t('Import')}</button></div><p class="muted">${t("Fits temperatures and certified thresholds on your labelled requests (format: {input}; the simple {run} items with {gold} work too). Decision questions run against a temporary uncalibrated copy of the model, which needs its memory once more while it runs; task questions (classify / verify / extract with gold) are certified on the answers as served, so the engine runs in basal-ui's process instead (cancelling then takes effect when basal-calibrate finishes). Import adds a file fitted elsewhere (basal-calibrate on another machine); one fitted on another model is listed with basal-serve's warning. The selected file applies from the model's next start.",{input:'<code>docs/CALIBRATION.md#input</code>',run:'<code>basal-run</code>',gold:'<code>gold</code>'})}</p><div id="calibrationFiles"></div><dialog id="reportDialog" class="confirm-dialog report-dialog"><h3>${t('Calibration report')}</h3><pre id="reportText" class="logs"></pre><div class="dialog-actions"><button type="button" id="reportClose">${t('Close')}</button></div></dialog>`;
$('#calibrationBlock').insertAdjacentHTML('beforeend',`<details id="calibrationAdvanced"><summary>${t('Advanced')}</summary><div class="controls"><label><input type="checkbox" id="calStrict"> ${t('Strict')}</label><label>${t('Reference')} <input type="text" id="calReference" placeholder="${t('basal-1.5 or path')}"></label><label>${t('Min/type')} <input type="number" id="calMinType" min="1" value="1"></label><label>${t('Min blend')} <input type="number" id="calMinBlend" min="0" value="0"></label><label>${t('Min family')} <input type="number" id="calMinFamily" min="1" value="1"></label><label>${t('Min/set')} <input type="number" id="calMinSet" min="1" value="1"></label><label>${t('Concurrency')} <input type="number" id="calConcurrency" min="1" max="256" value="1"></label><label>${t('Compare')} <select id="calCompare"><option value="shipped">${t('shipped')}</option><option value="none">${t('none')}</option><option value="path">${t('calibration file…')}</option></select></label><input id="calComparePath" type="text" placeholder="${t('calibration file path')}" hidden><label><input type="checkbox" id="calSaveRows"> ${t('Save rows')}</label><label><input type="checkbox" id="calReportOnly"> ${t('Report only')}</label></div></details>`);
$('#calCompare').onchange=()=>$('#calComparePath').hidden=$('#calCompare').value!=='path';
['calStrict','calSaveRows','calReportOnly'].forEach(id=>{const x=$('#'+id);x.setAttribute('role','switch');setChecked(x,x.checked);});
$('#calibrationInput').value=localStorage.getItem('basal-ui.calibrationInput')||'';
$('#calibrationInput').oninput=()=>localStorage.setItem('basal-ui.calibrationInput',$('#calibrationInput').value);
document.querySelectorAll('#calibrationBlock input[type=checkbox]').forEach(i=>{i.setAttribute('role','switch');setChecked(i,i.checked);});
document.body.append($('#reportDialog'));  // a modal inside a hidden view would block the page without showing
$('#reportClose').onclick=()=>$('#reportDialog').close();
let calibrationRunning=false, calibrationSignature='', calibrationReportSeen=null;
function renderCalibrationModels() {
  const select=$('#calibrationModel'), current=select.value||localStorage.getItem('basal-ui.calibrationModel')||'', rows=modelData.filter(m=>m.downloaded);
  const signature=JSON.stringify(rows.map(m=>m.id));
  if(signature!==calibrationSignature){
    calibrationSignature=signature;
    select.innerHTML=rows.map(m=>`<option value="${esc(m.id)}">${esc(m.id)}</option>`).join('')||`<option value="">${t('Download a model first')}</option>`;
    if(rows.some(m=>m.id===current))select.value=current;
    loadCalibrations();
  }
  $('#runCalibration').disabled=calibrationRunning||!rows.length;
}
$('#calibrationModel').onchange=()=>{localStorage.setItem('basal-ui.calibrationModel',$('#calibrationModel').value);loadCalibrations();};
$('#runCalibration').onclick=()=>act(async()=>{
  const targets=[['#calibrationT1','0.01'],['#calibrationT5','0.05']].filter(([s])=>$(s).checked).map(([,t])=>t);
  await api('/api/calibrate',{method:'POST',body:JSON.stringify({id:$('#calibrationModel').value,input:$('#calibrationInput').value.trim(),targets})});
  toast(t('Calibration started')); await refreshStatus();
});
$('#cancelCalibration').onclick=()=>act(async()=>{await api('/api/calibrate',{method:'DELETE'});await refreshStatus();});
$('#runCalibration').onclick=()=>act(async()=>{
  const targets=[['#calibrationT1','0.01'],['#calibrationT5','0.05']].filter(([s])=>$(s).checked).map(([,t])=>t);
  const compare=$('#calCompare').value,advanced={strict:$('#calStrict').checked,reference:$('#calReference').value.trim()||null,min_per_type:Number($('#calMinType').value),min_blend:Number($('#calMinBlend').value),min_family:Number($('#calMinFamily').value),min_per_set:Number($('#calMinSet').value),concurrency:Number($('#calConcurrency').value),compare:compare==='path'?$('#calComparePath').value.trim():compare,save_rows:$('#calSaveRows').checked,report_only:$('#calReportOnly').checked};
  await api('/api/calibrate',{method:'POST',body:JSON.stringify({id:$('#calibrationModel').value,input:$('#calibrationInput').value.trim(),targets,advanced})});
  toast(t('Calibration started')); await refreshStatus();
});
$('#importCalibration').onclick=()=>act(async()=>{
  const id=$('#calibrationModel').value, d=await api('/api/calibrations/import',{method:'POST',body:JSON.stringify({id,path:$('#calibrationImportPath').value.trim()})});
  renderCalibrationFiles(id,d); $('#calibrationImportPath').value='';
  const f=d.files.find(x=>x.file===d.file); toast(f?.warning?`${t('Imported')} — ${f.warning}`:t('Calibration imported'));
});
// the certified thresholds {target: {type: threshold}} with the task ones {task: {target: threshold}}
function allThresholds(f) {
  const out=JSON.parse(JSON.stringify(f?.thresholds||{}));
  for(const [task,byTarget] of Object.entries(f?.task_thresholds||{}))for(const [k,v] of Object.entries(byTarget||{}))(out[k]??={})[task]=v;
  return out;
}
function renderCalibrationState(job) {
  const running=Boolean(job?.running);
  $('#cancelCalibration').hidden=!running; $('#runCalibration').disabled=running||!$('#calibrationModel').value;
  $('#calibrationProgress').textContent=!job?'':running?`${shortName(job.model)} · ${ts(job.phase,job.phase_code,job.phase_params)}…`:job.phase==='error'?`${t('Failed: {error}',{error:job.error})}`:job.phase==='cancelled'?t('Cancelled'):['done','done_with_warnings'].includes(job.phase)?`${job.phase==='done_with_warnings'?t('Completed with warnings'):t('Completed (see report for certification)')}: ${shortName(job.result?.file||'')}${job.result?.warnings?.length?' · '+job.result.warnings.join(' · '):''}`:'';
  if(calibrationRunning&&!running)loadCalibrations();
  if(!running&&job?.result?.report_only&&calibrationReportSeen!==job.started){calibrationReportSeen=job.started;$('#reportText').textContent=job.result.report||'';$('#reportDialog').showModal();}
  calibrationRunning=running;
}
async function loadCalibrations() {
  const id=$('#calibrationModel').value;
  if(!id){$('#calibrationFiles').innerHTML='';return;}
  try{renderCalibrationFiles(id,await api(`/api/calibrations?id=${encodeURIComponent(id)}`));}
  catch(e){$('#calibrationFiles').innerHTML=`<div class="error-box">${esc(e.message)}</div>`;}
}
function thresholdCell(thresholds) {
  const types=[...new Set(Object.values(thresholds||{}).flatMap(Object.keys))];
  return types.map(t=>`<span class="chip">${esc(t)} ${['0.01','0.05'].map(k=>`${(Number(k)*100).toFixed(0)}%: ${thresholds?.[k]?.[t]==null?'—':Number(thresholds[k][t]).toFixed(3)}`).join(' · ')}</span>`).join(' ')||`<span class="muted">${t('none certified')}</span>`;
}
function renderCalibrationFiles(id, d) {
  const loaded=(statusCache?.models||[]).find(m=>m.id===id);
  const row=(f,value,label)=>{
    const fitted=f?.fitted_on&&typeof f.fitted_on==='object'?f.fitted_on:null, gates=f?.gates||{};
    const automated=Object.entries(gates.automated_at||{}).map(([k,v])=>`${(Number(k)*100).toFixed(0)}%: ${pct(v)} at ${pct(gates.automated_error?.[k])} error`).join(' · ');
    const checked=(d.selected||'')===value;
    return `<tr><td><label><input type="radio" name="calibrationUse" value="${esc(value)}"${checked?' checked':''}> ${esc(label)}</label>${f?.imported?` <span class="chip">${t('imported')}</span>`:''}${checked&&loaded?.restart_required?` <span class="badge fit-tight">${t('Restart to apply')}</span>`:''}${f?.warning?`<span class="reason">${esc(f.warning)}</span>`:''}${f?.error?`<span class="reason">${esc(f.error)}</span>`:''}${(f?.warnings||[]).map(w=>`<span class="reason">${esc(w)}</span>`).join('')}</td><td>${fitted?`${esc(fitted.data??'')} · ${esc(fitted.decisions??'—')} ${t('decisions')}`:esc(typeof f?.fitted_on==='string'?f.fitted_on:'—')}</td><td>${fitted?Object.entries(fitted.per_type||{}).map(([t,n])=>`<span class="chip">${esc(t)} ${esc(n)}</span>`).join(' '):''}</td><td>${thresholdCell(allThresholds(f))}</td><td>${esc(automated||'—')}</td><td>${value&&f?.has_report?`<button type="button" data-report="${esc(value)}">${t('Report')}</button>`:''}</td></tr>`;
  };
  const ctx=d.shipped?.context||{}, context=d.shipped?`${t('Shipped default')}: ${esc(shortName(d.shipped.file))} · ${esc(ctx.active?t('running settings'):t('saved settings for next start'))} · ${esc(ctx.mode)}${ctx.quant?` · ${esc(ctx.quant)}`:''}${ctx.preset?` · ${esc(ctx.preset)}`:''} · ${esc(ctx.layout)}${ctx.adapter?` · adapter ${esc(shortName(ctx.adapter))}`:''}`:t('Shipped default unavailable');
  const selection=d.selected?`${t('Selected custom calibration for next start')}: ${esc(shortName(d.selected))}`:`${t('Selected calibration')}: ${t('shipped default')}`;
  const shipped=d.shipped?row(d.shipped,'',`${t('Shipped default')} · ${shortName(d.shipped.file)}`):'';
  $('#calibrationFiles').innerHTML=`<p class="muted">${context}<br>${selection}</p><div class="table-wrap"><table><thead><tr><th>${t('Use')}</th><th>${t('Fitted on')}</th><th>${t('Per type')}</th><th>${t('Certified thresholds')}</th><th>${t('Automated on CERT')}</th><th></th></tr></thead><tbody>${shipped}${d.files.map(f=>row(f,f.file,new Date(f.when*1000).toLocaleString())).join('')}</tbody></table></div>`;
  for(const f of d.files||[])if(f.rows){
    const radio=[...$('#calibrationFiles').querySelectorAll('input[name=calibrationUse]')].find(x=>x.value===f.file);
    if(!radio)continue;
    const button=document.createElement('button');button.type='button';button.textContent=t('Refit');button.dataset.refit=f.rows;
    radio.closest('tr').lastElementChild.append(' ',button);
  }
  $('#calibrationFiles').querySelectorAll('[data-refit]').forEach(b=>b.onclick=()=>act(async()=>{
    await api('/api/calibrate',{method:'POST',body:JSON.stringify({id,input:b.dataset.refit,from_rows:true})});
    toast(t('Calibration refit started'));await refreshStatus();
  }));
  $('#calibrationFiles').querySelectorAll('input[name=calibrationUse]').forEach(r=>r.onchange=()=>act(async()=>{
    renderCalibrationFiles(id,await api('/api/calibrations/select',{method:'POST',body:JSON.stringify({id,file:r.value||null})}));
    toast(loaded?t('Calibration selected — restart the model to apply'):t('Calibration selected')); await refreshStatus();
  }));
  $('#calibrationFiles').querySelectorAll('[data-report]').forEach(b=>b.onclick=()=>act(async()=>{
    const r=await api(`/api/calibrations/report?id=${encodeURIComponent(id)}&file=${encodeURIComponent(b.dataset.report)}`);
    $('#reportText').textContent=r.report; $('#reportDialog').showModal();
  }));
}
// Load options: the engine mode and basal-serve's runtime settings of one load (basal_ui/options.py)
const GRAPH_MODES=['fast','fast-nocompile','fast-exit','fp8','nvfp4'];
const OPTION_SECTIONS=[
  ['Engine',['revision','adapter','ollama_url','ollama_model','llamacpp_url','http_parallel','quant','attn','dtype','vllm_preset','gpu_memory','vllm_fast_prefill','device','eager_attn','eager_forward','ple_offload','exit_heads','mlx_packed']],
  ['Determinism & orders',['deterministic','orders','auto_tau','auto_band']],
  ['Batching',['max_batch','wait_ms','batch_window_ms','soam']],
  ['Caching',['state_cache_gb','prefix_cache']],
  ['Scheduling',['scheduler','long_lane','long_tokens','long_share','long_slice_ms']],
  ['Context',['layout','context_overflow','truncate_head','max_len']],
  ['Kernels (CUDA graph engine)',['gemma_attn','merge','lean','tree','batches','readout_layer','template_prefix','fp8_accum']],
  ['Calibration',['strict_calibration']],
  ['Experimental',['think','think_tokens','think_python','think_url','think_calibration','think_model','think_adapter','think_memory','think_max_len','think_log','early_exit']],
];
const OPTION_FIELDS={
  mlx_packed:{label:'MLX packed (opt-in)',kind:'bool',modes:['mlx','mlx-q8']},
  quant:{label:'Quantisation',kind:'select',values:[[null,'as the mode'],['fp8','fp8'],['nvfp4','nvfp4'],['q8','q8 (mlx)']],modes:[...GRAPH_MODES,'mlx','mlx-q8']},
  attn:{label:'Attention kernel',kind:'select',values:['auto','fa2','fa3','fa4','cudnn'],modes:GRAPH_MODES},
  gemma_attn:{label:'Gemma 4 attention (graph engine)',kind:'select',values:['auto','math','flex','fa3','fa3+math'],modes:GRAPH_MODES},
  dtype:{label:'dtype',kind:'select',values:['bfloat16','float16','float32'],modes:[...GRAPH_MODES,'eager','mps','vllm']},
  vllm_fast_prefill:{label:'vLLM KV-sharing fast prefill (Gemma 4)',kind:'select',values:['auto','off'],modes:['vllm']},
  eager_attn:{label:'Gemma 4 eager attention',kind:'text',placeholder:'math, auto or sliding=K,full=K',modes:['eager']},
  eager_forward:{label:'Gemma 4 eager forward',kind:'select',values:[[null,'basal (default)'],['basal','basal'],['hf','hf (transformers)']],modes:['eager']},
  ple_offload:{label:'Per-layer embeddings in host memory (Gemma 4 E2B / E4B)',kind:'bool',modes:['eager']},
  exit_heads:{label:'Exit heads directory',kind:'text',placeholder:'<model>/exit_heads',modes:['fast-exit']},
  merge:{label:'Merged projections',kind:'select',values:['auto','on','off'],modes:GRAPH_MODES},
  lean:{label:'Lean layer function',kind:'select',values:['off','auto','on','pass2'],modes:GRAPH_MODES},
  tree:{label:'Pass-2 trie mask',kind:'select',values:['dense','auto','flex'],modes:GRAPH_MODES},
  batches:{label:'Batch rows',kind:'select',values:['auto','compact','default','fine'],modes:GRAPH_MODES},
  readout_layer:{label:'Readout-only last layer',kind:'select',values:['off','auto','on'],modes:GRAPH_MODES},
  template_prefix:{label:'Template prefix computed once',kind:'select',values:['auto','on','off'],modes:GRAPH_MODES},
  fp8_accum:{label:'FP8 accumulation',kind:'select',values:['fast','full'],modes:GRAPH_MODES},
  strict_calibration:{label:'Refuse a calibration fitted on another model (--strict-calibration)',kind:'bool'},
  vllm_preset:{label:'vLLM preset',kind:'select',values:['default','deterministic'],modes:['vllm']},
  gpu_memory:{label:'GPU memory share',kind:'num',attrs:'min="0.05" max="1" step="0.05"',modes:['vllm','sglang']},
  device:{label:'Device',kind:'select',values:[[null,'auto'],'cuda','mps','cpu'],modes:['eager','mps']},
  ollama_url:{label:'Ollama URL',kind:'text',modes:['ollama']},
  ollama_model:{label:'Ollama model',kind:'text',placeholder:'derived from the model',modes:['ollama']},
  llamacpp_url:{label:'llama-server URL',kind:'text',modes:['llamacpp']},
  http_parallel:{label:'Parallel HTTP requests',kind:'num',attrs:'min="1" step="1"',modes:['ollama','llamacpp']},
  deterministic:{label:'Deterministic (decisions independent of batching)',kind:'bool'},
  orders:{label:'Option orders',kind:'select',values:[[null,'model default'],'1','2','auto','2le4','2le8']},
  auto_tau:{label:'auto: margin τ',kind:'num',attrs:'min="0" max="1" step="0.05"'},
  auto_band:{label:'auto: band (low, high)',kind:'band'},
  max_batch:{label:'Max batch',kind:'num',attrs:'min="1" step="1"'},
  wait_ms:{label:'Wait (ms)',kind:'num',attrs:'min="0" step="0.5"'},
  batch_window_ms:{label:'Batch window (ms)',kind:'num',attrs:'min="0" step="0.5"'},
  soam:{label:'SOAM (shared option attention mask)',kind:'bool'},
  state_cache_gb:{label:'State cache (GB)',kind:'num',attrs:'min="0" step="0.5"'},
  prefix_cache:{label:'Prefix cache',kind:'bool'},
  scheduler:{label:'Scheduler',kind:'select',values:['auto','on','off']},
  long_lane:{label:'Long-request lane',kind:'bool'},
  long_tokens:{label:'Long request from (tokens)',kind:'num',attrs:'min="1" step="1"'},
  long_share:{label:'Long lane share',kind:'num',attrs:'min="0" max="1" step="0.05"'},
  long_slice_ms:{label:'Long lane slice (ms)',kind:'num',attrs:'min="0" step="1"'},
  layout:{label:'Prompt layout (prompt-format-2 models)',kind:'select',values:[[null,'model default (basal.json)'],['canonical','canonical'],['state_twice','state_twice (state read twice)'],['qfirst','qfirst (question first, one pass per question)']]},
  context_overflow:{label:'Prompt over the context',kind:'select',values:[['truncate','truncate the state'],['refuse','refuse (422)']]},
  truncate_head:{label:'Kept from the beginning',kind:'percent'},
  max_len:{label:'Max length (vLLM / SGLang)',kind:'num',attrs:'min="512" max="131072" step="512"',placeholder:'API server setting'},
  revision:{label:'Model revision',kind:'text',placeholder:'latest (main)'},
  adapter:{label:'LoRA adapter directory (merged at load)',kind:'text',placeholder:'/path/to/adapter'},
  think:{label:'Thinking (reason before low-margin decisions)',kind:'bool'},
  think_tokens:{label:'Reasoning tokens',kind:'num',attrs:'min="1" step="32"',think:true},
  think_python:{label:'Python with vLLM',kind:'text',placeholder:'basal-ui’s Python',think:true},
  think_url:{label:'Running think server URL',kind:'text',placeholder:'start one',think:true},
  think_calibration:{label:'Readout-2 calibration',kind:'text',placeholder:'<model>/CALIBRATION.think.json or off',think:true},
  think_model:{label:'Think weights',kind:'text',placeholder:'<model>/think/',think:true},
  think_adapter:{label:'LoRA adapter',kind:'text',placeholder:'<model>/think/adapter',think:true},
  think_memory:{label:'vLLM GPU memory fraction',kind:'num',attrs:'min="0.05" max="1" step="0.05"',think:true},
  think_max_len:{label:'vLLM context',kind:'num',attrs:'min="512" step="512"',think:true},
  think_log:{label:'vLLM log file',kind:'text',placeholder:'basal-think-vllm.log',think:true},
  early_exit:{label:'Early exit (default policy)',kind:'select',values:['off','0.999','0.995','0.99','0.98']},
};
// action: "load" (Model manager), "save" (Models: set up the options of the next load), "restart" (a loaded model)
const optionsButton=(id,media=false,action='load')=>`<button type="button" class="options-model" data-id="${esc(id)}" data-media="${media}" data-action="${action}" title="${t({load:'Load with options',save:'Set up the load options',restart:'Restart with other load options'}[action])}">⚙${t({load:' Options',save:' Setup',restart:''}[action])}</button>`;
document.body.insertAdjacentHTML('beforeend',`<dialog id="loadOptions" class="confirm-dialog options-dialog"><h3>${t('Load options')}</h3><p id="optionsModel" class="muted"></p><p id="optionsSetupNote" class="muted option-note" hidden>${t('Media loads choose their mode (eager or vLLM) when loading from Model manager.')}</p><div id="optionsForm"></div><div id="optionsError" class="error-box" hidden></div><div class="dialog-actions"><label><input type="checkbox" role="switch" id="optionsRemember"> ${t('Remember for this model')}</label><button type="button" id="optionsReset">${t('Reset to defaults')}</button><button type="button" id="optionsCancel">${t('Cancel')}</button><button type="button" id="optionsApply" class="primary">${t('Load')}</button></div></dialog>`);
$('#optionsModel').insertAdjacentHTML('afterend',`<div id="loadPresetControls" class="controls"><select id="loadPresetSelect" aria-label="${t('Load preset')}"></select><button type="button" id="applyLoadPreset">${t('Apply')}</button><button type="button" id="saveLoadPreset">${t('Save as…')}</button><button type="button" id="deleteLoadPreset">${t('Delete')}</button></div>`);
$('#saveLoadPreset').onclick=()=>promptPresetName('load');
$('#applyLoadPreset').onclick=()=>{const v=presetData.load?.[$('#loadPresetSelect').value];if(v)renderOptionsForm({...optionsState.data.defaults,...v});};
$('#deleteLoadPreset').onclick=()=>act(async()=>{const n=$('#loadPresetSelect').value;if(n)await deletePreset('load',n);});
$('#loadPresetSelect').onchange=()=>$('#deleteLoadPreset').disabled=!$('#loadPresetSelect').value;
let optionsState=null;
const optionValue=v=>v===null||v===undefined?'':String(v);
function optionInput(key, value) {
  const f=OPTION_FIELDS[key], id=`opt-${key}`;
  if(f.kind==='bool')return `<label><input type="checkbox" role="switch" id="${id}"${value?' checked aria-checked="true"':' aria-checked="false"'}> ${esc(t(f.label))}</label>`;
  const listed=(f.values||[]).map(v=>Array.isArray(v)?v:[v,v]);
  const input=f.kind==='select'?`<select id="${id}">${listed.map(([v,l])=>`<option value="${esc(optionValue(v))}"${optionValue(v)===optionValue(value)?' selected':''}>${esc(t(l))}</option>`).join('')}${listed.some(([v])=>optionValue(v)===optionValue(value))?'':`<option value="${esc(optionValue(value))}" selected>${esc(value)}</option>`}</select>`
    :f.kind==='band'?`<span class="controls"><input type="number" id="${id}-lo" min="0" max="1" step="0.05" value="${esc(value?.[0])}"><input type="number" id="${id}-hi" min="0" max="1" step="0.05" value="${esc(value?.[1])}"></span>`
    :f.kind==='percent'?`<span class="controls"><input type="range" id="${id}" min="0" max="100" step="1" value="${Math.round(Number(value)*100)}"><output id="${id}-out">${Math.round(Number(value)*100)}%</output></span>`
    :`<input type="${f.kind==='num'?'number':'text'}" id="${id}" value="${esc(optionValue(value))}" placeholder="${esc(t(f.placeholder||''))}" ${f.attrs||''}>`;
  return `<label for="${id}">${esc(t(f.label))}</label>${input}`;
}
const optionSectionOpen=new Map();  // section title → open, as the user left it (this session)
function renderOptionsForm(values) {
  const d=optionsState.data, list=optionsState.media?d.media_modes:d.modes, defaultMode=list.find(m=>m.default)?.mode;
  const mode=list.some(m=>m.mode===values.mode)?values.mode:defaultMode;
  const modes=`<div class="option-modes">${list.map(m=>{const reason=ts(m.reason,m.reason_code,m.reason_params);return `<label class="option-mode${m.available?'':' unavailable'}" title="${esc(reason||'')}"><input type="radio" name="optMode" value="${esc(m.mode)}"${m.mode===mode?' checked':''}${m.available?'':' disabled'}> <span>${esc(m.mode)}${m.default?` <span class="chip">${t('default')}</span>`:''}</span>${reason?`<small>${esc(reason)}</small>`:''}</label>`;}).join('')}</div>${optionsState.media?`<p class="muted">${t('With media (--media on) the model is served with --mode eager, or with --mode vllm on an NVIDIA GPU with basal[vllm].')}</p>`:''}`;
  const experimental='<p class="muted option-note" id="optExperimentalNote"></p>';
  // collapsible sections: Engine and any section with a non-default value open, unless the user toggled it this session
  const changed=keys=>keys.some(k=>JSON.stringify(values[k])!==JSON.stringify(d.defaults[k]));
  const isOpen=(title,keys)=>optionSectionOpen.get(title)??(title==='Engine'||changed(keys));
  $('#optionsForm').innerHTML=OPTION_SECTIONS.map(([title,keys])=>`<details class="option-section" data-section="${esc(title)}"${isOpen(title,keys)?' open':''}><summary>${esc(t(title))}</summary>${title==='Engine'?modes:''}<div class="option-grid">${keys.map(k=>`<div class="opt-field" data-opt="${k}"${OPTION_FIELDS[k].modes?` data-modes="${OPTION_FIELDS[k].modes.join(' ')}"`:''}>${optionInput(k,values[k])}</div>`).join('')}</div>${title==='Experimental'?experimental:title==='Caching'?`<p class="muted">${t('State cache reuses the longest cached prefix of growing or edited states, then computes the changed suffix. Its GB budget consumes GPU memory alongside weights and graphs; reduce it if memory is tight. No cache-hit counters are reported.')}</p>`:title==='Kernels (CUDA graph engine)'?`<p class="muted">${t('auto uses compact rows where supported. default uses padded graphs; fine captures more shapes and takes longer to start. compact needs supported attention kernels and more GPU memory (about 6 GB extra on measured RTX 3090 runs). Unsupported combinations show the engine’s refusal in the model row and Logs.')}</p>`:title==='Engine'?'<p class="muted" id="optPackedNote"></p>':''}</details>`).join('');
  $('#optionsForm').querySelectorAll('details.option-section > summary').forEach(s=>s.onclick=()=>optionSectionOpen.set(s.parentElement.dataset.section,!s.parentElement.open));  // a user's click (or Enter / Space), not the initial open
  $('#optionsForm').querySelectorAll('input[type=range]').forEach(r=>r.oninput=()=>{$(`#${r.id}-out`).textContent=`${r.value}%`;});
  $('#optionsForm').onchange=()=>updateOptionVisibility();
  updateOptionVisibility();
}
const optionModes=()=>optionsState.media?optionsState.data.media_modes:optionsState.data.modes;
function selectedOptionMode() { return $('#optionsForm input[name=optMode]:checked')?.value||optionModes().find(m=>m.default)?.mode; }
function updateOptionVisibility() {
  const mode=selectedOptionMode(), think=optionsState.data.think||{};
  const packed=$('#opt-mlx_packed'), packedReason=optionsState.data.mlx_packed_reason;
  const packedReasonText=ts(packedReason,optionsState.data.mlx_packed_reason_code,optionsState.data.mlx_packed_reason_params);
  packed.disabled=Boolean(packedReason)||optionsState.media||!['mlx','mlx-q8'].includes(mode);
  $('#optPackedNote').textContent=mode==='mps'?t('Packed PyTorch execution: choose Device cpu for the CPU path, or mps for Apple Silicon. The CPU default remains eager.'):packedReasonText||t('MLX packed is off by default. Measured on an Apple M5 Pro (basal 2.3.1, #36): basal-2.0-4.5B 134 → 88 ms per two-order decision, mini 47 → 31 ms, the same decisions as the KV-cache path in bf16 (mlx-q8 on the 4.5B: 0.955 instead of 0.977 agreement). Compatible Llama groups with prefix plus all suffixes up to 512 tokens use packed rows; longer groups keep the KV-cache path.');
  $('#opt-device option[value=cuda]').disabled=mode==='mps';
  $('#optionsForm').querySelectorAll('[data-modes]').forEach(x=>x.hidden=!x.dataset.modes.split(' ').includes(mode));
  // thinking: basal-2.0 4.5B with vLLM (here, in another environment, or a running server); early exit: fast-exit
  const thinkBox=$('#opt-think'), exitBox=$('#opt-early_exit');
  thinkBox.disabled=!think.model; if(!think.model)setChecked(thinkBox,false);
  Object.entries(OPTION_FIELDS).filter(([,f])=>f.think).forEach(([k])=>{$(`[data-opt="${k}"]`).hidden=!thinkBox.checked;});
  exitBox.disabled=mode!=='fast-exit'; if(exitBox.disabled)exitBox.value='off';
  const notes=[];
  if(!think.model)notes.push(ts(think.reason,think.reason_code,think.reason_params));
  else if(!think.vllm)notes.push(t('vLLM is not installed in basal-ui’s environment: give the Python of one that has it, or the URL of a running think server.'));
  if(mode!=='fast-exit')notes.push(t('Early exit needs --mode fast-exit (the exit heads of basal-1.0, bf16 on a CUDA GPU).'));
  // LoRA adapters at load: only with an engine that takes --adapter (basal-ui's feature probe)
  const adapterBox=$('#opt-adapter'), adapterReason=optionsState.data.adapter_reason;
  if(adapterBox){adapterBox.disabled=Boolean(adapterReason); if(adapterReason){adapterBox.value='';adapterBox.title=ts(adapterReason,optionsState.data.adapter_reason_code,optionsState.data.adapter_reason_params);}else adapterBox.title='';}
  $('#optExperimentalNote').textContent=notes.join(' ');
}
function collectOptions() {
  const d=optionsState.data, out={}, defaultMode=optionModes().find(m=>m.default)?.mode, mode=selectedOptionMode();
  out.mode=mode===defaultMode?null:mode;
  for(const [key,f] of Object.entries(OPTION_FIELDS)) {
    if(f.kind==='band'){const lo=$(`#opt-${key}-lo`)?.value, hi=$(`#opt-${key}-hi`)?.value;out[key]=!lo||!hi?d.defaults[key]:[Number(lo),Number(hi)];continue;}
    const el=$(`#opt-${key}`);
    if(!el||el.closest('[hidden]')||el.disabled)continue;  // a field of another mode, or unavailable, keeps its default
    if(f.kind==='bool')out[key]=el.checked;
    else if(f.kind==='percent')out[key]=Number(el.value)===Math.round(d.defaults[key]*100)?d.defaults[key]:Number(el.value)/100;
    else if(f.kind==='num')out[key]=el.value===''?d.defaults[key]:Number(el.value);
    else if(f.kind==='select')out[key]=el.value===''?null:el.value;
    else out[key]=el.value.trim()||d.defaults[key];
  }
  return Object.fromEntries(Object.entries(out).filter(([k,v])=>JSON.stringify(v)!==JSON.stringify(d.defaults[k])));
}
async function openLoadOptions(id, media=false, action='load') {
  const data=await api(`/api/models/options?id=${encodeURIComponent(id)}`);
  const loaded=(statusCache?.models||[]).find(m=>m.id===id), restart=action==='restart', save=action==='save';
  optionsState={id,media,restart,save,data};
  $('#optionsModel').textContent=`${id}${media?` · ${t('with media')}`:''}${restart?` · ${t('restarts the running model')}`:save?` · ${t('kept for the next load')}`:''}`;
  $('#optionsApply').textContent=restart?t('Restart'):save?t('Save'):t('Load');
  $('#optionsSetupNote').hidden=!save;
  $('#optionsRemember').closest('label').hidden=save;  // Save always remembers
  setChecked($('#optionsRemember'),data.saved);
  renderOptionsForm(restart&&loaded?{...data.defaults,...loaded.options}:data.options);
  $('#optionsError').hidden=true;
  await loadPresets();renderLoadPresets();
  $('#loadPresetSelect').value='';
  $('#deleteLoadPreset').disabled=true;
  $('#loadOptions').showModal();
  $('#optionsForm').scrollTop=0;  // the dialog is reused: another model's options start at the top
}
document.addEventListener('click',e=>{const b=e.target.closest('.options-model');if(b)act(()=>openLoadOptions(b.dataset.id,b.dataset.media==='true',b.dataset.action));});
$('#optionsCancel').onclick=()=>$('#loadOptions').close();
$('#optionsReset').onclick=()=>renderOptionsForm(optionsState.data.defaults);
$('#optionsApply').onclick=async()=>{
  const {id,media,restart,save,data}=optionsState, options=collectOptions(), remember=save||$('#optionsRemember').checked;
  $('#optionsApply').disabled=true;
  try{
    if(remember||data.saved)await api('/api/models/options',{method:'PUT',body:JSON.stringify({id,options:remember?options:{}})});
    if(save){$('#loadOptions').close();toast(t('Load options saved for {model}',{model:shortName(id)}));await loadModels();return;}
    if(restart)await api('/api/restart',{method:'POST',body:JSON.stringify({id,options})});
    else{statusSequence++;clearSelectedMedia();await api('/api/load',{method:'POST',body:JSON.stringify({id,...(media?{media:true}:{}),options})});}
    $('#loadOptions').close(); toast(restart?t('Restarting {model}',{model:shortName(id)}):t('Model load started')); await refreshStatus();
  }catch(e){$('#optionsError').textContent=e.message;$('#optionsError').hidden=false;}
  finally{$('#optionsApply').disabled=false;}
};
function syncHistoryModels(models) {
  const select=$('#historyModel'), current=select.value, ids=[...new Set([...models,...(current?[current]:[])])];
  if(JSON.stringify([...select.options].map(o=>o.value).slice(1))===JSON.stringify(ids))return;
  select.innerHTML=`<option value="">${t('All models')}</option>`+ids.map(id=>`<option value="${esc(id)}">${esc(id)}</option>`).join('');
  select.value=current;
}
let historySequence=0;
async function loadHistory(mode='reset') {
  clearTimeout(historyTimer);
  if($('#history').hidden)return;
  const sequence=++historySequence;
  const params=new URLSearchParams({limit:'100'}), query=$('#historySearch').value.trim();
  if($('#historyModel').value)params.set('model',$('#historyModel').value);
  if(historySource)params.set('source',historySource);
  if(query)params.set('q',query);
  if(mode==='more'&&historyBefore)params.set('before',historyBefore);
  if(mode==='poll')params.set('order','asc');
  if(mode==='poll'&&historyAfter)params.set('after',historyAfter);
  try{
    let d, items=[], pollCursor=null;
    do{
      d=await api(`/api/history?${params}`);
      const page=d.items||[]; items.push(...page);
      if(mode==='poll'&&page.length)pollCursor=page.at(-1).id;
      if(mode!=='poll'||page.length<100)break;
      params.set('after',page.at(-1).id);
    }while(sequence===historySequence);
    if(sequence!==historySequence)return;  // the filter changed meanwhile: that query's own fetch renders
    syncHistoryModels(d.models||[]);
    if(mode==='reset'){historyRows=new Map();historyOpen.clear();}
    items.forEach(x=>historyRows.set(x.id,x));
    if(mode!=='poll')$('#historyMore').hidden=items.length<100;
    const sorted=[...historyRows.values()].sort((a,b)=>b.ts-a.ts);
    if(mode==='reset'){historyAfter=items[0]?.id||null;historyBefore=items.at(-1)?.id||null;}
    else if(mode==='more'&&items.length)historyBefore=items.at(-1).id;
    else if(mode==='poll'){historyAfter=pollCursor||historyAfter;if(!historyBefore)historyBefore=items[0]?.id||null;}
    if(mode!=='poll'||items.length)renderHistory(sorted);
  }catch(e){if(sequence!==historySequence)return;$('#historyTable').innerHTML=`<div class="error-box">${esc(e.message)}</div>`;}
  if(!$('#history').hidden&&sequence===historySequence)historyTimer=setTimeout(()=>loadHistory('poll'),5000);
}
function renderHistory(items) {
  const flags=x=>`${x.deferred?` <span class="badge fit-too_large">${t('deferred')}</span>`:''}${x.truncated?` <span class="badge fit-tight">${t('truncated')}</span>`:''}`;
  $('#historyTable').innerHTML=items.length?`<table><thead><tr><th>${t('Time')}</th><th>${t('Model')}</th><th>${t('Source')}</th><th>${t('State')}</th><th>${t('Answers')}</th><th>${t('Latency')}</th><th>${t('Status')}</th></tr></thead><tbody>${items.map(x=>`<tr class="history-row" data-history="${esc(x.id)}"><td title="${esc(new Date(x.ts*1000).toISOString())}">${esc(clock(x.ts))}</td><td title="${esc(x.model)}">${esc(shortName(x.model))}</td><td><span class="chip">${esc(t(x.source))}</span></td><td class="muted history-state">${esc(x.state_preview)}</td><td>${Object.entries(x.answers||{}).map(([n,a])=>`<span class="chip">${esc(n)}: ${esc(a.best??'—')} (${pct(a.confidence)})</span>`).join(' ')}${flags(x)}</td><td>${num(x.latency_ms,0)} ms</td><td><span class="badge ${x.status>=200&&x.status<300?'fit-fits':'fit-too_large'}">${esc(x.status)}</span></td></tr>`).join('')}</tbody></table>`:`<div class="empty">${t('No decisions yet — try the Playground.')}</div>`;
  $('#historyTable').querySelectorAll('[data-history]').forEach(row=>{
    row.onclick=()=>act(()=>toggleHistory(row));
    if(historyOpen.has(row.dataset.history))row.after(historyDetailRow(historyOpen.get(row.dataset.history)));
  });
}
async function toggleHistory(row) {
  const id=row.dataset.history, next=row.nextElementSibling;
  if(next?.classList.contains('history-detail')){next.remove();historyOpen.delete(id);return;}
  const record=await api(`/api/history/${encodeURIComponent(id)}`);
  historyOpen.set(id,record); row.after(historyDetailRow(record));
}
function historyDetailRow(x) {
  const tr=document.createElement('tr'); tr.className='history-detail';
  tr.innerHTML=`<td colspan="7"><div class="history-panes"><div><strong>${t('Recorded request')}</strong><pre class="history-request"></pre></div><div><strong>${t('Response')}</strong><pre class="history-response"></pre></div></div><div class="controls"><button type="button" class="copy-request">${t('Copy request')}</button><button type="button" class="replay-request">${t('Replay in Playground')}</button></div><div class="history-replay-error error-box" role="alert" hidden></div></td>`;
  $('.history-request',tr).textContent=JSON.stringify(x.request,null,2);
  $('.history-response',tr).textContent=JSON.stringify(x.response,null,2);
  $('.copy-request',tr).onclick=()=>copyText(JSON.stringify(x.request,null,2),t('Request copied'));
  const replay=$('.replay-request',tr), error=$('.history-replay-error',tr);
  if(x.replay_error){replay.disabled=true;error.textContent=x.replay_error;error.hidden=false;}
  replay.onclick=()=>replayRequest(x);
  return tr;
}
function replayRequest(x) {
  if(x.replay_error||!x.replay_request){toast(x.replay_error||t('This request cannot be replayed.'),'error');return;}
  if(!readyModels().some(m=>m.id===x.model)){toast(t('Model {model} is not loaded',{model:x.model}),'error');return;}
  selectedModel=x.model; localStorage.setItem('basal-ui.model',selectedModel);
  $('#playgroundModel').value=selectedModel; setPlaygroundMedia(readyModels().find(m=>m.id===selectedModel)?.media||[]);
  $('#results').innerHTML='';$('#rawDetails').hidden=true;$('#decideError').hidden=true;
  setRequest(x.replay_request);
  showView('playground',true);
}
function resetHistory(){historyAfter=null;historyBefore=null;loadHistory('reset');}
$('#historySearch').oninput=()=>{clearTimeout(historyDebounce);historyDebounce=setTimeout(resetHistory,300);};
$('#historyModel').onchange=resetHistory;
$('#historySource').querySelectorAll('[data-source]').forEach(b=>b.classList.toggle('active',b.dataset.source===historySource));
$('#historySource').onclick=e=>{const b=e.target.closest('[data-source]');if(!b)return;historySource=b.dataset.source;$('#historySource').querySelectorAll('[data-source]').forEach(x=>x.classList.toggle('active',x===b));resetHistory();};
$('#historyMore').onclick=()=>loadHistory('more');
$('#clearHistory').onclick=()=>act(async()=>{
  if(!await confirmDialog({title:t('Clear history?'),description:t('Every recorded decision (playground, API and MCP) is deleted.'),confirmLabel:t('Clear history'),destructive:true}))return;
  const d=await api('/api/history',{method:'DELETE'}); toast(tp(d.cleared,'{n} decisions deleted','{n} decisions deleted')); resetHistory();
});
async function loadLogs() {
  clearTimeout(logTimer);
  if($('#logs').hidden)return;
  const out=$('#logOutput'), source=logSource;
  try{
    const d=await api(`/api/logs?source=${encodeURIComponent(source)}&after=${logAfter}${logGeneration?`&generation=${encodeURIComponent(logGeneration)}`:''}`);
    if(source!==logSource)return;  // the user switched tabs meanwhile: that tab's own fetch renders
    if(logGeneration&&d.generation!==logGeneration){out.textContent='';logAfter=0;}
    logGeneration=d.generation;
    $('#logSources').innerHTML=d.sources.map(x=>`<button type="button" data-logsource="${esc(x.id)}" class="${x.id===logSource?'active':''}" title="${esc(x.id)}">${esc(x.id==='basal-ui'?'basal-ui':shortName(x.id))}${x.state==='gone'?` <span class="chip">${t('gone')}</span>`:x.id.startsWith('job:')&&x.state==='running'?` <span class="chip">${t('running')}</span>`:''}</button>`).join('');
    const atBottom=out.scrollTop+out.clientHeight>=out.scrollHeight-8;
    if(d.lines.length){
      out.append(document.createTextNode(d.lines.map(x=>source==='basal-ui'?`${clock(x.ts)} ${x.text}`:x.text).join('\n')+'\n'));
      logAfter=d.lines.at(-1).seq;
    }
    $('#logEmpty').hidden=Boolean(out.textContent);
    if($('#logFollow').checked&&atBottom)out.scrollTop=out.scrollHeight;
  }catch(e){
    if(source!==logSource)return;
    if(source!=='basal-ui'){toast(e.message,'error');showView('logs/basal-ui',true);return;}
  }
  if(!$('#logs').hidden&&source===logSource)logTimer=setTimeout(loadLogs,2000);
}
$('#logSources').onclick=e=>{const b=e.target.closest('[data-logsource]');if(b)showView(`logs/${encodeURIComponent(b.dataset.logsource)}`,true);};
$('#copyLogs').onclick=()=>copyText($('#logOutput').textContent,t('Log copied'));
$('#clearLogs').onclick=()=>{$('#logOutput').textContent='';$('#logEmpty').hidden=false;};
$('#downloadLogs').onclick=()=>{
  const a=document.createElement('a'), url=URL.createObjectURL(new Blob([$('#logOutput').textContent],{type:'text/plain'}));
  a.href=url; a.download=`${shortName(logSource)}-${new Date().toISOString().slice(0,10)}.log`; a.click(); URL.revokeObjectURL(url);
};
// Batch: basal-run over a JSONL file against a loaded model (basal_ui/batch.py)
function answerBest(a) {
  if(!a||typeof a!=='object')return null;
  for(const key of ['choice','action','tool','label','score'])if(a[key]!=null)return String(typeof a[key]==='number'?Number(a[key]).toFixed(2):a[key]);
  for(const key of ['selected','labels','tools'])if(Array.isArray(a[key]))return a[key].join(', ')||t('none');
  if(typeof a.noul==='number')return a.noul>=0.5?'true':'false';
  return a.type||null;
}
let batchRowsLoaded=0, batchRunning=false, batchResult=null;
$('#batchInput').value=localStorage.getItem('basal-ui.batchInput')||'';
$('#batchInput').oninput=()=>localStorage.setItem('basal-ui.batchInput',$('#batchInput').value);
function renderBatchState(job, ready) {
  const select=$('#batchModel'), current=select.value, ids=ready.map(m=>m.id);
  if(JSON.stringify([...select.options].map(o=>o.value))!==JSON.stringify(ids))select.innerHTML=ids.map(id=>`<option value="${esc(id)}">${esc(id)}</option>`).join('')||`<option value="">${t('Load a model first')}</option>`;
  if(ids.includes(current))select.value=current; else if(ids.includes(selectedModel))select.value=selectedModel;
  const levels=modelEntry(select.value)?.early_exit||[], ee=$('#batchEarlyExit');
  if(JSON.stringify([...ee.options].map(o=>o.value))!==JSON.stringify(['',...levels]))ee.innerHTML=['',...levels].map(l=>`<option value="${esc(l)}">${l?`${t('early exit')} ${esc(l)}`:t('no early exit')}</option>`).join('');
  ee.hidden=!levels.length;
  const running=Boolean(job?.running);
  $('#runBatch').disabled=running||!ids.length; $('#cancelBatch').hidden=!running;
  $('#batchProgress').innerHTML=!job?'':running?`<div class="progress-wrap"><div class="progress"><span style="width:${job.total?job.done/job.total*100:0}%"></span></div><small>${job.done} / ${job.total}</small></div>`:job.phase==='error'?`<div class="error-box">${esc(job.error)}</div>`:job.phase==='cancelled'?`<p class="muted">${t('Cancelled.')}</p>`:'';
  if(!running&&job?.phase==='done'&&(batchRunning||!batchResult))showBatchResult(job.result);
  batchRunning=running;
}
function showBatchResult(r) {
  batchResult=r;
  $('#batchSummary').innerHTML=`<div class="chips"><span class="chip">${tp(r.items,'{n} items','{n} items')}</span><span class="chip">${tp(r.errors,'{n} errors','{n} errors')}</span><span class="chip">${esc(r.seconds)} s</span><span class="chip">${esc(r.items_per_s)} ${t('items/s')}</span><span class="chip">${t('server p50')} ${esc(r.server_latency_p50_ms??'—')} ms</span>${r.accuracy!=null?`<span class="chip">${t('accuracy')} ${pct(r.accuracy)}</span>`:''}</div><p class="muted mono">${esc(r.output)}</p>`;
  $('#downloadBatch').hidden=$('#copyBatchPath').hidden=false;
  batchRowsLoaded=0; $('#batchRows').innerHTML=''; loadBatchRows();
}
async function loadBatchRows() {
  const d=await api(`/api/batch/rows?offset=${batchRowsLoaded}&limit=100`);
  const rows=d.rows.map(x=>`<tr><td>${esc(x.id)}</td><td>${x.option!=null?`<span class="chip">${esc(x.prediction)}: ${esc(x.option)} (${pct(x.confidence)})</span>`:Object.entries(x.answers||{}).map(([n,a])=>`<span class="chip">${esc(n)}: ${esc(answerBest(a)??'—')}${a?.confidence!=null?` (${pct(a.confidence)})`:''}</span>`).join(' ')}${x.correct===true?` <span class="badge fit-fits">${t('correct')}</span>`:x.correct===false?` <span class="badge fit-too_large">${t('wrong')}</span>`:''}</td><td>${num(x.latency_ms,1)} ms</td><td>${x.error?`<span class="reason">${esc(httpFailure(x.status,textOf(x.error)))}${x.request_id?` [${t('request')} ${esc(x.request_id)}]`:""}</span>`:''}</td></tr>`).join('');
  if(!batchRowsLoaded)$('#batchRows').innerHTML=`<table><thead><tr><th>${t('Id')}</th><th>${t('Answer')}</th><th>${t('Latency')}</th><th>${t('Error')}</th></tr></thead><tbody></tbody></table>`;
  $('#batchRows tbody').insertAdjacentHTML('beforeend',rows);
  batchRowsLoaded+=d.rows.length; $('#batchMore').hidden=batchRowsLoaded>=d.total;
}
$('#batchMore').onclick=()=>act(loadBatchRows);
$('#runBatch').onclick=()=>act(async()=>{
  const body={id:$('#batchModel').value,input:$('#batchInput').value.trim(),concurrency:Number($('#batchConcurrency').value)||16};
  if($('#batchOutput').value.trim())body.output=$('#batchOutput').value.trim();
  if(!$('#batchEarlyExit').hidden&&$('#batchEarlyExit').value)body.early_exit=$('#batchEarlyExit').value;
  await api('/api/batch',{method:'POST',body:JSON.stringify(body)});
  $('#batchSummary').innerHTML=''; $('#batchRows').innerHTML=''; $('#batchMore').hidden=$('#downloadBatch').hidden=$('#copyBatchPath').hidden=true;
  toast(t('Batch started')); await refreshStatus();
});
$('#cancelBatch').onclick=()=>act(async()=>{await api('/api/batch',{method:'DELETE'});await refreshStatus();});
$('#downloadBatch').onclick=()=>{const a=document.createElement('a');a.href='/api/batch/download';a.click();};
$('#copyBatchPath').onclick=()=>copyText(batchResult?.output||'',t('Output path copied'));
// Inspect: basal-viz embedded at /viz/ for a downloaded bf16 model (basal_ui/inspect.py)
const inspectableModel=m=>m.port==='bf16'&&!m.experimental;
let inspectSignature='', inspectReady=false;
function renderInspectModels() {
  const select=$('#inspectModel'), current=select.value||localStorage.getItem('basal-ui.inspectModel')||'', rows=modelData.filter(m=>m.downloaded);
  const signature=JSON.stringify(rows.map(m=>m.id));
  if(signature===inspectSignature)return;
  inspectSignature=signature;
  select.innerHTML=rows.map(m=>`<option value="${esc(m.id)}"${inspectableModel(m)?'':' disabled'}>${esc(m.id)}${inspectableModel(m)?'':` — ${t('basal-viz reads bf16 weights')}`}</option>`).join('')||`<option value="">${t('Download a bf16 model first')}</option>`;
  const ok=rows.filter(inspectableModel).map(m=>m.id);
  select.value=ok.includes(current)?current:ok[0]||'';
  loadInspectCalibrations();
}
async function loadInspectCalibrations() {
  const id=$('#inspectModel').value, sel=$('#inspectCalibration');
  sel.innerHTML=`<option value="">${t('shipped calibration')}</option>`;
  if(!id)return;
  try{const d=await api(`/api/calibrations?id=${encodeURIComponent(id)}`);sel.insertAdjacentHTML('beforeend',d.files.map(f=>{const count=f.fitted_on?.decisions;return `<option value="${esc(f.file)}"${f.file===d.selected?' selected':''}>${esc(new Date(f.when*1000).toLocaleString())} · ${count==null?'?':tp(count,'{n} decisions','{n} decisions')}</option>`;}).join(''));}catch{}
}
$('#inspectModel').onchange=()=>{localStorage.setItem('basal-ui.inspectModel',$('#inspectModel').value);loadInspectCalibrations();};
$('#startInspect').onclick=()=>act(async()=>{const orders=$('#inspectOrders').value;await api('/api/inspect',{method:'POST',body:JSON.stringify({id:$('#inspectModel').value,calibration:$('#inspectCalibration').value||null,orders:orders?Number(orders):null})});toast(t('Inspector loading'));await refreshStatus();});
$('#stopInspect').onclick=()=>act(async()=>{await api('/api/inspect',{method:'DELETE'});await refreshStatus();});
function renderInspectState(state, job) {
  const loading=Boolean(job?.running), ready=state?.state==='ready', frame=$('#vizFrame');
  $('#inspectState').textContent=loading?t('loading {model}…',{model:shortName(job.model)}):ready?t('{status}: {model} · {mode} on {device} · {orders}',{status:t('ready'),model:state.model,mode:state.mode,device:state.device,orders:tp(state.orders,'{n} option order','{n} option orders')}):job?.phase==='error'?t('Failed: {message}',{message:job.error}):t('idle');
  $('#stopInspect').hidden=!(ready||loading); $('#startInspect').disabled=loading||!$('#inspectModel').value;
  if(ready&&!inspectReady){frame.dataset.loaded='';frame.onload=()=>{frame.dataset.loaded='1';};frame.src='/viz/';}
  if(!ready&&inspectReady){frame.src='about:blank';$('#inspectNotice').hidden=true;}
  frame.hidden=!ready; inspectReady=ready;
}
// What the inspector would explain of a Playground decision: the request it can explain and how that differs from
// the served one, or why it cannot (another model, media, nothing but task questions).
function inspectHandoff(request) {
  const st=statusCache?.inspect||{}, served=(statusCache?.models||[]).find(m=>m.id===request.model);
  const variant=id=>modelData.find(m=>m.id===id), sv=variant(request.model), iv=variant(st.model);
  if(request.media)return {blocked:t('basal-viz explains text decisions; this request carries media')};
  if(st.model!==request.model&&!(sv&&iv&&sv.family===iv.family&&sv.tier===iv.tier))
    return {blocked:t('the inspector runs {inspector}, this decision came from {model}: start the inspector with the bf16 weights of {weights}',{inspector:st.model,model:request.model,weights:sv?`${sv.family} ${sv.tier}`:request.model})};
  const body=structuredClone(request), notes=[];
  for(const key of ['model','think','think_text','early_exit'])delete body[key];
  const tasks=Object.entries(body.questions||{}).filter(([,q])=>TASK_TYPES.includes(q?.type)).map(([n])=>n);
  tasks.forEach(n=>delete body.questions[n]);
  if(!Object.keys(body.questions||{}).length)return {blocked:t('basal-viz explains decision questions; this request has only task questions')};
  if(tasks.length)notes.push(t('{questions}: task questions are left out (basal-viz explains the decision questions)',{questions:tasks.join(', ')}));
  if(st.model!==request.model)notes.push(t('weights: served {label} ({model}), explained bf16 ({inspector})',{label:sv.label,model:request.model,inspector:st.model}));
  if(served?.mode&&served.mode!==st.mode)notes.push(t('engine: served {mode}, explained {inspector} on {device}',{mode:served.mode,inspector:st.mode,device:st.device}));
  const orders=served?.entry?.orders;
  if(orders!=null&&String(orders)!==String(st.orders))notes.push(t('option orders: served {orders}, explained {inspector}{match}',{orders,inspector:st.orders,match:['1','2'].includes(String(orders))?` (${t('start the inspector with {n} option {order} to match',{n:orders,order:String(orders)==='1'?t('order'):t('orders')})})`:''}));
  if(served?.entry&&JSON.stringify(served.entry.temperature_per_prim||{})!==JSON.stringify(st.temperatures||{}))
    notes.push(t('calibration: the served temperatures differ from the inspector’s (choose the calibration the model is served with)'));
  if(served?.options?.think&&request.think!=='off')notes.push(t('thinking: explained without the reasoning (the first readout)'));
  if(request.early_exit&&request.early_exit!=='off')notes.push(t('early exit {policy}: explained at the final layer',{policy:request.early_exit}));
  return {body,notes};
}
function inspectDecision(request) {
  if(statusCache?.inspect?.state!=='ready'){toast(t('Start the inspector for {model} first',{model:request.model||t('a model')}),'error');showView('inspect',true);return;}
  const handoff=inspectHandoff(request), notice=$('#inspectNotice');
  if(handoff.blocked){toast(handoff.blocked,'error');return;}
  notice.className=handoff.notes.length?'inspect-notice differs':'inspect-notice same';
  notice.innerHTML=handoff.notes.length?`<strong>${t('This explanation differs from the served decision of {model}:',{model:esc(request.model)})}</strong><ul>${handoff.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>`:t('Same model, engine, option orders and calibration as the served decision of {model}.',{model:esc(request.model)});
  notice.hidden=false;
  showView('inspect',true);
  const frame=$('#vizFrame'), send=()=>frame.contentWindow.postMessage({request:handoff.body},location.origin);
  if(frame.dataset.loaded==='1')send(); else frame.addEventListener('load',send,{once:true});
}
const PAGES={backends:['10 / BACKENDS','Backends','Local inference engines, compatibility, setup and explicit verification.'],overview:['01 / OVERVIEW','Overview','Hardware, model readiness, and serving at a glance.'],models:['02 / MODELS','Models','Browse suggested and all basal variants.'],manager:['03 / MODEL MANAGER','Model manager','Loaded models, their statistics and benchmarks, models on disk, and the models directory.'],playground:['04 / PLAYGROUND','Playground','Ask typed questions of a loaded model.'],api:['05 / API SERVER','API server','One port for every loaded model, keys, MCP and the memory budget.'],history:['06 / HISTORY','History','Every decision this app answered: playground, API and MCP.'],logs:['07 / LOGS','Logs','Output of each basal-serve and basal-ui’s own events.'],batch:['08 / BATCH','Batch','Run a JSONL file of requests through a loaded model and export the answers.'],inspect:['09 / INSPECT','Inspect','basal-viz: the prompt, the tokens, every layer and the calibrated decision.']};
function showView(hash, updateHash=false) {
  const raw=String(hash||'').replace(/^#/,''), slash=raw.indexOf('/');
  const view=slash<0?raw:raw.slice(0,slash), arg=slash<0?'':raw.slice(slash+1), name=PAGES[view]?view:'overview';
  const [eyebrow,title,description]=PAGES[name], number=eyebrow.split(' / ')[0];
  $('#pageEyebrow').textContent=LANG==='en'?eyebrow:`${number} / ${t(title).toLocaleUpperCase(LANG)}`; $('#pageTitle').textContent=t(title); $('#pageDescription').textContent=t(description);
  document.querySelectorAll('.view').forEach(v=>v.hidden=v.dataset.page!==name);
  document.querySelectorAll('.side-nav [data-view]').forEach(b=>{const active=b.dataset.view===name;b.classList.toggle('active',active);if(active)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  const target=`#${name}${arg?`/${arg}`:''}`;
  if(updateHash&&location.hash!==target)history.pushState(null,'',target);
  shownHash=location.hash;
  clearTimeout(historyTimer); clearTimeout(logTimer);
  if(name==='backends')window.dispatchEvent(new Event('backends:view'));
  if(name==='history')loadHistory('reset');
  if(name==='logs'){
    let source=logSource; try{if(arg)source=decodeURIComponent(arg);}catch{}
    if(source!==logSource){logSource=source;logAfter=0;logGeneration=null;$('#logOutput').textContent='';}
    loadLogs();
  }
}
const route=()=>{if(location.hash!==shownHash)showView(location.hash);};
document.querySelectorAll('.side-nav [data-view]').forEach(b=>b.onclick=()=>showView(b.dataset.view,true));
window.addEventListener('hashchange',route); window.addEventListener('popstate',route);
function selectModelsTab(tab){for(const x of ['suggested','all']){$(`#${x==='all'?'allModels':x}`).hidden=x!==tab;const b=$(`#${x==='all'?'allModelsTab':'suggestedTab'}`);b.classList.toggle('active',x===tab);b.setAttribute('aria-selected',String(x===tab));}localStorage.setItem('basal-ui.tab',tab);}
$('#suggestedTab').onclick=()=>selectModelsTab('suggested');$('#allModelsTab').onclick=()=>selectModelsTab('all');
selectModelsTab(localStorage.getItem('basal-ui.tab')==='all'?'all':'suggested');
async function loadMemorySettings(){
  try{
    const d=await api('/api/memory'), budget=$('input#memoryBudget');
    budget.placeholder=t('Hardware budget: {size} GiB',{size:(d.hardware_budget_bytes/2**30).toFixed(1)});
    for(const [input,bytes] of [[budget,d.settings.budget_bytes],[$('#memoryReserve'),d.settings.reserve_bytes]]){
      input.value=bytes==null?'':String(Number((bytes/2**30).toFixed(3)));
      input.dataset.bytes=bytes==null?'':String(bytes); input.dataset.shown=input.value;  // an unedited field saves its exact bytes
    }
  }catch(e){$('#memoryError').textContent=e.message;$('#memoryError').hidden=false;}
}
$('#saveMemory').onclick=async()=>{
  const bytesOf=input=>{const value=input.value.trim();if(value===input.dataset.shown&&input.dataset.bytes!==undefined)return input.dataset.bytes===''?null:Number(input.dataset.bytes);return value===''?null:Math.round(Number(value)*2**30);};
  try{
    await api('/api/memory',{method:'PUT',body:JSON.stringify({budget_bytes:bytesOf($('input#memoryBudget')),reserve_bytes:bytesOf($('#memoryReserve'))??0})});
    $('#memoryError').hidden=true; toast(t('Memory settings saved')); await Promise.all([loadMemorySettings(),refreshStatus()]);
  }catch(e){$('#memoryError').textContent=e.message;$('#memoryError').hidden=false;toast(e.message,'error');}
};
showView(location.hash);
loadMemorySettings();
loadPresets();refreshUpdates();
 (async()=>{try{renderHardware(await api('/api/hardware'));}catch(e){$('#deviceName').textContent=t('Hardware unavailable: {message}',{message:e.message});}await Promise.all([loadSettings(),loadModels(),loadExamples(),refreshStatus(),loadServe()]);restorePlaygroundLanguageDraft();})();
