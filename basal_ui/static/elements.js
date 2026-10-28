function updateMediaEvidence() {
  const disabled=selectedMedia.length>0;
  document.querySelectorAll('.question').forEach(card=>{
    const evidence=$('[data-f="evidence"]',card);
    if(disabled&&evidence.checked){evidence.checked=false;evidence.setAttribute('aria-checked','false');}
    evidence.disabled=disabled;
    evidence.title=disabled?t('Evidence spans are read from the state text; remove media to enable them'):'';
    let note=$('.media-evidence-note',card);
    if(disabled&&!note){note=document.createElement('small');note.className='muted media-evidence-note';note.textContent=t('Evidence spans are read from the state text.');evidence.closest('.qf-switch').after(note);}
    if(!disabled)note?.remove();
  });
}
function detachElementQuestion(name) {
  if(!name)return;
  const card=[...document.querySelectorAll('.question')].find(c=>$('.q-name',c).value===name);
  card?.remove();
  if($('#editJsonToggle').checked){
    try {const request=JSON.parse($('#requestJson').value);if(request.questions)delete request.questions[name];$('#requestJson').value=JSON.stringify(request,null,2);} catch {}
  }
}
function removeElementQuestion(item, sync=true) {
  detachElementQuestion(item.elements?.question);
  if(item.originalData!==undefined){
    URL.revokeObjectURL(item.url);
    item.data=item.originalData;item.file=item.originalFile;item.url=URL.createObjectURL(item.file);
  }
  delete item.originalData;delete item.originalFile;delete item.elements;
  renderSelectedMedia();if(sync)syncRequest();
}
function clearElementQuestions(items=selectedMedia) {
  for(const item of items){
    if(!item.elements)continue;
    detachElementQuestion(item.elements.question);delete item.elements;delete item.originalData;delete item.originalFile;
  }
}
function openElementsEditor(index) {
  const item=selectedMedia[index];if(!item||item.kind!=='image')return;
  if(selectedMedia.some((m,i)=>i!==index&&m.elements)){showMediaError(t('Only one image can have marked elements at a time'));return;}
  const dialog=document.createElement('dialog');dialog.className='elements-dialog';
  dialog.innerHTML=`<h2>${t('Mark elements')}</h2><p class="muted">${t('Drag to draw a box. Select, move, resize or remove boxes; labels are optional.')}</p><div class="elements-layout"><div class="elements-canvas-wrap"><canvas aria-label="${t('Element box editor')}"></canvas></div><div class="elements-side"><div class="elements-list"></div><p class="muted elements-error" role="alert"></p></div></div><div class="dialog-actions"><button type="button" class="elements-clear" data-variant="outline">${t('Clear all')}</button><span class="grow"></span><button type="button" class="elements-cancel" data-variant="outline">${t('Cancel')}</button><button type="button" class="elements-save" data-variant="primary">${t('Save')}</button></div>`;
  document.body.append(dialog);
  const canvas=$('canvas',dialog),ctx=canvas.getContext('2d'),list=$('.elements-list',dialog),error=$('.elements-error',dialog),save=$('.elements-save',dialog);canvas.hidden=true;save.disabled=true;
  const rawOriginal=item.originalData??item.data,match=rawOriginal.match(/^data:([^;]+);base64,(.*)$/),originalData=match?.[2]||rawOriginal,originalMime=item.originalFile?.type||match?.[1]||item.file.type||'image/png',originalBytes=Uint8Array.from(atob(originalData),x=>x.charCodeAt(0)),source=new Image(),sourceCard=item.elements?[...document.querySelectorAll('.question')].find(q=>$('.q-name',q).value===item.elements.question):null,oldModel=sourceCard?parseQuestionCard(sourceCard)[1]:null,boxes=Object.entries(item.elements?.boxes||{}).map(([key,rect])=>{const option=oldModel?.criteria?.[key],value=typeof option==='string'?option:option?.element,label=item.elements?.labels?.[key]||(typeof value==='string'?value.replace(/^\[\d+\]\s*/,''):'');return {rect:[...rect],label,key,option,label0:label};});
  // a box that keeps its number and label keeps its option as it was (a bundled example's role, checked, ...)
  const optionOf=(b,i)=>b.option!==undefined&&b.key===String(i+1)&&b.label.trim()===b.label0.trim()?b.option:`[${i+1}]${b.label.trim()?` ${b.label.trim()}`:''}`;
  let width=0,height=0,selected=-1,gesture=null;
  const imageData=()=>`data:${originalMime};base64,${originalData}`;
  const draw=()=>{ctx.clearRect(0,0,width,height);ctx.drawImage(source,0,0);boxes.forEach(({rect:[x,y,w,h]},i)=>{ctx.lineWidth=Math.max(2,width/500);ctx.strokeStyle='#e07b1f';ctx.strokeRect(x,y,w,h);const s=Math.max(20,Math.min(32,width/28)),by=Math.max(0,y-s);ctx.fillStyle='#e07b1f';ctx.fillRect(x,by,s,s);ctx.fillStyle='white';ctx.font=`bold ${s*.68}px sans-serif`;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(String(i+1),x+s/2,by+s/2);if(i===selected){ctx.fillStyle='#fff';ctx.strokeStyle='#e07b1f';for(const [hx,hy] of [[x,y],[x+w,y],[x,y+h],[x+w,y+h]]){ctx.beginPath();ctx.arc(hx,hy,Math.max(4,width/canvas.clientWidth*6),0,Math.PI*2);ctx.fill();ctx.stroke();}}});};  // the image, then each box with its badge where Save paints it
  const renderList=()=>{list.innerHTML=boxes.map((b,i)=>`<div class="element-label-row"><button type="button" data-select="${i}" aria-label="${t('Select element {number}',{number:i+1})}">[${i+1}]</button><input class="input" type="text" maxlength="80" data-label="${i}" aria-label="${t('Label for element {number}',{number:i+1})}" placeholder="${t('Optional label')}" value="${esc(b.label)}"><button type="button" data-delete="${i}" aria-label="${t('Remove element {number}',{number:i+1})}">×</button></div>`).join('');list.querySelectorAll('[data-select]').forEach(b=>b.onclick=()=>{selected=Number(b.dataset.select);draw();});list.querySelectorAll('[data-label]').forEach(input=>input.oninput=()=>{boxes[Number(input.dataset.label)].label=input.value;});list.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>{boxes.splice(Number(b.dataset.delete),1);selected=-1;renderList();draw();});};
  const point=e=>{const r=canvas.getBoundingClientRect();return [Math.max(0,Math.min(width,(e.clientX-r.left)*width/r.width)),Math.max(0,Math.min(height,(e.clientY-r.top)*height/r.height))];};
  const hit=(p)=>{const tol=Math.max(6,width/canvas.clientWidth*8);for(let i=boxes.length-1;i>=0;i--){const [x,y,w,h]=boxes[i].rect;for(const [corner,cx,cy] of [['nw',x,y],['ne',x+w,y],['sw',x,y+h],['se',x+w,y+h]])if(Math.hypot(p[0]-cx,p[1]-cy)<tol)return {i,corner};if(p[0]>=x&&p[0]<=x+w&&p[1]>=y&&p[1]<=y+h)return {i,move:true};}return null;};
  canvas.onpointerdown=e=>{const p=point(e),h=hit(p);if(h){selected=h.i;gesture={...h,start:p,rect:[...boxes[h.i].rect]};}else if(boxes.length>=26){error.textContent=t('You can mark up to 26 elements.');return;}else{selected=boxes.length;boxes.push({rect:[p[0],p[1],0,0],label:''});gesture={draw:true,start:p,rect:[p[0],p[1],0,0]};renderList();}canvas.setPointerCapture(e.pointerId);draw();};
  canvas.onpointermove=e=>{if(!gesture)return;const p=point(e),[sx,sy]=gesture.start;let [x,y,w,h]=gesture.rect;if(gesture.draw){x=Math.min(sx,p[0]);y=Math.min(sy,p[1]);w=Math.abs(p[0]-sx);h=Math.abs(p[1]-sy);}else if(gesture.move){x=Math.max(0,Math.min(width-w,x+p[0]-sx));y=Math.max(0,Math.min(height-h,y+p[1]-sy));}else{if(gesture.corner.includes('w')){w=Math.max(0,x+w-p[0]);x=Math.max(0,p[0]);}else w=Math.max(0,Math.min(width-x,p[0]-x));if(gesture.corner.includes('n')){h=Math.max(0,y+h-p[1]);y=Math.max(0,p[1]);}else h=Math.max(0,Math.min(height-y,p[1]-y));}boxes[selected].rect=[x,y,w,h];draw();};
  canvas.onpointerup=()=>{if(gesture?.draw&&Math.min(boxes[selected].rect[2],boxes[selected].rect[3])<6){boxes.splice(selected,1);selected=-1;renderList();}gesture=null;draw();};
  dialog.onkeydown=e=>{if((e.key==='Delete'||e.key==='Backspace')&&selected>=0&&e.target.tagName!=='INPUT'){boxes.splice(selected,1);selected=-1;renderList();draw();e.preventDefault();}};
  $('.elements-clear',dialog).onclick=()=>{boxes.splice(0);selected=-1;error.textContent='';renderList();draw();};
  $('.elements-cancel',dialog).onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove());
  $('.elements-save',dialog).onclick=async()=>{
    if(!boxes.length){removeElementQuestion(item);dialog.close();return;}
    const img=new Image();img.src=imageData();await img.decode();const out=document.createElement('canvas');out.width=width;out.height=height;const c=out.getContext('2d');c.drawImage(img,0,0);
    boxes.forEach(({rect:[x,y,w,h]},i)=>{c.strokeStyle='#e07b1f';c.lineWidth=Math.max(2,width/500);c.strokeRect(x,y,w,h);const s=Math.max(20,Math.min(32,width/28));const bx=x,by=Math.max(0,y-s);c.fillStyle='#e07b1f';c.beginPath();c.roundRect(bx,by,s,s,4);c.fill();c.fillStyle='#fff';c.font=`bold ${s*.68}px sans-serif`;c.textAlign='center';c.textBaseline='middle';c.fillText(String(i+1),bx+s/2,by+s/2);});
    const data=out.toDataURL('image/png').split(',')[1];
    const oldCard=sourceCard;
    const occupied=new Set([...document.querySelectorAll('.q-name')].filter(input=>input.closest('.question')!==oldCard).map(input=>input.value.trim()));
    let question=item.elements?.question;
    if(!question){question='element';for(let suffix=2;occupied.has(question);suffix++)question=`element${suffix}`;}
    oldCard?.remove();item.originalData=originalData;item.originalFile=item.originalFile||new File([originalBytes],item.file.name,{type:originalMime});const baseName=item.originalFile.name;const file=new File([Uint8Array.from(atob(data),x=>x.charCodeAt(0))],`${baseName.replace(/\.[^.]+$/,'')}-marked.png`,{type:'image/png'});item.data=data;item.file=file;URL.revokeObjectURL(item.url);item.url=URL.createObjectURL(file);
    item.elements={question,image:index,size:[width,height],boxes:Object.fromEntries(boxes.map((b,i)=>[String(i+1),b.rect.map(Math.round)])),labels:Object.fromEntries(boxes.map((b,i)=>[String(i+1),b.label.trim()]))};
    addQuestion(question,{type:'choice',instructions:oldModel?.instructions??t('Which element should be acted on next?'),criteria:Object.fromEntries(boxes.map((b,i)=>[String(i+1),optionOf(b,i)]))});
    const card=[...document.querySelectorAll('.question')].find(q=>$('.q-name',q).value===question);card._elementOwner=index;card._elementItem=item;card.dataset.elementOwner=index;$('.q-type',card).disabled=true;const options=$('[data-f="criteria"]',card);options.disabled=true;const note=document.createElement('small');note.className='muted';note.textContent=t('Edit options in Mark elements.');options.after(note);
    card.querySelector('.q-name').addEventListener('input',e=>{item.elements.question=e.target.value;});renderSelectedMedia();syncRequest();dialog.close();
  };
  source.onload=()=>{width=canvas.width=source.naturalWidth;height=canvas.height=source.naturalHeight;canvas.style.aspectRatio=`${width}/${height}`;canvas.hidden=false;save.disabled=false;renderList();draw();};source.src=imageData();dialog.showModal();
}

