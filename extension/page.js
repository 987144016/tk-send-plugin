// Gmail DOM only. Never fetch Gmail internal endpoints, read cookies or focus a window.
export async function gmailPage(action,job){
  const origin=location.origin;
  const isMail=origin==='https://mail.google.com';
  const isAccount=origin==='https://accounts.google.com';
  if(!isMail&&!isAccount)return {error:'gmail_login_required'};
  // Gmail 的账号选择器在 accounts.google.com 的 iframe 里，主 frame 读不到它的 DOM。
  // 在 iframe 上下文只做一件事：点中目标账号行，然后交给 Gmail 自己切换。
  if(action==='switch_account'&&isAccount){
    const wanted=(job?.sender||'').toLowerCase();
    if(!wanted)return {error:'missing_sender'};
    const shown=e=>!!(e&&e.getClientRects().length);
    const rows=[...document.querySelectorAll('a,div[role="link"],div[role="button"],li,div[data-identifier],div[data-email],[data-email],[data-identifier],button,div[jsname]')].filter(shown);
    const blob=e=>((e.getAttribute('data-identifier')||'')+' '+(e.getAttribute('data-email')||'')+' '+(e.getAttribute('aria-label')||'')+' '+(e.getAttribute('href')||'')+' '+(e.innerText||'')).toLowerCase();
    const hit=rows.find(e=>blob(e).includes(wanted));
    if(!hit)return {waiting:true,stage:'账号选择器里没找到 '+wanted+'（若已登录请稍等再试）'};
    hit.click();
    return {waiting:true,account_row_clicked:true,stage:'已点击账号行：'+wanted};
  }
  const visible=e=>!!(e&&e.getClientRects().length);
  const buttons=root=>[...root.querySelectorAll('[role="button"],button')].filter(visible);
  const text=e=>(e?.innerText||e?.textContent||'').trim();
  if(action==='inspect_template_menu'){
    const nodes=[...document.querySelectorAll('[role="menu"],[role^="menuitem"],.J-M,.J-N,.J-N-Jz')].filter(visible);
    return {inspected:true,frame:location.origin+location.pathname,menu_count:nodes.length,
      menus:nodes.slice(0,50).map(e=>({tag:e.tagName,role:e.getAttribute('role'),
        class:e.className,text:text(e).slice(0,200),html:e.outerHTML.slice(0,900)}))};
  }
  const accountButton=[...document.querySelectorAll('[aria-label]')].find(e=>/Google (账号|Account)/i.test(e.getAttribute('aria-label')||'')&&/@/.test(e.getAttribute('aria-label')));
  const sender=accountButton?.getAttribute('aria-label')?.match(/[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@gmail\.com/i)?.[0]?.toLowerCase();
  if(action==='switch_account'){
    // handled below — do not hard-fail before human switch simulation
  } else if(action==='scan_inbox'){
    if(!sender)return {waiting:true,stage:'等待 Gmail 当前账号显示（收件箱扫描前）',replies_found:false,replies:[]};
    // Must still be on sending mailbox — never switch before scan.
    if(job?.sender && sender!==job.sender.toLowerCase())
      return {error:'wrong_gmail_account_for_inbox_scan',current:sender,wanted:job.sender.toLowerCase()};
  } else {
    if(!sender)return {waiting:true,stage:'等待 Gmail 当前账号显示'};
    if(sender!==job.sender.toLowerCase())return {error:'wrong_gmail_account',need_switch:true,current:sender,wanted:job.sender.toLowerCase()};
  }
  const alerts=[...document.querySelectorAll('[role="alert"],[role="alertdialog"]')].map(text).join('\n');
  if(/reached.*limit.*sending|sending limit|已达到发送.*上限|已达到.*发送.*限额/i.test(alerts))return {error:'gmail_limit'};
  if(action==='read_reply'){
    const id=(job?.message_id||'').toLowerCase();
    if(!/^[a-f0-9]{10,40}$/.test(id))return {error:'reply_message_id_missing'};
    const message=[...document.querySelectorAll('[data-legacy-message-id]')]
      .find(el=>(el.getAttribute('data-legacy-message-id')||'').toLowerCase()===id);
    if(!message)return {waiting:true,stage:'等待 Gmail 回信正文显示'};
    const from=(message.querySelector('[email]')?.getAttribute('email')||'').toLowerCase();
    if(from!==(job?.recipient||'').toLowerCase())return {error:'wrong_reply_sender'};
    const body=(message.querySelector('.a3s')?.innerText||'').trim();
    if(!body)return {waiting:true,stage:'等待 Gmail 回信正文加载'};
    return {verified:true,from,message_id:id,body};
  }
  if(action==='restore_unread'){
    const control=[...document.querySelectorAll('[role="button"][aria-label]')]
      .find(el=>/^(标记为未读|Mark as unread)$/i.test(el.getAttribute('aria-label')||''));
    if(!control)return {error:'mark_unread_control_missing'};
    control.click();
    return {requested:true};
  }
  const digest=async s=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(n=>n.toString(16).padStart(2,'0')).join('');
  const set=(e,value)=>{const setter=Object.getOwnPropertyDescriptor(e instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set;setter.call(e,value);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));};
  const compose=[...document.querySelectorAll('[role="dialog"]')].find(e=>e.querySelector('input[name="subjectbox"]'));
  const addressList=dialog=>[...new Set([...dialog.querySelectorAll('[email],[data-email],[data-hovercard-id]')].map(e=>e.getAttribute('email')||e.getAttribute('data-email')||e.getAttribute('data-hovercard-id')).filter(e=>/^[^\s@]+@[^\s@]+$/.test(e)).map(e=>e.toLowerCase()))];
  const bulk=job.recipient_mode==='bcc';
  const expected=bulk?(job.recipients||[]).map(x=>x.toLowerCase()):[(job.recipient||'').toLowerCase()];
  if(bulk&&(expected.length<1||expected.length>100||new Set(expected).size!==expected.length))return {error:'invalid_bcc_round'};
  const field=(root,kind)=>root?.querySelector('input[name="'+kind+'"],textarea[name="'+kind+'"]')||[...(root?.querySelectorAll('input[role="combobox"],input[peoplekit-id]')||[])].find(e=>{
    const label=e.getAttribute('aria-label')||'';
    return kind==='bcc'?/密送|Bcc/i.test(label):kind==='cc'?/抄送|^Cc\b/i.test(label):/收件人|^To\b/i.test(label)&&!/密送|抄送|Bcc|^Cc\b/i.test(label);
  });
  const fieldAddresses=(dialog,kind)=>{
    const input=field(dialog,kind);if(!input)return [];
    let row=input;
    while(row.parentElement&&row.parentElement!==dialog){
      const parent=row.parentElement;
      if(['to','cc','bcc'].some(other=>other!==kind&&field(parent,other)))break;
      row=parent;
    }
    return addressList(row);
  };
  const envelope=dialog=>{
    const expanded={to:fieldAddresses(dialog,'to'),cc:fieldAddresses(dialog,'cc'),bcc:fieldAddresses(dialog,'bcc')};
    const summaries=[...dialog.querySelectorAll('div.aoD.hl[tabindex="1"]')].filter(visible);
    if(summaries.length===1){
      const summary=summaries[0],label=text(summary);
      const displayed=addressList(summary),all=addressList(dialog);
      // A collapsed summary is proof only if it explicitly names BCC and
      // contains every recipient shown anywhere in this compose window.
      if(/^(密送|Bcc)\s*[:：]/i.test(label)&&displayed.length&&displayed.length===all.length&&displayed.every(a=>all.includes(a)))
        return {to:[],cc:[],bcc:displayed};
    }
    return expanded;
  };
  const pendingAddresses=dialog=>['to','cc','bcc'].some(kind=>(field(dialog,kind)?.value||'').trim());
  const matches=(actual,wanted)=>actual.length===wanted.length&&[...actual].sort().every((x,i)=>x===[...wanted].sort()[i]);
  
  if(action==='switch_account'){
    // 影刀-style: human DOM clicks only (avatar → account row). NEVER authuser/URL navigate (ban risk). Same tab.
    const wanted=(job.sender||'').toLowerCase();
    if(!wanted)return {error:'missing_sender'};
    const sleep=ms=>new Promise(r=>setTimeout(r,ms+(Math.random()*40|0)));
    const humanClick=async el=>{
      if(!el)return false;
      try{el.scrollIntoView({block:'center',inline:'nearest'});}catch{}
      await sleep(60);
      const r=el.getBoundingClientRect();
      const x=Math.max(2,Math.min(window.innerWidth-2,r.left+r.width/2+(Math.random()*6-3)));
      const y=Math.max(2,Math.min(window.innerHeight-2,r.top+r.height/2+(Math.random()*6-3)));
      const base={bubbles:true,cancelable:true,view:window,clientX:x,clientY:y,screenX:x,screenY:y,button:0,buttons:1};
      for(const t of ['pointerover','mouseover','pointerenter','mouseenter'])el.dispatchEvent(new MouseEvent(t,{...base,buttons:0}));
      await sleep(35);
      el.dispatchEvent(new PointerEvent('pointerdown',{...base,pointerId:1,pointerType:'mouse',isPrimary:true}));
      el.dispatchEvent(new MouseEvent('mousedown',base));
      await sleep(55+Math.random()*90);
      el.dispatchEvent(new PointerEvent('pointerup',{...base,pointerId:1,pointerType:'mouse',isPrimary:true,buttons:0}));
      el.dispatchEvent(new MouseEvent('mouseup',{...base,buttons:0}));
      try{el.click();}catch{}
      return true;
    };
    const blobOf=e=>((e.getAttribute('aria-label')||'')+' '+(e.getAttribute('data-email')||'')+' '+(e.getAttribute('data-identifier')||'')+' '+(e.getAttribute('data-account-email')||'')+' '+(e.getAttribute('href')||'')+' '+(e.innerText||'')).toLowerCase();
    const findAccountButton=()=>[...document.querySelectorAll('a[aria-label],button[aria-label],img[aria-label],div[aria-label],[aria-label]')].find(e=>{
      const lab=e.getAttribute('aria-label')||'';
      return /Google (账号|Account)/i.test(lab)&&/@/.test(lab)&&visible(e);
    })||[...document.querySelectorAll('a[href*="SignOutOptions"],a[href*="accounts.google.com"],a[aria-label*="@gmail.com"]')].find(visible);
    const accountButton=findAccountButton();
    const sender=accountButton?.getAttribute('aria-label')?.match(/[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@gmail\.com/i)?.[0]?.toLowerCase()
      ||[...document.querySelectorAll('[data-email],[data-identifier]')].map(e=>(e.getAttribute('data-email')||e.getAttribute('data-identifier')||'').toLowerCase()).find(x=>x.includes('@gmail.com'));
    if(sender===wanted){
      document.documentElement.dataset.gmwSwitchTries='0';
      document.documentElement.dataset.gmwSwitchPhase='';
      return {switched:true,sender,stage:'已在目标 Gmail 账号'};
    }
    // 1) Prefer already-open account picker / list row (AccountChooser / gb menu)
    const rowSel='a,div[role="link"],div[role="button"],li,div[data-identifier],div[data-email],[data-email],[data-identifier],div[role="menuitem"],button,div[jsname]';
    const candidates=[...document.querySelectorAll(rowSel)].filter(visible);
    const exact=candidates.find(e=>
      (e.getAttribute('data-email')||e.getAttribute('data-identifier')||'').toLowerCase()===wanted);
    const hit=exact||candidates.find(e=>{
      const b=blobOf(e);
      if(!b.includes(wanted))return false;
      // Prefer the row that looks like an account entry, not the page chrome
      const id=(e.getAttribute('data-identifier')||e.getAttribute('data-email')||'').toLowerCase();
      if(id===wanted)return true;
      if(/@gmail\.com/.test(b)&&b.includes(wanted.split('@')[0]))return true;
      return b.includes(wanted);
    });
    if(hit){
      const link=hit.closest('a');
      if(link?.target==='_blank')link.target='_self';
      await humanClick(hit);
      document.documentElement.dataset.gmwSwitchPhase='clicked_row';
      return {waiting:true,account_row_clicked:true,stage:'影刀式点击账号行 → '+wanted};
    }
    // 2) Open avatar / Google Account menu if picker not showing target
    const tries=Number(document.documentElement.dataset.gmwSwitchTries||'0');
    const phase=document.documentElement.dataset.gmwSwitchPhase||'';
    if(accountButton && tries<6){
      document.documentElement.dataset.gmwSwitchTries=String(tries+1);
      document.documentElement.dataset.gmwSwitchPhase='open_avatar';
      await humanClick(accountButton);
      return {waiting:true,stage:'影刀式点击头像打开账号列表（'+wanted+'）'};
    }
    // 3) No authuser / URL switch — stay on same tab and ask human if DOM path failed
    // 页面还在加载时不要急着判“没有头像”，否则会连触发重载。
    if(!sender){
      if(document.readyState!=='complete')return {waiting:true,stage:'等待 Gmail 页面加载完成（'+document.readyState+'）'};
      return {waiting:true,need_inbox_reload:true,stage:'等待 Gmail 账号头像显示：'+wanted};
    }
    return {error:'wrong_gmail_account: 当前 '+(sender||'?')+'，需要 '+wanted+'（请确认账号选择器里能点到该行；已禁用 authuser URL 切换）',phase,tries};
  }

  if(action==='prepare'){
    if(!compose){const button=buttons(document).find(e=>/^(写邮件|撰写|Compose)$/i.test(text(e)));if(!button)return {waiting:true,stage:'等待 Gmail 写信按钮'};button.click();return {waiting:true,stage:'正在打开 Gmail 写信窗口'};}
    const subject=compose.querySelector('input[name="subjectbox"]');
    const bodyFields=[...compose.querySelectorAll('[contenteditable="true"][role="textbox"], [contenteditable="true"][aria-label="邮件正文"], [contenteditable="true"][aria-label="Message Body"]')];
    const body=bodyFields.find(visible)||bodyFields[0];
    const to=compose.querySelector('input[name="to"],textarea[name="to"],input[role="combobox"][aria-label*="收件"],input[role="combobox"][aria-label*="To"],input[peoplekit-id]');
    if(!body)return {waiting:true,stage:'等待 Gmail 邮件正文控件'};
    if(!bulk&&!to)return {waiting:true,stage:'等待 Gmail 收件人控件'};
    if(compose.dataset.gmwJob===job.job_id&&!visible(body)){
      const restore=buttons(compose).filter(e=>/^(最大化|Maximize)$/i.test(e.getAttribute('aria-label')||''));
      if(restore.length!==1)return {waiting:true,stage:'正在等待本任务的 Gmail 草稿展开'};
      const tries=Number(compose.dataset.gmwRestoreTries||'0');
      if(tries>=2){
        const close=compose.querySelector('[aria-label="保存并关闭"],[aria-label="Save & close"],[data-tooltip="Save & close"]');
        if(close&&!compose.dataset.gmwRestoreClose&&!compose.dataset.gmwClicked){
          compose.dataset.gmwRestoreClose='1';
          close.click();
          return {waiting:true,stage:'无法展开草稿，已保存并关闭；准备新写信窗口'};
        }
        return {error:'gmail_draft_restore_failed'};
      }
      compose.dataset.gmwRestoreTries=String(tries+1);
      restore[0].click();
      return {waiting:true,stage:'已点击最大化，等待本任务的 Gmail 草稿展开'};
    }
    if(compose.dataset.gmwJob===job.job_id)delete compose.dataset.gmwRestoreTries;
    const previous=compose.dataset.gmwJob;
    if(previous&&previous!==job.job_id&&(job.abandoned_test_drafts||[]).includes(previous)&&!compose.dataset.gmwClicked&&text(body).includes('TK-'+previous)&&subject.value===job.subject&&addressList(compose).length===1&&addressList(compose)[0]===job.recipient.toLowerCase()){
      const close=compose.querySelector('[aria-label="保存并关闭"],[aria-label="Save & close"],[data-tooltip="Save & close"]');
      if(!close)return {error:'previous_test_draft_close_unavailable'};
      close.click();return {waiting:true,closed_previous:true,stage:'已保存上次未发送的测试草稿，正在准备本次测试'};
    }
    if(!compose.dataset.gmwJob){
      const existingAddrs=addressList(compose);
      const hasContent=!!(subject.value||text(body)||existingAddrs.length);
      if(hasContent){
        const recipOk=!bulk && existingAddrs.map(a=>a.toLowerCase()).includes((job.recipient||'').toLowerCase());
        const subjOk=!subject.value || subject.value===job.subject;
        // Matching leftover from aborted mid-compose (e.g. forbidden-template): claim and send.
        if(recipOk && subjOk && text(body).length>20){
          compose.dataset.gmwJob=job.job_id;
          if(!subject.value) set(subject,job.subject);
          return {waiting:true,stage:'接管已填好的匹配草稿，准备发送'};
        }
        // Stale unrelated draft: discard/close, never abandon the job.
        const discard=[...compose.querySelectorAll('[aria-label],[data-tooltip]')].find(e=>{
          const a=(e.getAttribute('aria-label')||'')+' '+(e.getAttribute('data-tooltip')||'');
          return /丢弃草稿|Discard draft/i.test(a);
        });
        const close=compose.querySelector('[aria-label="保存并关闭"],[aria-label="Save & close"],[data-tooltip="Save & close"]')
          || [...compose.querySelectorAll('[aria-label],[data-tooltip]')].find(e=>{
            const a=(e.getAttribute('aria-label')||'')+' '+(e.getAttribute('data-tooltip')||'');
            return /保存并关闭|Save & close/i.test(a);
          });
        const btn=discard||close;
        if(!btn) return {waiting:true,stage:'发现无关草稿，等待关闭控件'};
        btn.click();
        return {waiting:true,closed_stale:true,stage:'已关闭无关草稿，继续准备本次发送'};
      }
      compose.dataset.gmwJob=job.job_id;
      if(!bulk){set(to,job.recipient);to.focus();
      for(const type of ['keydown','keypress','keyup'])to.dispatchEvent(new KeyboardEvent(type,{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));}
      set(subject,job.subject);
      if(job.gmail_template_name)return {waiting:true,stage:'正在调用 Gmail 已保存模板：'+job.gmail_template_name};
      if(job.native_nodes){
        const build=node=>{if(typeof node==='string')return document.createTextNode(node);const el=document.createElement(node.tag);for(const [key,value] of Object.entries(node.attrs))el.setAttribute(key,value);el.append(...node.children.map(build));return el;};
        body.replaceChildren(...job.native_nodes.map(build));
        body.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertFromPaste'}));
      }else{
      const parsed=new DOMParser().parseFromString(job.html,'text/html');
      for(const node of parsed.querySelectorAll('script,iframe,object,embed,form,meta,link'))node.remove();
      for(const node of parsed.querySelectorAll('*'))for(const a of [...node.attributes])if(/^on/i.test(a.name)||/^(href|src)$/i.test(a.name)&&/^\s*javascript:/i.test(a.value))node.removeAttribute(a.name);
      body.focus();document.execCommand('insertHTML',false,parsed.body.innerHTML);body.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertFromPaste'}));
      }
      // Ask Gmail's normal paste handler to upload each inline picture. Leave a
      // range at its original location; never silently send data-URI placeholders.
      let index=0;
      for(const img of job.native_images?[]:[...body.querySelectorAll('img[src^="data:image/"]')]){
        const uri=img.getAttribute('src'),match=uri.match(/^data:(image\/(?:png|jpeg|gif|webp));base64,(.+)$/s);
        if(!match)return {error:'unsupported_inline_image'};
        const bytes=Uint8Array.from(atob(match[2]),c=>c.charCodeAt(0));
        const range=document.createRange();range.selectNode(img);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
        const transfer=new DataTransfer();transfer.items.add(new File([bytes],'inline-'+index+++'.'+match[1].split('/')[1],{type:match[1]}));
        body.dispatchEvent(new ClipboardEvent('paste',{clipboardData:transfer,bubbles:true,cancelable:true}));
      }
      return {waiting:true};
    }
    if(compose.dataset.gmwJob!==job.job_id){
      // Different job claimed this compose earlier; close and let next tick reopen clean.
      const close=compose.querySelector('[aria-label="保存并关闭"],[aria-label="Save & close"],[data-tooltip="Save & close"]');
      if(close && !compose.dataset.gmwClicked){ close.click(); return {waiting:true,stage:'关闭他任务草稿后继续'}; }
      return {waiting:true,stage:'等待他任务草稿关闭'};
    }
    if(bulk){
      const bcc=field(compose,'bcc');
      if(!bcc||!visible(bcc)){
        const proof=envelope(compose);
        if(matches(proof.bcc,expected)&&!proof.to.length&&!proof.cc.length&&!pendingAddresses(compose)){
          // Gmail collapses the filled recipient area after template insertion.
          // Keep the verified envelope; no reopening or simulated mouse is needed.
        }else{
        const collapsed=[...compose.querySelectorAll('div.aoD.hl[tabindex="1"]')].filter(visible);
        if(collapsed.length===1){
          const header=collapsed[0];header.focus();
          const r=header.getBoundingClientRect();const target=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
          if(target&&(target===header||header.contains(target)))target.click();
          return {waiting:true,stage:'正在展开 Gmail 收件人区域'};
        }
        const controls=[...compose.querySelectorAll('span,a,button,[role="button"],[role="link"]')].filter(e=>visible(e)&&/^(密送|Bcc)$/i.test(text(e))&&!Array.from(e.children).some(c=>text(c)===text(e)));
        if(controls.length===1)controls[0].click();
        return {waiting:true,stage:'正在展开 Gmail 密送栏，尚未发送'};
        }
      }
      const addresses=envelope(compose);
      if(addresses.to.length||addresses.cc.length||(field(compose,'to')?.value||'').trim()||(field(compose,'cc')?.value||'').trim())return {error:'bcc_public_recipient_not_empty'};
      if(addresses.bcc.some(a=>!expected.includes(a)))return {error:'bcc_unexpected_recipient'};
      if(!matches(addresses.bcc,expected)){
        if(!bcc||!visible(bcc))return {error:'bcc_field_unavailable'};
        const remaining=expected.filter(a=>!addresses.bcc.includes(a));
        const pending=(bcc.value||'').trim().split(/[,;\s]+/).filter(Boolean).map(a=>a.toLowerCase());
        if(pending.some(a=>!remaining.includes(a)))return {error:'bcc_unexpected_pending_recipient'};
        // Follow Gmail's batch-paste handler, as used by the established RPA
        // flow, without changing the user's system clipboard.
        set(bcc,'');bcc.focus();
        const transfer=new DataTransfer();transfer.setData('text/plain',remaining.join(', '));
        const handled=!bcc.dispatchEvent(new ClipboardEvent('paste',{clipboardData:transfer,bubbles:true,cancelable:true}));
        if(!handled){
          set(bcc,remaining.join(', '));
          for(const type of ['keydown','keypress','keyup'])bcc.dispatchEvent(new KeyboardEvent(type,{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));
        }
        return {waiting:true,stage:'正在批量确认本轮'+expected.length+'位密送收件人'};
      }
      if(pendingAddresses(compose))return {error:'bcc_uncommitted_recipient'};
    }
    const recipients=addressList(compose);
    if(!matches(recipients,expected))return {waiting:true,stage:bulk?('等待 Gmail 确认'+expected.length+'位密送收件人'):'等待 Gmail 确认唯一收件人'};
    if(job.gmail_template_name){
      if(!compose.dataset.gmwTemplateRequested){
        // Gmail also renders saved-template rows as .J-N without role=menuitem.
        const items=[...new Set(document.querySelectorAll('[role="menuitem"],[role="menuitemcheckbox"],[role="menuitemradio"],.J-M .J-N,[role="menu"] .J-N'))].filter(visible);
        const normalize=s=>String(s||'').replace(/\s+/g,' ').trim();
        const requested=normalize(job.gmail_template_name);
        const labels=e=>[
          text(e), e.getAttribute('title'), e.getAttribute('aria-label'),
          e.getAttribute('data-tooltip'),
          ...[...e.querySelectorAll('[title],[aria-label]')].flatMap(child=>[
            child.getAttribute('title'),child.getAttribute('aria-label')]),
        ].map(normalize).filter(Boolean);
        const exact=items.filter(e=>labels(e).includes(requested));
        const shortened=exact.length?[]:items.filter(e=>labels(e).some(label=>{
          const match=label.match(/^(.*?)(?:\.\.\.|…)$/);
          const prefix=match?.[1]?.trim()||'';
          return prefix.length>=12&&requested.startsWith(prefix);
        }));
        const names=exact.length?exact:shortened;
        if(names.length>1)return {error:'ambiguous_saved_template'};
        if(!names.length&&items.some(e=>/^(插入模板|Insert template)$/i.test(text(e)))&&
            items.some(e=>! /^(插入模板|Insert template)$/i.test(text(e))))
          return {error:'gmail_template_not_found'};
        let target=names[0],step='choose';
        if(!target){target=items.find(e=>/^(模板|Templates)(?:\s*[►▶▸›])?$/i.test(text(e)));step='templates';}
        if(!target){target=[...compose.querySelectorAll('[aria-label="更多选项"],[aria-label="More options"]')].find(visible);step='more';}
        if(!target)return {waiting:true,stage:'正在等待 Gmail 模板菜单加载'};
        if(step==='choose'){
          compose.dataset.gmwTemplateRequested=job.gmail_template_name;
          delete compose.dataset.gmwTemplateHoverTries;
        }
        for(const old of document.querySelectorAll('[data-gmw-target]'))
          if(old.dataset.gmwTarget===job.job_id+'-'+step)delete old.dataset.gmwTarget;
        target.dataset.gmwTarget=job.job_id+'-'+step;
        // These are verified navigation controls, never the send button. DOM
        // activation avoids waiting for animation frames in a background tab.
        {
          const rect=target.getBoundingClientRect();
          const mouse={bubbles:true,cancelable:true,view:window,button:0,clientX:rect.x+rect.width/2,clientY:rect.y+rect.height/2};
          target.dispatchEvent(new MouseEvent('mouseover',{...mouse,buttons:0}));
          target.dispatchEvent(new MouseEvent('mouseenter',{...mouse,bubbles:false,buttons:0}));
          target.dispatchEvent(new MouseEvent('mousemove',{...mouse,buttons:0}));
          if(step!=='templates'){
            target.dispatchEvent(new MouseEvent('mousedown',{...mouse,buttons:1}));
            target.dispatchEvent(new MouseEvent('mouseup',{...mouse,buttons:0}));
          }
        }
        if(step==='templates'){
          const tries=Number(compose.dataset.gmwTemplateHoverTries||'0')+1;
          compose.dataset.gmwTemplateHoverTries=String(tries);
          if(tries>=3) target.click();
        }else target.click();
        return {waiting:true,stage:step==='more'?'正在打开 Gmail 更多选项':step==='templates'?'正在打开 Gmail 模板菜单':'正在插入 Gmail 已保存模板：'+job.gmail_template_name};
      }
      if(text(body).length<20)return {waiting:true,stage:'正在等待 Gmail 已保存模板加载完成'};
      set(subject,job.subject);
      if(!text(body).includes(job.marker)){
        const reference=document.createElement('div');reference.textContent='Reference: '+job.marker;
        reference.style.cssText='font-size:10px;color:#888;margin-top:20px';body.append(reference);
        body.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));
      }
      if(compose.querySelector('[aria-label="移除附件"],[aria-label="Remove attachment"]'))return {error:'unexpected_attachment_in_saved_template'};
    }
    if(subject.value!==job.subject||!text(body).includes(job.marker))return {error:'compose_content_mismatch'};
    const imgs=[...body.querySelectorAll('img')];
    const accountIndex=location.pathname.match(/^\/mail\/u\/(\d+)(?:\/|$)/)?.[1];
    let corrected=0;
    if(job.gmail_template_name&&accountIndex){
      for(const img of imgs){
        let url;try{url=new URL(img.src,location.href);}catch{continue;}
        const sourceIndex=url.pathname.match(/^\/mail\/u\/(\d+)$/)?.[1];
        if(url.origin!==location.origin||!sourceIndex||sourceIndex===accountIndex||
           url.searchParams.get('view')!=='fimg'||!url.searchParams.has('permmsgid'))continue;
        url.pathname='/mail/u/'+accountIndex;
        img.src=url.href;corrected++;
      }
    }
    if(corrected){
      body.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertReplacementText'}));
      return {waiting:true,stage:'正在让 Gmail 重新加载模板图片',diagnostics:{
        image_count:imgs.length,expected_count:job.inline_image_count,corrected_count:corrected}};
    }
    if(imgs.some(i=>/^(data|blob):/.test(i.src)||!i.complete||i.complete&&!i.naturalWidth)||imgs.length<job.inline_image_count)
      return {waiting:true,stage:'等待 Gmail 上传内嵌图片',diagnostics:{
        image_count:imgs.length,expected_count:job.inline_image_count,
        incomplete_count:imgs.filter(i=>!i.complete).length,
        data_blob_count:imgs.filter(i=>/^(data|blob):/.test(i.src)).length,
        broken_count:imgs.filter(i=>i.complete&&!i.naturalWidth).length}};
    if(compose.querySelector('[role="progressbar"]'))return {waiting:true,stage:'等待附件或图片上传结束'};
    compose.dataset.gmwHash=await digest(body.innerHTML);
    return {prepared:true,sender};
  }
  if(action==='send'){
    if(!compose||compose.dataset.gmwJob!==job.job_id)return {error:'prepared_draft_missing'};
    const body=[...compose.querySelectorAll('[contenteditable="true"][role="textbox"]')].find(visible);
    const recipients=addressList(compose);
    const addressProof=bulk?envelope(compose):null;
    if(!body||compose.dataset.gmwHash!==await digest(body.innerHTML)||compose.querySelector('input[name="subjectbox"]').value!==job.subject||!matches(recipients,expected)||bulk&&(pendingAddresses(compose)||addressProof.to.length||addressProof.cc.length||!matches(addressProof.bcc,expected)))return {error:'compose_changed_before_click'};
    if(compose.dataset.gmwClicked)return {error:'already_clicked'};
    const send=buttons(compose).find(e=>/^(发送|Send)(\s|$)/.test(text(e))&&!/稍后|Schedule/.test(text(e)));
    if(!send||send.getAttribute('aria-disabled')==='true')return {error:'send_button_unavailable'};
    compose.dataset.gmwClicked='1';send.click();return {clicked:true};
  }
  if(action==='submitted')return {submitted:!compose&&/已发送|邮件已发送|Message sent/i.test(document.body.innerText),sender};
  if(action==='snapshot'){
    const body=[...(compose?.querySelectorAll('[contenteditable="true"][role="textbox"]')||[])].find(visible);
    if(!body||compose.dataset.gmwJob!==job.job_id||compose.dataset.gmwHash!==await digest(body.innerHTML))return {error:'prepared_snapshot_changed'};
    const addressProof=bulk?envelope(compose):null;
    if(bulk&&(pendingAddresses(compose)||addressProof.to.length||addressProof.cc.length||!matches(addressProof.bcc,expected)))return {error:'bcc_envelope_changed'};
    return {snapshot:{template_name:job.gmail_template_name,subject:compose.querySelector('input[name="subjectbox"]').value,html:body.innerHTML,sha256:await digest(body.innerHTML),...(bulk?{recipient_mode:'bcc',recipients:addressProof.bcc,to:addressProof.to,cc:addressProof.cc}:{})}};
  }
  if(action==='receipt'){
    const rows=[...document.querySelectorAll('tr.zA,[role="main"] tr[role="row"]')].filter(visible);
    if(rows.length===1){rows[0].click();return {waiting:true};}
    if(rows.length>1)return {error:'ambiguous_sent_matches'};
    const messages=[...document.querySelectorAll('[data-legacy-message-id],[data-message-id]')].filter(e=>visible(e)&&text(e).includes(job.marker));
    // Prefer the actual message container, not a parent covering a whole thread.
    const message=messages.find(e=>e.matches('.adn'))||messages[messages.length-1];
    if(!message)return {waiting:true};
    const headerSender=message.querySelector('.gD[email]')?.getAttribute('email')?.toLowerCase();
    const recipients=[...message.querySelectorAll('.g2[email]')].map(e=>e.getAttribute('email').toLowerCase());
    if(headerSender!==sender)return {error:'sent_headers_not_verified'};
    if(bulk&&!matches([...new Set(recipients)],expected)){
      const details=[...message.querySelectorAll('[aria-label],[data-tooltip]')].find(e=>visible(e)&&/显示详细信息|Show details/i.test(e.getAttribute('aria-label')||e.getAttribute('data-tooltip')||''));
      if(details)details.click();
      return {waiting:true};
    }
    if(!bulk&&!recipients.includes(job.recipient.toLowerCase()))return {error:'sent_headers_not_verified'};
    const hex=value=>{if(/^[a-f0-9]{10,40}$/.test(value||''))return value;const d=value?.match(/(?:msg|thread)-[fa]:(\d+)/)?.[1];return d?BigInt(d).toString(16):null;};
    const message_id=hex(message.getAttribute('data-legacy-message-id'))||hex(message.getAttribute('data-message-id'));
    const main=message.closest('[role="main"]');
    const threadIds=[...new Set([...(main?.querySelectorAll('[data-legacy-thread-id],[data-thread-id]')||[])].filter(visible).map(e=>hex(e.getAttribute('data-legacy-thread-id'))||hex(e.getAttribute('data-thread-id'))).filter(Boolean))];
    const thread_id=threadIds.length===1?threadIds[0]:null;
    if(!message_id||!thread_id)return {error:'sent_ids_not_verified'};
    return {status:'sent',sender,recipient:job.recipient,marker:job.marker,message_id,thread_id,...(bulk?{recipient_mode:'bcc',recipients:[...new Set(recipients)]}:{})};
  }
  if(action==='scan_inbox'){
    // Programmatic scan of already-logged-in Gmail inbox (no OAuth API).
    // Must run on the mailbox that just sent — BEFORE any account switch.
    const wanted=(job.sender||'').toLowerCase();
    if(sender && wanted && sender!==wanted)
      return {error:'wrong_gmail_account_for_inbox_scan',current:sender,wanted};
    const sleep=ms=>new Promise(r=>setTimeout(r,ms));
    // A thread, All Mail or a search can leave partial rows in the DOM.
    // Scan only the actual inbox list for this Gmail tab.
    const hash=(location.hash||'').toLowerCase();
    if(hash!=='#inbox'){
      const url=new URL(location.href);
      url.hash='inbox';
      location.href=url.href;
      return {waiting:true,stage:'打开收件箱以扫描回复',replies_found:false,replies:[]};
    }
    const rows=[...document.querySelectorAll('tr.zA,[role="main"] tr[role="row"]')].filter(visible);
    const recipient=(job.recipient||'').toLowerCase();
    const marker=job.marker||'';
    const replies=[];
    const inboxRows=[];
    // 不再只盯“刚发那一封”的收件人：把收件箱首屏所有非本人的行都带回去，
    // 由服务端去匹配“这封是谁的回复/退信”。
    // Gmail 会把部分达人回信标成“可疑/钓鱼”。这类邮件仍然是回复，不能当没看到，
    // 只是记录时打上 suspicious 标记（换模板即可，不影响后续发送）。
    const suspiciousRe=/可疑|钓鱼|可能是垃圾邮件|请谨慎|be careful|suspicious|phishing|dangerous|scam/i;
    // 退信=硬退；可疑拦截=Gmail Message blocked / phishing-spam style（可换模板重试）
    const hardBounceRe=/undeliverable|mail delivery failed|delivery status notification\s*\(failure\)|returned mail|user unknown|address not found|no such user|5\.1\.1|地址不存在|找不到地址|地址失效|mailbox unavailable|invalid recipient/i;
    const messageBlockedRe=/message blocked|has been blocked|your message to .+ has been blocked|blocked\. see technical details|邮件被拦截|可疑拦截/i;
    const daemonFromRe=/mailer-daemon|postmaster|mail-daemon|mail delivery subsystem|noreply@google\.com/i;
    const failedRecipientFromText=blob=>{
      const pats=[
        /your message to\s+([^\s>]+@[^\s>]+)\s+has been blocked/i,
        /message to\s+([^\s>]+@[^\s>]+)\s+has been blocked/i,
        /final-recipient:\s*rfc822;\s*(\S+)/i,
        /(?:to|原收件人)[:：]\s*<?([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})>?/i,
      ];
      for(const p of pats){ const m=blob.match(p); if(m){ const em=(m[1]||'').toLowerCase().replace(/[>,;]+$/,''); if(em.includes('@')&&!daemonFromRe.test(em)) return em; } }
      return '';
    };
    const classifyBounce=blob=>{
      if(messageBlockedRe.test(blob)) return 'suspicious_block';
      if(daemonFromRe.test(blob) && hardBounceRe.test(blob)) return 'hard_bounce';
      if(daemonFromRe.test(blob) && /delivery status|undeliver|mailer-daemon|failure/i.test(blob) && !messageBlockedRe.test(blob)) return 'hard_bounce';
      return '';
    };
    const pickRows=(rows2,limit)=>rows2.slice(0,limit);
    const toRecord=row=>{
      const body=(row.innerText||'').trim();
      const blob=(body+' '+(row.getAttribute('aria-label')||'')).toLowerCase();
      // Conversation rows list participants in order ("me, creator"). The first
      // [email] is often our own account, even when the latest text is a reply.
      const participants=[...row.querySelectorAll('[email]')]
        .map(el=>(el.getAttribute('email')||'').trim().toLowerCase())
        .filter(Boolean);
      const from=participants.findLast(email=>email!==wanted)||'';
      if(!from.includes('@')) return null;
      const identity=row.querySelector('[data-legacy-last-non-draft-message-id]');
      const message_id=(identity?.getAttribute('data-legacy-last-non-draft-message-id')||'').toLowerCase();
      const thread_id=(identity?.getAttribute('data-legacy-thread-id')||'').toLowerCase();
      const thread_key=(identity?.getAttribute('data-thread-id')||'').replace(/^#/, '');
      const isUnread = row.classList.contains('zE') || !!row.querySelector('.zF');
      const bounce_kind=classifyBounce(blob+' '+from);
      const failed_recipient=bounce_kind?failedRecipientFromText(blob):'';
      return {
        from,
        subject:(row.querySelector('.y6')?.textContent||body.split(String.fromCharCode(10))[0]||'').trim().slice(0,120),
        snippet:(row.querySelector('.y2')?.innerText||body).trim().slice(0,220),
        unread:!!isUnread,
        at:row.querySelector('td.xW span[title]')?.getAttribute('title')||row.getAttribute('aria-label')||'',
        message_id:message_id||undefined,
        thread_id:thread_id||undefined,
        thread_url:thread_key?(location.origin+location.pathname+'#all/'+thread_key):undefined,
        suspicious:suspiciousRe.test(blob),
        bounce_kind:bounce_kind||undefined,
        failed_recipient:failed_recipient||undefined,
      };
    };
    for(const row of pickRows(rows,80)){
      const record=toRecord(row);
      if(!record) continue;
      inboxRows.push(record);
      const matched = !!recipient && record.from===recipient;
      const blob=((row.innerText||'')+' '+(row.getAttribute('aria-label')||'')).toLowerCase();
      const looksReply = /回复|re:|答覆|answered/i.test(blob);
      const looksBounce = !!record.bounce_kind || /退信|delivery status|mailer-daemon|undeliverable|message blocked/i.test(blob);
      if(matched || looksReply || looksBounce || !record.bounce_kind)
        replies.push({...record, matched, heuristic:!matched});
    }
    // 可疑/垃圾邮件里的回信：Gmail 常把“可疑”邮件直接丢进垃圾邮件标签。
    // 顺手扫一遍垃圾邮件箱，把里面的来信也带回去（点链接是 SPA 切换，不刷新页面）。
    let spamRows=[];
    try{
      const hash2=(location.hash||'').toLowerCase();
      if(!hash2.includes('spam')){
        const spamNav=[...document.querySelectorAll('a[href*="#spam"], a[href*="/#spam"]')].find(visible);
        if(spamNav){
          spamNav.click();
          await sleep(2500);
          const sRows=[...document.querySelectorAll('tr.zA,[role="main"] tr[role="row"]')].filter(visible);
          for(const row of pickRows(sRows,30)){
            const record=toRecord(row);
            if(!record) continue;
            record.suspicious=true;
            record.spam=true;
            spamRows.push(record);
            const matched=!!recipient && record.from===recipient;
            replies.push({...record, matched, heuristic:!matched});
          }
          const inboxNav=[...document.querySelectorAll('a[href*="#inbox"], a[href*="/#inbox"]')].find(visible);
          if(inboxNav) inboxNav.click();
        }
      }
    }catch(e){}
    return {
      replies_found: replies.length>0,
      replies,
      inbox_rows: inboxRows,
      spam_rows: spamRows,
      spam_unread: spamRows.length,
      stage: replies.length ? ('收件箱扫描到 '+replies.length+' 条疑似回复'+(spamRows.length?('（含垃圾邮件 '+spamRows.length+' 条）'):'')) : '收件箱扫描完成（无新回复）',
      diagnostics: {row_count: rows.length, spam_row_count: spamRows.length, mailbox: sender||wanted, recipient, marker},
      scanned: true,
    };
  }
  return {error:'unknown_action'};
}

