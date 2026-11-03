/* ═══ RECONCILIACIÓN · ROASLytics ═══════════════════════════════════════════════════════
   Un solo archivo que vale en dos sitios:
     · como pestaña «Reconciliación» del dashboard (/s/<slug>/, partes/06d.part lo carga);
     · como página sola en /s/<slug>/reconciliacion/ (lo que ve el rol vendedor).
   Pinta lo que devuelve /s/<slug>/reconciliacion/api y manda cada decisión en cuanto se
   marca. No depende de nada del dashboard: solo de un <div id="reconc" data-…>.
   El servidor dice qué puede hacer quien mira (`permisos`); aquí solo se enseña o se
   esconde el control, la puerta de verdad está en el servidor.
   ══════════════════════════════════════════════════════════════════════════════════════ */
(function(){
  const CSS=`
  .rc{font:13px/1.45 var(--d,system-ui);color:var(--tx,#ECECF4)}
  .rc *{box-sizing:border-box}
  .rc .rc-bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 14px}
  .rc .rc-bar .sp{flex:1}
  .rc .pill{background:var(--pan2,#171724);border:1px solid var(--line,#23232f);color:var(--dim,#8A8AA3);border-radius:999px;padding:5px 12px;font:600 11.5px var(--d,system-ui);cursor:pointer}
  .rc .pill.on{color:var(--tx,#fff);border-color:var(--pu,#7C3AED);background:rgba(124,58,237,.18)}
  .rc .btn{background:var(--pan2,#171724);border:1px solid var(--line,#23232f);color:var(--dim,#8A8AA3);border-radius:6px;padding:6px 11px;font:600 11.5px var(--d,system-ui);cursor:pointer;text-decoration:none;display:inline-block}
  .rc .btn:hover{color:var(--tx,#fff)} .rc .btn.pri{background:var(--pu,#7C3AED);color:#fff;border-color:transparent}
  .rc .btn:disabled{opacity:.5;cursor:default}
  .rc select,.rc input[type=text],.rc input[type=email]{background:var(--pan2,#171724);border:1px solid var(--line,#23232f);color:var(--tx,#fff);border-radius:6px;padding:5px 8px;font:12px var(--d,system-ui)}
  .rc .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 16px}
  .rc .kpi{background:var(--pan,#101018);border:1px solid var(--line,#23232f);border-radius:10px;padding:12px 14px}
  .rc .kpi b{display:block;font-size:20px;font-weight:700} .rc .kpi span{color:var(--dim,#8A8AA3);font-size:11.5px}
  .rc .ficha{color:var(--dim,#8A8AA3);font-size:12px;margin:0 0 14px} .rc .ficha b{color:var(--tx,#fff);font-weight:600}
  .rc .lote{background:var(--pan,#101018);border:1px solid var(--line,#23232f);border-radius:12px;margin:0 0 18px;overflow:hidden}
  .rc .lote>.lh{padding:14px 16px;display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap;border-bottom:1px solid var(--line,#23232f)}
  .rc .lote>.lh h3{margin:0 0 3px;font-size:14.5px} .rc .lote>.lh p{margin:0;color:var(--dim,#8A8AA3);font-size:12px;max-width:760px}
  .rc .lote>.lh .meta{margin-left:auto;text-align:right;color:var(--dim,#8A8AA3);font-size:11.5px;white-space:nowrap}
  .rc .tag{display:inline-block;border-radius:4px;padding:1px 7px;font:600 10.5px var(--m,monospace);letter-spacing:.3px;text-transform:uppercase}
  .rc .tag.ab{background:rgba(59,130,246,.18);color:#93c5fd} .rc .tag.de{background:rgba(245,158,11,.18);color:#fcd34d} .rc .tag.ce{background:rgba(34,197,94,.18);color:#86efac} .rc .tag.in{background:rgba(138,138,163,.18);color:var(--dim,#8A8AA3)}
  .rc table{width:100%;border-collapse:collapse;font-size:12.5px}
  .rc th{text-align:left;color:var(--dim,#8A8AA3);font-weight:600;font-size:11px;letter-spacing:.3px;text-transform:uppercase;padding:9px 10px;border-bottom:1px solid var(--line,#23232f);white-space:nowrap}
  .rc td{padding:9px 10px;border-bottom:1px solid var(--line,#23232f);vertical-align:top}
  .rc tr:last-child td{border-bottom:0}
  .rc td.num{font-family:var(--m,monospace);text-align:right;white-space:nowrap}
  .rc tr.SI td:first-child{box-shadow:inset 3px 0 0 var(--gr,#22c55e)} .rc tr.NO td:first-child{box-shadow:inset 3px 0 0 var(--rd,#ef4444)} .rc tr.DUDA td:first-child{box-shadow:inset 3px 0 0 var(--am,#f59e0b)}
  .rc tr.cerrado td{opacity:.72}
  .rc .ev{color:var(--dim,#8A8AA3);font-size:11.5px;margin-top:3px;max-width:420px}
  .rc .ev summary{cursor:pointer;color:var(--pul,#a78bfa)}
  .rc .cierre{font-size:11.5px;color:var(--dim,#8A8AA3)} .rc .cierre b{color:var(--tx,#fff)}
  .rc .vacio{padding:36px 16px;text-align:center;color:var(--dim,#8A8AA3)}
  .rc .msg{margin:8px 0;color:var(--dim,#8A8AA3);font-size:12px} .rc .msg.err{color:#fca5a5}
  .rc .inv{background:var(--pan,#101018);border:1px solid var(--line,#23232f);border-radius:10px;padding:14px 16px;margin:0 0 16px;display:none}
  .rc .inv.on{display:block} .rc .inv h4{margin:0 0 6px;font-size:13px} .rc .inv p{margin:0 0 10px;color:var(--dim,#8A8AA3);font-size:12px}
  .rc .inv form{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .rc td input.nota{width:200px}
  @media (max-width:900px){.rc td input.nota{width:120px}}
  `;

  const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const TIPOS={valor:'Valor',source:'Source (llamada)',cobro_sin_oportunidad:'Cobro sin oportunidad',bloqueado:'Sin identificador',factura_cero:'Factura en 0',llamada_sin_contacto:'Llamada sin contacto',fuera_periodo:'Fuera del periodo',otro:'Otro'};
  const ESTADO={abierto:['ab','Por decidir'],decidido:['de','Decidido'],cerrado:['ce','Cerrado']};

  function montar(el){
    if(!el||el.dataset.rcMontado) return; el.dataset.rcMontado='1';
    if(!document.getElementById('rc-css')){ const st=document.createElement('style'); st.id='rc-css'; st.textContent=CSS; document.head.appendChild(st); }
    const cfg={pre:el.dataset.pre||'',slug:el.dataset.slug,rol:el.dataset.rol||'',email:el.dataset.email||'',suite:el.dataset.suite||'',nombre:el.dataset.nombre||''};
    const base=`${cfg.pre}/s/${encodeURIComponent(cfg.slug)}/reconciliacion/`;
    const S={filtro:'pendiente',lote:'',datos:null,cargando:false};
    el.classList.add('rc');
    let moneda='USD', fmt=n=>{ try{ return (n||0).toLocaleString(moneda==='EUR'?'es-ES':'en-US',{style:'currency',currency:moneda,minimumFractionDigits:2,maximumFractionDigits:2}); }catch(e){ return '$'+(n||0).toFixed(2); } };

    async function api(ruta,opts={}){
      const r=await fetch(base+'api'+ruta,{cache:'no-store',credentials:'same-origin',method:opts.method||'GET',
        headers:{'Accept':'application/json',...(opts.body?{'Content-Type':'application/json'}:{})},
        body:opts.body?JSON.stringify(opts.body):undefined});
      const j=await r.json().catch(()=>({}));
      if(!r.ok){ const d=j.detail!==undefined?j.detail:j.error; throw new Error(typeof d==='string'?d:(d&&d.error)||('HTTP '+r.status)); }
      return j;
    }
    const puede=a=>!!(S.datos&&S.datos.permisos&&S.datos.permisos.includes(a));

    async function cargar(){
      S.cargando=true; pintar();
      try{ S.datos=await api(''); moneda=S.datos.moneda||'USD'; S.err=null; }
      catch(e){ S.err=e.message; }
      S.cargando=false; pintar();
    }

    function pintar(){
      if(S.err&&!S.datos){ el.innerHTML=`<div class="vacio">No pude cargar la reconciliación: ${esc(S.err)}</div>`; return; }
      if(!S.datos){ el.innerHTML='<div class="vacio">Cargando…</div>'; return; }
      const D=S.datos, lotes=D.lotes||[], items=D.items||[];
      const efecto=lotes.reduce((a,l)=>a+(l.efecto_si||0),0);
      const f=D.ficha||{};
      let h=`<div class="rc-bar">
        ${[['pendiente','Pendientes'],['decidido','Decididas sin cerrar'],['cerrado','Historial'],['todos','Todas']].map(([k,t])=>`<button type="button" class="pill${S.filtro===k?' on':''}" data-f="${k}">${t}</button>`).join('')}
        <select data-lote><option value="">Todos los lotes</option>${lotes.map(l=>`<option value="${esc(l.id)}"${S.lote===l.id?' selected':''}>${esc(l.titulo)} · ${l.pendientes} pendiente${l.pendientes===1?'':'s'}</option>`).join('')}</select>
        <span class="sp"></span>
        ${puede('invitar')?`<button type="button" class="btn" data-inv>Dar acceso a un vendedor</button>`:''}
        ${puede('exportar')?`<a class="btn" href="${esc(base)}export.xlsx${S.lote?'?lote='+encodeURIComponent(S.lote):''}" download>Descargar Excel</a>`:''}
        <button type="button" class="btn" data-recargar>${S.cargando?'Cargando…':'Actualizar'}</button>
      </div>
      <div class="inv" id="rcInv"><h4>Dar acceso solo a la reconciliación</h4>
        <p>La persona recibe un correo de Sentinel Suite, crea su contraseña y entra directamente a esta página: puede marcar SI / NO / DUDA y escribir notas, nada más. Vale para los vendedores del cliente.</p>
        <form data-invform><input type="email" name="email" placeholder="correo@cliente.com" required> <input type="text" name="name" placeholder="Nombre"> <button type="submit" class="btn pri">Enviar invitación</button> <span class="msg" data-invmsg></span></form></div>
      <div class="kpis">
        <div class="kpi"><b>${D.total.abiertos}</b><span>por decidir</span></div>
        <div class="kpi"><b>${D.total.decididos}</b><span>decididas, sin aplicar en el CRM</span></div>
        <div class="kpi"><b>${D.total.cerrados}</b><span>cerradas (historial)</span></div>
        <div class="kpi"><b>${fmt(efecto)}</b><span>subiría en el CRM con lo marcado SI</span></div>
      </div>
      <p class="ficha">Ficha: criterio <b>${esc(f.criterio_reconciliacion||'sin definir')}</b> · app de pagos <b>${esc(f.apps_reconciliacion||'—')}</b> · cohorte <b>${esc(f.cohorte||'creación de la oportunidad')}</b> · etapa de cierre <b>${esc(f.etapa_cierre||'—')}</b>${puede('ficha')?' · se edita en Ajustes':''}</p>`;
      const visibles=items.filter(i=>S.filtro==='todos'||(S.filtro==='pendiente'?i.estado!=='cerrado':i.estado===S.filtro));
      const porLote={}; visibles.forEach(i=>(porLote[i.lote]=porLote[i.lote]||[]).push(i));
      const lotesVis=lotes.filter(l=>porLote[l.id]&&(!S.lote||l.id===S.lote));
      if(!lotes.length) h+=ejemplo();
      else if(!lotesVis.length) h+=`<div class="vacio">Nada en esta vista.</div>`;
      lotesVis.forEach(l=>{ h+=lote(l,porLote[l.id]); });
      el.innerHTML=h; enganchar();
    }

    // Las columnas son SIEMPRE las mismas para todos los clientes (plantilla fija), aunque el lote
    // no tenga filas de valor o de source. Sin lotes se enseña la misma tabla con un lead de ejemplo.
    const CABECERA=`<thead><tr><th>Tipo</th><th>Contacto</th><th>Opp creada</th><th style="text-align:right">Plataforma</th><th style="text-align:right">CRM</th><th style="text-align:right">Dif.</th><th>Propuesta</th><th>Decisión</th><th>Nota</th><th>Estado</th></tr></thead>`;
    function ejemplo(){
      const ej={id:'ejemplo',tipo:'valor',contacto:'María Ejemplo',cliente_plataforma:'Maria Ejemplo Gómez',telefono:'+1 555 010 2030',opp_creada:'2026-09-03',estado_opp:'open',
        valor_plataforma:520,valor_ghl:0,diferencia_pct:100,propuesta:'Subir el Opp Value a 520.00 y marcar ganada',propuesta_decision:'SI',
        evidencia:'Cruce por teléfono. Opp creada el 3-sep en etapa Unqualified; en Fresha pagó 520 el 5-sep.',cruce_por:'teléfono',estado:'abierto',informativo:false,_ejemplo:true};
      return `<div class="lote"><div class="lh"><div><h3>Así se verá cada reconciliación <span class="tag in">ejemplo</span></h3>
        <p>Todavía no hay ninguna reconciliación registrada para ${esc(cfg.nombre||cfg.slug)}. Cuando Claude cruce los pagos o las llamadas con el CRM aparecerá aquí, con estas mismas columnas: la fila de abajo es un lead inventado para que se vea el formato.</p></div></div>
        <div style="overflow:auto"><table>${CABECERA}<tbody>${fila(ej)}</tbody></table></div></div>`;
    }

    function lote(l,its){
      const per=(l.periodo_desde||l.periodo_hasta)?`${l.periodo_desde||'…'} → ${l.periodo_hasta||'…'}`:'';
      const nDec=its.filter(i=>i.estado==='decidido').length;
      return `<div class="lote" data-lote="${esc(l.id)}"><div class="lh"><div>
        <h3>${esc(l.titulo)} <span class="tag ${l.estado==='cerrado'?'ce':'ab'}">${l.estado==='cerrado'?'cerrado':'abierto'}</span></h3>
        <p>${[per,l.criterio?'criterio '+l.criterio:'',l.plataforma,l.cohorte?'cohorte: '+l.cohorte:'',l.total_export!=null?'total del export '+fmt(l.total_export):''].filter(Boolean).map(esc).join(' · ')}</p>
        ${l.resumen?`<p style="margin-top:6px">${esc(l.resumen)}</p>`:''}</div>
        <div class="meta">${l.n} registro${l.n===1?'':'s'} · ${l.pendientes} pendiente${l.pendientes===1?'':'s'}<br>${esc((l.creado_en||'').slice(0,10))} · ${esc(l.creado_por||'')}<br>
          ${puede('cerrar')&&nDec?`<button type="button" class="btn" data-cerrarlote="${esc(l.id)}" style="margin-top:6px">Cerrar lo decidido (${nDec})</button>`:''}
          ${puede('borrar')?`<button type="button" class="btn" data-borrarlote="${esc(l.id)}" style="margin-top:6px">Borrar lote</button>`:''}</div></div>
        <div style="overflow:auto"><table>${CABECERA}<tbody>
        ${its.map(fila).join('')}</tbody></table></div></div>`;
    }

    function fila(i){
      const dec=i.decision||'', pre=i.propuesta_decision;
      const [cls,txt]=ESTADO[i.estado]||ESTADO.abierto;
      const bloqueado=!puede('decidir')||i.estado==='cerrado'||i.informativo||i._ejemplo;
      const contacto=`<b>${esc(i.contacto||i.cliente_plataforma||'—')}</b>${i.cliente_plataforma&&i.cliente_plataforma!==i.contacto?`<div class="ev">en la plataforma: ${esc(i.cliente_plataforma)}</div>`:''}
        ${i.estado_opp&&/open|abiert/i.test(i.estado_opp)?'<div class="ev">open → won</div>':''}${i.telefono?`<div class="ev">${esc(i.telefono)}</div>`:''}`;
      const prop=`${esc(i.propuesta||(i.informativo?'Informativo':''))}${i.source_actual||i.source_propuesto?`<div class="ev">${esc(i.source_actual||'—')} → ${esc(i.source_propuesto||'—')}</div>`:''}${i.evidencia?`<details class="ev"><summary>evidencia${i.cruce_por?' · cruce por '+esc(i.cruce_por):''}</summary>${esc(i.evidencia)}</details>`:''}`;
      const decision=i.informativo?'<span class="tag in">info</span>':`<select data-dec="${esc(i.id)}"${bloqueado?' disabled':''}>
        <option value=""${!dec?' selected':''}>${pre?'propuesta: '+pre:'—'}</option>${['SI','NO','DUDA'].map(d=>`<option value="${d}"${dec===d?' selected':''}>${d}</option>`).join('')}</select>`;
      const nota=i.informativo?'':`<input type="text" class="nota" data-nota="${esc(i.id)}" value="${esc(i.nota||'')}" placeholder="tu nota"${bloqueado?' disabled':''}>`;
      let estado=`<span class="tag ${cls}">${txt}</span>`;
      if(i._ejemplo) estado+='<div class="cierre">Al marcar SI y aplicarlo en el CRM, el equipo lo cierra y pasa al historial.</div>';
      else if(i.estado==='cerrado') estado+=`<div class="cierre">${esc((i.cerrado_en||'').slice(0,10))} · ${esc(i.cerrado_por||'')}<br>${esc(i.resultado||'')}${puede('cerrar')&&!i.informativo?` <button type="button" class="btn" data-reabrir="${esc(i.id)}" style="padding:2px 7px;margin-top:4px">Reabrir</button>`:''}</div>`;
      else if(i.estado==='decidido'){ estado+=`<div class="cierre">${esc(i.decidido_por||'')} · ${esc((i.decidido_en||'').slice(0,10))}</div>`; if(puede('cerrar')) estado+=`<button type="button" class="btn" data-cerrar="${esc(i.id)}" style="padding:2px 7px;margin-top:4px">Cerrar</button>`; }
      return `<tr class="${esc(dec||(i.estado==='cerrado'?'':'')) } ${i.estado==='cerrado'?'cerrado':''}">
        <td>${esc(TIPOS[i.tipo]||i.tipo)}</td><td>${contacto}</td><td>${esc(i.opp_creada||i.fecha_evento||'')}</td>
        <td class="num">${i.valor_plataforma!=null?fmt(i.valor_plataforma):''}</td><td class="num">${i.valor_ghl!=null?fmt(i.valor_ghl):''}</td>
        <td class="num">${i.diferencia_pct!=null?i.diferencia_pct.toFixed(1)+'%':''}</td><td>${prop}</td><td>${decision}</td><td>${nota}</td><td>${estado}</td></tr>`;
    }

    function enganchar(){
      el.querySelectorAll('[data-f]').forEach(b=>b.onclick=()=>{ S.filtro=b.dataset.f; pintar(); });
      const sel=el.querySelector('[data-lote]'); if(sel) sel.onchange=()=>{ S.lote=sel.value; pintar(); };
      const rec=el.querySelector('[data-recargar]'); if(rec) rec.onclick=cargar;
      const inv=el.querySelector('[data-inv]'); if(inv) inv.onclick=()=>el.querySelector('#rcInv').classList.toggle('on');
      const form=el.querySelector('[data-invform]'); if(form) form.onsubmit=invitar;
      el.querySelectorAll('[data-dec]').forEach(s=>s.onchange=async()=>{
        if(!s.value){ return; }
        const id=s.dataset.dec, nota=(el.querySelector(`[data-nota="${id}"]`)||{}).value;
        s.disabled=true;
        try{ const r=await api(`/items/${encodeURIComponent(id)}/decidir`,{method:'POST',body:{decision:s.value,nota}}); actualizar(r.item); }
        catch(e){ alert('No se guardó: '+e.message); s.disabled=false; }
      });
      el.querySelectorAll('[data-nota]').forEach(n=>n.onchange=async()=>{
        const id=n.dataset.nota, s=el.querySelector(`[data-dec="${id}"]`);
        if(!s||!s.value){ n.title='La nota se guarda al marcar una decisión.'; return; }
        try{ const r=await api(`/items/${encodeURIComponent(id)}/decidir`,{method:'POST',body:{decision:s.value,nota:n.value}}); actualizar(r.item,true); }
        catch(e){ alert('No se guardó la nota: '+e.message); }
      });
      el.querySelectorAll('[data-cerrar]').forEach(b=>b.onclick=async()=>{
        const res=prompt('¿Qué se hizo en el CRM? (p. ej. «Opp Value subido a 520 y marcada ganada» o «Descartado»)','');
        if(res===null) return; b.disabled=true;
        try{ const r=await api(`/items/${encodeURIComponent(b.dataset.cerrar)}/cerrar`,{method:'POST',body:{resultado:res}}); actualizar(r.item); }
        catch(e){ alert('No se cerró: '+e.message); b.disabled=false; }
      });
      el.querySelectorAll('[data-reabrir]').forEach(b=>b.onclick=async()=>{
        b.disabled=true;
        try{ const r=await api(`/items/${encodeURIComponent(b.dataset.reabrir)}/reabrir`,{method:'POST',body:{}}); actualizar(r.item); }
        catch(e){ alert('No se reabrió: '+e.message); b.disabled=false; }
      });
      el.querySelectorAll('[data-cerrarlote]').forEach(b=>b.onclick=async()=>{
        const res=prompt('Cierra todas las filas ya decididas de este lote. ¿Qué se hizo? (se anota en cada una)','Aplicado en el CRM');
        if(res===null) return; b.disabled=true;
        try{ await api(`/lotes/${encodeURIComponent(b.dataset.cerrarlote)}/cerrar`,{method:'POST',body:{resultado:res}}); await cargar(); }
        catch(e){ alert('No se pudo: '+e.message); b.disabled=false; }
      });
      el.querySelectorAll('[data-borrarlote]').forEach(b=>b.onclick=async()=>{
        if(!confirm('Borra el lote y TODAS sus filas (también el historial de decisiones). No hay deshacer. ¿Seguro?')) return;
        b.disabled=true;
        try{ await api(`/lotes/${encodeURIComponent(b.dataset.borrarlote)}`,{method:'DELETE'}); await cargar(); }
        catch(e){ alert('No se borró: '+e.message); b.disabled=false; }
      });
    }

    function actualizar(item,soloNota){
      if(!S.datos) return;
      const its=S.datos.items; const k=its.findIndex(x=>x.id===item.id);
      if(k>=0) its[k]=item; else its.push(item);
      // Recalcular totales y lotes sin otra llamada
      const todos=its; S.datos.total={abiertos:todos.filter(i=>i.estado==='abierto').length,decididos:todos.filter(i=>i.estado==='decidido').length,cerrados:todos.filter(i=>i.estado==='cerrado').length};
      S.datos.lotes.forEach(l=>{ const li=todos.filter(i=>i.lote===l.id); l.pendientes=li.filter(i=>i.estado!=='cerrado').length;
        l.efecto_si=li.filter(i=>i.tipo==='valor'&&i.decision==='SI'&&i.valor_plataforma!=null).reduce((a,i)=>a+Math.max(0,(i.valor_plataforma||0)-(i.valor_ghl||0)),0); });
      if(!soloNota) pintar();
    }

    async function invitar(ev){
      ev.preventDefault();
      const form=ev.target, msg=form.querySelector('[data-invmsg]'), email=(form.email.value||'').trim(), name=(form.name.value||'').trim();
      if(!cfg.suite){ msg.textContent='Esta instalación no está conectada a Sentinel Suite.'; return; }
      msg.className='msg'; msg.textContent='Enviando…';
      try{
        const r=await fetch(cfg.suite+'/api/admin/invite',{method:'POST',credentials:'include',
          headers:{'Content-Type':'application/json','Accept':'application/json'},
          body:JSON.stringify({email,name,access:[{slug:'reportes',role:'vendedor'}],subaccounts:[cfg.slug]})});
        const j=await r.json().catch(()=>({}));
        if(!r.ok) throw new Error(j.error||j.detail||('HTTP '+r.status));
        msg.textContent=`Listo: ${email} recibirá el correo de la suite y entrará directo a esta reconciliación.`; form.reset();
      }catch(e){ msg.className='msg err'; msg.textContent='No se pudo invitar desde aquí ('+e.message+'). Hazlo en Suite → Administración → Personas: herramienta ROASLytics, rol «vendedor», solo esta subcuenta.'; }
    }

    cargar();
  }

  window.ROASReconc={montar};
  const auto=document.getElementById('reconc');
  if(auto&&auto.dataset.slug&&!auto.dataset.manual) montar(auto);
})();