function validElementGeometry(elements) {
  const [width,height]=elements?.size||[];
  if(!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0||!elements.boxes||typeof elements.boxes!=='object'||!Object.keys(elements.boxes).length)return false;
  return Object.values(elements.boxes).every(rect=>Array.isArray(rect)&&rect.length===4&&rect.every(Number.isFinite)&&rect[0]>=0&&rect[1]>=0&&rect[2]>0&&rect[3]>0&&rect[0]+rect[2]<=width&&rect[1]+rect[3]<=height);
}
function elementPresetData() {
  return {mediaNames:selectedMedia.map(item=>item.file.name),marked:selectedMedia.flatMap((item,index)=>item.elements?[{index,...item.elements,markedData:item.data,originalData:item.originalData,originalName:item.originalFile?.name,originalMime:item.originalFile?.type,markedName:item.file.name}]:[])};
}
function restorePresetMediaFallback(request,names=[]) {
  const restored=[];
  try {
    for(const [index,item] of (request.media||[]).entries()){
      if(!item||!['image','audio'].includes(item.type)||typeof item.data!=='string')continue;
      const match=item.data.match(/^data:([^;]+);base64,(.*)$/),mime=match?.[1]||(item.type==='image'?'image/png':'audio/wav'),data=match?.[2]||item.data;
      if(!/^[A-Za-z0-9+/]+={0,2}$/.test(data))continue;
      const bytes=Uint8Array.from(atob(data),char=>char.charCodeAt(0)),file=new File([bytes],names[index]||`replay.${item.type==='image'?'png':'wav'}`,{type:mime});
      restored.push({kind:item.type,data,file,url:URL.createObjectURL(file)});
    }
  } catch { restored.forEach(item=>URL.revokeObjectURL(item.url));return false; }
  selectedMedia=restored;renderSelectedMedia();return true;
}
function restoreElementMarks(records) {
  const editingJson=$('#editJsonToggle').checked;
  let request=null;
  if(editingJson){try{const parsed=JSON.parse($('#requestJson').value);if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed))request=parsed;}catch{}}
  for(const record of records||[]){
    const item=selectedMedia[record.index];if(!item||item.kind!=='image'||(record.markedData&&record.markedData!==item.data))continue;
    const elements={question:record.question,image:record.index,size:record.size,boxes:record.boxes,labels:record.labels};
    if(!validElementGeometry(elements))continue;
    if(typeof record.originalData==='string'&&record.originalData){
      try {
        const bytes=Uint8Array.from(atob(record.originalData),x=>x.charCodeAt(0));
        item.originalData=record.originalData;item.originalFile=new File([bytes],record.originalName||'original.png',{type:record.originalMime||'image/png'});
      } catch { continue; }
    }
    const card=editingJson?null:[...document.querySelectorAll('.question')].find(q=>$('.q-name',q).value===record.question);
    if(editingJson&&request&&!Object.hasOwn(request.questions||{},record.question)){removeElementQuestion(item,false);continue;}
    if(!editingJson&&!card){removeElementQuestion(item,false);continue;}
    item.elements=elements;
    if(editingJson)continue;
    card._elementOwner=record.index;card._elementItem=item;card.dataset.elementOwner=record.index;$('.q-type',card).disabled=true;
    const options=$('[data-f="criteria"]',card);if(options){options.disabled=true;const note=document.createElement('small');note.className='muted';note.textContent=t('Edit options in Mark elements.');options.after(note);}
    $('.q-name',card).addEventListener('input',e=>{item.elements.question=e.target.value;});
  }
  renderSelectedMedia();updateMediaEvidence();if(!editingJson)syncRequest();
}
function syncElementIndices() {
  selectedMedia.forEach((item,index)=>{if(!item.elements)return;item.elements.image=index;const card=[...document.querySelectorAll('.question')].find(q=>$('.q-name',q).value===item.elements.question);if(card){card._elementOwner=index;card._elementItem=item;card.dataset.elementOwner=index;}});
}
