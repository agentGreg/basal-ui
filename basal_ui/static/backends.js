'use strict';
let backendInventory=null, backendTimer=null, testingBackend=null;
const backendStatus={installed:'Installed',not_installed:'Not installed',needs_attention:'Needs attention',unavailable:'Unavailable on this machine'};
async function loadBackends(refresh=false){
  $('#refreshBackends').disabled=true;$('#backendError').hidden=true;
  if(!backendInventory)$('#backendInventory').textContent='Checking backend dependencies and devices…';
  try{
    backendInventory=await api(`/api/backends${refresh?'?refresh=true':''}`);
    const env=backendInventory.environment;
    $('#backendEnvironment').textContent=`${backendInventory.hardware.device_name} · Python ${env.version} · ${env.python}`;
    renderBackends();renderBackendTests(backendInventory.tests);
    scheduleBackendTests();
  }catch(e){$('#backendError').textContent=e.message;$('#backendError').hidden=false;}
  finally{$('#refreshBackends').disabled=false;}
}
function renderBackends(){
  if(!backendInventory)return;
  const entries=backendInventory.backends.filter(b=>b.compatible||$('#showUnavailableBackends').checked);
  $('#backendInventory').innerHTML=entries.map(b=>`<article class="backend-card"><div class="section-head"><h3>${esc(b.name)}</h3><span class="badge ${b.status==='installed'?'fit-fits':b.status==='needs_attention'?'fit-tight':'fit-unknown'}">${esc(backendStatus[b.status])}</span></div><p>${esc(b.reason)}</p><p class="muted">${Object.entries(b.versions).map(([p,v])=>`${esc(p)}: ${esc(v||'not installed')}`).join(' · ')}</p><div class="muted">Engine modes (availability depends on model and machine)</div><div class="chips">${b.modes.map(m=>`<span class="chip">${esc(m)}</span>`).join('')}</div><details><summary>Setup guidance</summary><p>${esc(b.setup.note)}</p>${b.setup.command?`<pre>${esc(b.setup.command)}</pre><button type="button" data-backend-copy="${esc(b.id)}">Copy setup command</button>`:''}<p><a href="${esc(b.documentation)}" target="_blank" rel="noopener">Backend documentation</a></p></details><div class="controls"><button type="button" data-backend-models="${esc(b.id)}" ${b.models.length?'':'disabled'}>View matching models (${b.models.length})</button><button type="button" data-backend-test="${esc(b.id)}" ${b.status==='installed'&&b.models.some(m=>m.downloaded)?'':'disabled'}>Test inference…</button></div>${b.compatible&&!b.models.some(m=>m.downloaded)?'<p class="muted">Download a compatible model from Models before testing.</p>':''}</article>`).join('');
}
$('#refreshBackends').onclick=()=>loadBackends(true);
$('#showUnavailableBackends').onchange=renderBackends;
$('#backendInventory').onclick=e=>{
  const copy=e.target.closest('[data-backend-copy]'),models=e.target.closest('[data-backend-models]'),test=e.target.closest('[data-backend-test]');
  if(copy){const b=backendInventory.backends.find(x=>x.id===copy.dataset.backendCopy);copyText(b.setup.command,'Setup command copied');}
  if(models){
    const b=backendInventory.backends.find(x=>x.id===models.dataset.backendModels);
    backendModelFilter={name:b.name,ids:new Set(b.models.map(m=>m.id))};
    $('#backendModelFilterNotice').hidden=false;$('#backendModelFilterNotice span').textContent=`Models compatible with ${b.name} after its dependencies are installed.`;
    setChecked($('#olderToggle'),true);setChecked($('#notRunnableToggle'),true);setChecked($('#downloadedToggle'),false);
    rerenderFilters();showView('models',true);selectModelsTab('all');
  }
  if(test)act(()=>openBackendTest(test.dataset.backendTest));
};
$('#clearBackendModelFilter').onclick=()=>{backendModelFilter=null;$('#backendModelFilterNotice').hidden=true;rerenderFilters();};
async function openBackendTest(id){
  testingBackend=backendInventory.backends.find(b=>b.id===id);
  const models=testingBackend.models.filter(m=>m.downloaded);
  $('#backendTestTitle').textContent=`Test ${testingBackend.name}`;
  $('#backendTestModel').innerHTML=models.map(m=>`<option value="${esc(m.id)}">${esc(m.id)}</option>`).join('');
  $('#backendTestError').hidden=true;$('#startBackendTest').disabled=!models.length;
  updateBackendTestModes();
  const state=await api('/api/status');
  $('#backendUnloadChoices').innerHTML=state.models.length?state.models.map(m=>{
    const idle=m.state==='ready'&&m.health?.reachable===true&&m.health.running===0&&m.health.queued===0;
    return `<label class="backend-unload"><input type="checkbox" value="${esc(m.id)}" data-generation="${esc(m.generation||'')}" ${idle?'':'disabled'}><span>${esc(m.id)} <small class="muted">${idle?'idle; rechecked before unloading':'busy or activity unknown'}</small></span></label>`;
  }).join(''):'<p class="muted">No running models.</p>';
  $('#backendTestDialog').showModal();
}
function updateBackendTestModes(){
  const model=testingBackend.models.find(m=>m.id===$('#backendTestModel').value);
  $('#backendTestMode').innerHTML=(model?.modes||[]).map(m=>`<option>${esc(m)}</option>`).join('');
  updateBackendEstimate();
}
function updateBackendEstimate(){
  const model=testingBackend.models.find(m=>m.id===$('#backendTestModel').value),needed=model?.mode_estimates?.[$('#backendTestMode').value];
  $('#backendTestEstimate').textContent=`Revision: ${model?.revision||'current downloaded snapshot (pinned at start)'} · Estimated memory: ${needed?gb(needed):'unknown — test unavailable'}. Startup may take up to 30 minutes for compilation; inference has a 2-minute deadline.`;
  $('#startBackendTest').disabled=!needed;
}
$('#backendTestModel').onchange=updateBackendTestModes;$('#backendTestMode').onchange=updateBackendEstimate;
$('#startBackendTest').onclick=async()=>{
  const unload=[...$('#backendUnloadChoices input:checked')].map(e=>({id:e.value,generation:e.dataset.generation}));
  if(unload.length&&!await confirmDialog({title:'Unload selected models for this test?',description:`${unload.map(m=>m.id).join(', ')} will remain stopped after the test. Use Restore to reload their previous configuration. Busy models will be refused.`,confirmLabel:'Unload and test'}))return;
  $('#startBackendTest').disabled=true;
  try{
    await api('/api/backends/tests',{method:'POST',body:JSON.stringify({backend:testingBackend.id,model:$('#backendTestModel').value,mode:$('#backendTestMode').value,unload})});
    $('#backendTestDialog').close();await pollBackendTests();
  }catch(e){$('#backendTestError').textContent=e.message;$('#backendTestError').hidden=false;}
  finally{$('#startBackendTest').disabled=false;}
};
function renderBackendTests(tests){
  $('#backendTests').innerHTML=tests.length?[...tests].reverse().map(t=>{
    const job=statusCache?.jobs?.[t.restoring?'backend-restore':'backend-test'];
    return `<article class="backend-card"><h4>${esc(t.backend)} · ${esc(t.mode)} · ${esc(t.model)}</h4><p>${t.status==='passed'?'Inference test passed':esc(t.status)}${t.status==='starting'&&job?.running?` · ${esc(job.phase)}`:''}${t.restoring?' · restoring models':''}</p>${t.result?`<p class="muted">${esc(t.result.elapsed_seconds)} seconds · revision ${esc(t.revision||'local snapshot')} · valid response; not an accuracy or performance certification</p>`:''}${t.error?`<p class="error-box">${esc(t.error)}</p>`:''}${t.stopped_models.length?`<p>Stopped models:</p><ul>${t.stopped_models.map(m=>`<li>${esc(m.id)} · ${esc(m.mode)}${m.error?` — ${esc(m.error)}`:''}</li>`).join('')}</ul>`:''}<div class="controls"><a href="#logs/job%3A${t.restoring?'backend-restore':'backend-test'}">Latest job logs</a>${t.status==='starting'||t.restoring?`<button type="button" data-backend-cancel="${esc(t.id)}">Cancel</button>`:''}${t.stopped_models.length&&t.status!=='starting'?`<button type="button" data-backend-restore="${esc(t.id)}" ${t.restoring?'disabled':''}>Restore stopped models</button>`:''}</div></article>`;
  }).join(''):'<p class="muted">No tests in this app session. Dependency checks do not verify inference.</p>';
}
$('#backendTests').onclick=e=>{
  const cancel=e.target.closest('[data-backend-cancel]'),restore=e.target.closest('[data-backend-restore]');
  if(cancel)act(async()=>{await api(`/api/backends/tests/${encodeURIComponent(cancel.dataset.backendCancel)}`,{method:'DELETE'});await pollBackendTests();});
  if(restore)act(async()=>{await api(`/api/backends/tests/${encodeURIComponent(restore.dataset.backendRestore)}/restore`,{method:'POST'});await pollBackendTests();});
};
function scheduleBackendTests(){clearTimeout(backendTimer);if(!$('#backends').hidden)backendTimer=setTimeout(pollBackendTests,2000);}
async function pollBackendTests(){
  try{const data=await api('/api/backends/tests');renderBackendTests(data.tests);}catch(e){$('#backendError').textContent=e.message;$('#backendError').hidden=false;}
  scheduleBackendTests();
}
window.addEventListener('backends:view',()=>backendInventory?pollBackendTests():loadBackends());
if(!$('#backends').hidden)loadBackends();
