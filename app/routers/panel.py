from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from app.services.memory import MemoryService
from app.config import get_settings
import logging

logger = logging.getLogger(__name__)
router = APIRouter()
memory = MemoryService()
settings = get_settings()

# ── Auth simples por token na URL ─────────────────────────────────────────────
def _check_token(token: str):
    if token != settings.app_secret:
        raise HTTPException(status_code=401, detail="Token inválido")

# ── API de dados ──────────────────────────────────────────────────────────────

@router.get("/panel/leads")
async def get_leads(
    token: str = Query(...),
    owner_id: str = Query(""),
    status: str = Query(""),
    channel: str = Query(""),
    search: str = Query(""),
    limit: int = Query(100),
):
    _check_token(token)
    db = memory.db
    query = db.table("customers").select("*").order("last_contact", desc=True).limit(limit)
    if owner_id:
        query = query.eq("owner_id", owner_id)
    if status:
        query = query.eq("lead_status", status)
    if channel:
        query = query.eq("channel", channel)
    result = query.execute()
    leads = result.data or []
    if search:
        s = search.lower()
        leads = [l for l in leads if s in (l.get("name") or "").lower() or s in (l.get("phone") or "") or s in (l.get("summary") or "").lower()]
    return leads


@router.get("/panel/stats")
async def get_stats(token: str = Query(...), owner_id: str = Query("")):
    _check_token(token)
    db = memory.db
    try:
        from datetime import datetime
        today = datetime.utcnow().date().isoformat()

        q = db.table("customers").select("lead_status,channel,lead_score,last_contact,last_sentiment,sentiment_history")
        if owner_id:
            q = q.eq("owner_id", owner_id)
        result = q.execute()
        leads = result.data or []

        total = len(leads)
        today_leads = sum(1 for l in leads if (str(l.get("last_contact") or ""))[:10] == today)
        novos = sum(1 for l in leads if l.get("lead_status") in ("novo", None, ""))
        qualificando = sum(1 for l in leads if l.get("lead_status") == "qualificando")
        mornos = sum(1 for l in leads if l.get("lead_status") == "morno")
        hot = sum(1 for l in leads if l.get("lead_status") == "quente")
        human = sum(1 for l in leads if l.get("lead_status") == "em_atendimento_humano")
        clientes = sum(1 for l in leads if l.get("lead_status") == "cliente")

        channel_counts = {}
        for l in leads:
            c = l.get("channel") or "não identificado"
            channel_counts[c] = channel_counts.get(c, 0) + 1

        channel_stats = sorted(
            [{"canal": k, "total": v, "pct": round(v / total * 100) if total else 0}
             for k, v in channel_counts.items()],
            key=lambda x: x["total"], reverse=True
        )

        # Sentimento agregado
        sentiments = {"positivo": 0, "neutro": 0, "negativo": 0, "frustrado": 0, "entusiasmado": 0}
        for l in leads:
            s = l.get("last_sentiment")
            if s and s in sentiments:
                sentiments[s] += 1
        total_sent = sum(sentiments.values()) or 1
        sentiment_stats = {k: {"total": v, "pct": round(v / total_sent * 100)} for k, v in sentiments.items() if v > 0}

        return {
            "total": total,
            "hoje": today_leads,
            "novos": novos,
            "qualificando": qualificando,
            "mornos": mornos,
            "quentes": hot,
            "em_atendimento": human,
            "clientes": clientes,
            "canais": channel_stats,
            "sentimento": sentiment_stats,
        }
    except Exception as e:
        logger.error(f"[Panel Stats] erro: {e}")
        return {"total": 0, "hoje": 0, "novos": 0, "qualificando": 0, "mornos": 0, "quentes": 0, "em_atendimento": 0, "clientes": 0, "canais": [], "sentimento": {}}


@router.get("/panel/lead/{phone}/messages")
async def get_lead_messages(phone: str, token: str = Query(...), owner_id: str = Query(""), limit: int = 10):
    _check_token(token)
    db = memory.db
    q = db.table("messages").select("role,content,created_at").eq("phone", phone).order("created_at", desc=True).limit(limit)
    if owner_id:
        q = q.eq("owner_id", owner_id)
    result = q.execute()
    msgs = list(reversed(result.data or []))
    return msgs


@router.get("/panel/owners")
async def get_owners(token: str = Query(...)):
    _check_token(token)
    db = memory.db
    result = db.table("owners").select("id,business_name,phone,agent_mode").execute()
    return result.data or []


@router.get("/panel/export")
async def export_leads(token: str = Query(...), owner_id: str = Query("")):
    """Exporta leads como CSV."""
    _check_token(token)
    import csv, io
    db = memory.db
    q = db.table("customers").select("*").order("last_contact", desc=True)
    if owner_id:
        q = q.eq("owner_id", owner_id)
    result = q.execute()
    leads = result.data or []

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "name","phone","channel","lead_score","lead_status",
        "last_intent","total_messages","last_contact","summary"
    ])
    writer.writeheader()
    for l in leads:
        writer.writerow({k: l.get(k, "") for k in writer.fieldnames})

    from fastapi.responses import Response
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"}
    )


# ── Knowledge Bank API ───────────────────────────────────────────────────────

@router.get("/panel/knowledge")
async def list_knowledge(
    token: str = Query(...),
    owner_id: str = Query(""),
    category: str = Query(""),
    search: str = Query(""),
    limit: int = Query(100),
):
    _check_token(token)
    db = memory.db
    q = db.table("knowledge_items").select("*").order("created_at", desc=True).limit(limit)
    if owner_id:
        q = q.eq("owner_id", owner_id)
    if category:
        q = q.eq("category", category)
    result = q.execute()
    items = result.data or []
    if search:
        s = search.lower()
        items = [i for i in items if s in (i.get("content") or "").lower()]
    return items


@router.post("/panel/knowledge")
async def add_knowledge(request: Request, token: str = Query(...)):
    _check_token(token)
    body = await request.json()
    owner_id = body.get("owner_id", "")
    category = body.get("category", "faq")
    content  = body.get("content", "").strip()
    source   = body.get("source", "painel")
    if not owner_id or not content:
        raise HTTPException(status_code=400, detail="owner_id e content são obrigatórios")
    try:
        from app.services.knowledge import KnowledgeBank
        kb = KnowledgeBank()
        result = kb.add_item(owner_id, category, content, source=source)
        return result
    except Exception as e:
        logger.error(f"[Panel KB] add error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/panel/knowledge/{item_id}")
async def delete_knowledge(item_id: str, token: str = Query(...)):
    _check_token(token)
    db = memory.db
    db.table("knowledge_items").delete().eq("id", item_id).execute()
    return {"ok": True, "deleted": item_id}


# ── Dashboard HTML ────────────────────────────────────────────────────────────

@router.get("/panel", response_class=HTMLResponse)
async def dashboard(token: str = Query(...)):
    _check_token(token)
    html = _build_html(token)
    return HTMLResponse(content=html)


@router.get("/panel/knowledge-ui", response_class=HTMLResponse)
async def knowledge_ui(token: str = Query(...)):
    _check_token(token)
    return HTMLResponse(content=_build_knowledge_html(token))


def _build_html(token: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Painel de Leads</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ height: 100%; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f0f0f; color: #e0e0e0; min-height: 100vh; display: flex; flex-direction: column; }}
  .header {{ background: #1a1a1a; border-bottom: 1px solid #2a2a2a; padding: 16px 24px; display: flex; align-items: center; gap: 12px; flex-shrink: 0; }}
  .header h1 {{ font-size: 18px; font-weight: 600; color: #fff; }}
  .header span {{ font-size: 12px; color: #666; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px; padding: 16px 24px 0; flex-shrink: 0; }}
  .stat {{ background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 10px; padding: 12px; }}
  .stat .val {{ font-size: 24px; font-weight: 700; color: #fff; }}
  .stat .lbl {{ font-size: 11px; color: #666; margin-top: 2px; }}
  .stat.hot .val {{ color: #ff6b35; }}
  .stat.human .val {{ color: #4fc3f7; }}
  .stat.today .val {{ color: #66bb6a; }}
  .channels {{ padding: 10px 24px 0; display: flex; gap: 6px; flex-wrap: wrap; flex-shrink: 0; }}
  .ch-tag {{ background: #1e1e1e; border: 1px solid #2a2a2a; border-radius: 20px; padding: 3px 10px; font-size: 11px; color: #aaa; }}
  .ch-tag span {{ color: #fff; font-weight: 600; }}
  .toolbar {{ padding: 12px 24px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; flex-shrink: 0; }}
  .toolbar input {{ background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 8px; padding: 8px 14px; color: #e0e0e0; font-size: 14px; width: 240px; outline: none; }}
  .toolbar input:focus {{ border-color: #444; }}
  .toolbar select {{ background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 8px; padding: 8px 12px; color: #e0e0e0; font-size: 14px; outline: none; cursor: pointer; }}
  .toolbar a.btn {{ background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 8px; padding: 8px 14px; color: #aaa; font-size: 13px; text-decoration: none; margin-left: auto; }}
  .toolbar a.btn:hover {{ color: #fff; border-color: #444; }}
  .table-wrap {{ flex: 1; overflow-y: auto; padding: 0 24px 24px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ background: #151515; color: #666; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .5px; padding: 10px 16px; text-align: left; cursor: pointer; user-select: none; position: sticky; top: 0; z-index: 2; }}
  th:hover {{ color: #aaa; }}
  td {{ padding: 10px 16px; border-bottom: 1px solid #1a1a1a; font-size: 13px; vertical-align: middle; }}
  tr:hover td {{ background: #161616; cursor: pointer; }}
  .name {{ color: #fff; font-weight: 500; }}
  @media (max-width: 768px) {{
    .stats {{ grid-template-columns: repeat(4, 1fr); gap: 6px; padding: 10px 12px 0; }}
    .stat {{ padding: 8px; }}
    .stat .val {{ font-size: 18px; }}
    .stat .lbl {{ font-size: 9px; }}
    .toolbar {{ padding: 8px 12px; }}
    .toolbar input {{ width: 100%; }}
    .table-wrap {{ padding: 0 8px 16px; }}
    td, th {{ padding: 8px 6px; font-size: 11px; }}
    .summary {{ max-width: 120px; }}
    .modal {{ width: 95vw; padding: 16px; }}
  }}
  .phone a {{ color: #4fc3f7; text-decoration: none; font-size: 12px; }}
  .phone a:hover {{ text-decoration: underline; }}
  .score {{ font-weight: 700; }}
  .score.hot {{ color: #ff6b35; }}
  .score.warm {{ color: #ffb74d; }}
  .score.cold {{ color: #666; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }}
  .badge.novo {{ background: #1e2a1e; color: #66bb6a; }}
  .badge.qualificando {{ background: #1e2030; color: #7986cb; }}
  .badge.morno {{ background: #2a2510; color: #ffb74d; }}
  .badge.quente {{ background: #2a1510; color: #ff6b35; }}
  .badge.em_atendimento_humano {{ background: #1e2a30; color: #4fc3f7; }}
  .badge.cliente {{ background: #1a2a1a; color: #81c784; }}
  .intent {{ font-size: 11px; color: #666; }}
  .ch {{ font-size: 11px; color: #888; }}
  .sentiment {{ font-size: 11px; font-weight: 600; }}
  .sentiment.positivo {{ color: #66bb6a; }}
  .sentiment.entusiasmado {{ color: #81c784; }}
  .sentiment.neutro {{ color: #888; }}
  .sentiment.negativo {{ color: #ef5350; }}
  .sentiment.frustrado {{ color: #ff7043; }}
  .badge.perdido {{ background: #2a1a1a; color: #ef5350; }}
  .sentiment-bar {{ display: flex; gap: 8px; padding: 12px 24px 0; flex-wrap: wrap; }}
  .sent-chip {{ display: flex; align-items: center; gap: 4px; background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 20px; padding: 4px 12px; font-size: 12px; }}
  .sent-dot {{ width: 8px; height: 8px; border-radius: 50%; }}
  .sent-dot.positivo,.sent-dot.entusiasmado {{ background: #66bb6a; }}
  .sent-dot.neutro {{ background: #888; }}
  .sent-dot.negativo {{ background: #ef5350; }}
  .sent-dot.frustrado {{ background: #ff7043; }}
  .summary {{ font-size: 12px; color: #555; max-width: 280px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .date {{ font-size: 11px; color: #555; }}
  /* Modal */
  .modal-bg {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,.75); z-index: 100; align-items: center; justify-content: center; }}
  .modal-bg.open {{ display: flex; }}
  .modal {{ background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px; width: 560px; max-width: 95vw; max-height: 85vh; overflow-y: auto; padding: 24px; }}
  .modal h2 {{ font-size: 16px; color: #fff; margin-bottom: 4px; }}
  .modal .meta {{ font-size: 12px; color: #666; margin-bottom: 16px; }}
  .modal .section {{ margin-bottom: 16px; }}
  .modal .section h3 {{ font-size: 11px; text-transform: uppercase; color: #555; letter-spacing: .5px; margin-bottom: 8px; }}
  .modal .summary-box {{ background: #111; border-radius: 8px; padding: 16px; font-size: 13px; color: #aaa; line-height: 1.7; max-height: 200px; overflow-y: auto; }}
  .modal .summary-box .s-label {{ display: inline-block; background: #1e2a30; color: #4fc3f7; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; margin-bottom: 4px; margin-top: 8px; }}
  .modal .summary-box .s-label:first-child {{ margin-top: 0; }}
  .modal .summary-box .s-text {{ color: #ccc; margin: 4px 0 8px 0; }}
  .modal .lead-cards {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 16px; }}
  .modal .lead-card {{ background: #111; border: 1px solid #222; border-radius: 8px; padding: 10px 12px; }}
  .modal .lead-card .lc-label {{ font-size: 10px; color: #555; text-transform: uppercase; letter-spacing: .5px; }}
  .modal .lead-card .lc-val {{ font-size: 15px; color: #fff; font-weight: 600; margin-top: 2px; }}
  .msg {{ padding: 8px 12px; border-radius: 8px; margin-bottom: 6px; font-size: 13px; line-height: 1.5; max-width: 88%; }}
  .msg.user {{ background: #1e2a30; color: #b0d4e3; align-self: flex-start; }}
  .msg.assistant {{ background: #1e2a1e; color: #a8d4aa; align-self: flex-end; margin-left: auto; }}
  .msg-wrap {{ display: flex; flex-direction: column; }}
  .msg-time {{ font-size: 10px; color: #444; margin-top: 2px; }}
  .close-btn {{ float: right; background: none; border: none; color: #666; font-size: 20px; cursor: pointer; line-height: 1; }}
  .close-btn:hover {{ color: #fff; }}
  .empty {{ text-align: center; padding: 60px; color: #444; font-size: 14px; }}
</style>
</head>
<body>

<div class="header">
  <h1>⚡ EcoZap</h1>
  <nav style="display:flex;gap:6px;margin-left:16px">
    <a href="/panel?token={token}" style="background:#252525;border:1px solid #333;border-radius:8px;padding:5px 14px;color:#fff;font-size:13px;text-decoration:none;font-weight:600">Leads</a>
    <a href="/panel/knowledge-ui?token={token}" style="background:transparent;border:1px solid #2a2a2a;border-radius:8px;padding:5px 14px;color:#888;font-size:13px;text-decoration:none">Conhecimento</a>
    <a href="/panel/billing?token={token}" style="background:transparent;border:1px solid #2a2a2a;border-radius:8px;padding:5px 14px;color:#888;font-size:13px;text-decoration:none">Billing</a>
  </nav>
  <span id="last-update" style="margin-left:auto"></span>
</div>

<div class="stats" id="stats-row">
  <div class="stat"><div class="val" id="s-total">—</div><div class="lbl">Total de leads</div></div>
  <div class="stat today"><div class="val" id="s-hoje">—</div><div class="lbl">Contatos hoje</div></div>
  <div class="stat"><div class="val" id="s-novos" style="color:#66bb6a">—</div><div class="lbl">Novos</div></div>
  <div class="stat"><div class="val" id="s-qualificando" style="color:#7986cb">—</div><div class="lbl">Qualificando</div></div>
  <div class="stat"><div class="val" id="s-mornos" style="color:#ffb74d">—</div><div class="lbl">Mornos</div></div>
  <div class="stat hot"><div class="val" id="s-hot">—</div><div class="lbl">Quentes</div></div>
  <div class="stat human"><div class="val" id="s-human">—</div><div class="lbl">Em atendimento</div></div>
  <div class="stat"><div class="val" id="s-clientes" style="color:#81c784">—</div><div class="lbl">Clientes</div></div>
</div>

<div class="channels" id="channels-row"></div>
<div class="sentiment-bar" id="sentiment-row"></div>

<div class="toolbar">
  <input type="text" id="search" placeholder="Buscar por nome ou número..." oninput="filterLeads()">
  <select id="filter-status" onchange="filterLeads()">
    <option value="">Todos os status</option>
    <option value="novo">Novo</option>
    <option value="qualificando">Qualificando</option>
    <option value="morno">Morno</option>
    <option value="quente">Quente</option>
    <option value="em_atendimento_humano">Em atendimento</option>
    <option value="cliente">Cliente</option>
  </select>
  <select id="filter-channel" onchange="filterLeads()">
    <option value="">Todos os canais</option>
    <option value="reels">Reels</option>
    <option value="anuncio">Anúncio</option>
    <option value="youtube">YouTube</option>
    <option value="indicacao">Indicação</option>
    <option value="google">Google</option>
    <option value="direct">Direct</option>
    <option value="stories">Stories</option>
  </select>
  <a class="btn" href="/panel/export?token={token}" download>⬇ Exportar CSV</a>
</div>

<div class="table-wrap">
  <table id="leads-table">
    <thead>
      <tr>
        <th onclick="sortBy('name')">Nome ↕</th>
        <th>Contato</th>
        <th onclick="sortBy('lead_score')">Score ↕</th>
        <th>Status</th>
        <th>Sentimento</th>
        <th>Canal</th>
        <th>Intenção</th>
        <th onclick="sortBy('total_messages')">Msgs ↕</th>
        <th onclick="sortBy('last_contact')">Último contato ↕</th>
        <th>Resumo</th>
      </tr>
    </thead>
    <tbody id="leads-body"></tbody>
  </table>
  <div id="empty-state" class="empty" style="display:none">Nenhum lead encontrado.</div>
</div>

<!-- Modal de perfil -->
<div class="modal-bg" id="modal-bg" onclick="closeModal(event)">
  <div class="modal" id="modal">
    <button class="close-btn" onclick="closeModal()">×</button>
    <h2 id="m-name"></h2>
    <div class="meta" id="m-meta"></div>
    <div class="section">
      <h3>Resumo da conversa</h3>
      <div class="summary-box" id="m-summary">—</div>
    </div>
    <div class="section">
      <h3>Últimas mensagens</h3>
      <div class="msg-wrap" id="m-messages"></div>
    </div>
  </div>
</div>

<script>
const TOKEN = '{token}';
let allLeads = [];
let sortKey = 'last_contact';
let sortAsc = false;

async function loadStats() {{
  const r = await fetch(`/panel/stats?token=${{TOKEN}}`);
  const d = await r.json();
  document.getElementById('s-total').textContent = d.total;
  document.getElementById('s-hoje').textContent = d.hoje;
  document.getElementById('s-novos').textContent = d.novos || 0;
  document.getElementById('s-qualificando').textContent = d.qualificando || 0;
  document.getElementById('s-mornos').textContent = d.mornos || 0;
  document.getElementById('s-hot').textContent = d.quentes || 0;
  document.getElementById('s-human').textContent = d.em_atendimento;
  document.getElementById('s-clientes').textContent = d.clientes || 0;
  const ch = document.getElementById('channels-row');
  ch.innerHTML = d.canais.slice(0,6).map(c =>
    `<div class="ch-tag"><span>${{c.canal}}</span> ${{c.pct}}% (${{c.total}})</div>`
  ).join('');
  // Sentimento
  const sr = document.getElementById('sentiment-row');
  const sent = d.sentimento || {{}};
  const sentLabels = {{positivo:'Positivo',entusiasmado:'Entusiasmado',neutro:'Neutro',negativo:'Negativo',frustrado:'Frustrado'}};
  sr.innerHTML = Object.keys(sent).map(k =>
    `<div class="sent-chip"><span class="sent-dot ${{k}}"></span><span style="color:#aaa">${{sentLabels[k]||k}}</span> <span style="color:#fff;font-weight:600">${{sent[k].pct}}%</span></div>`
  ).join('');
}}

async function loadLeads() {{
  const r = await fetch(`/panel/leads?token=${{TOKEN}}&limit=200`);
  allLeads = await r.json();
  filterLeads();
  document.getElementById('last-update').textContent = 'Atualizado: ' + new Date().toLocaleTimeString('pt-BR');
}}

function filterLeads() {{
  const search = document.getElementById('search').value.toLowerCase();
  const status = document.getElementById('filter-status').value;
  const channel = document.getElementById('filter-channel').value;
  let leads = allLeads.filter(l => {{
    if (search && !((l.name||'').toLowerCase().includes(search) || (l.phone||'').includes(search))) return false;
    if (status && l.lead_status !== status) return false;
    if (channel && l.channel !== channel) return false;
    return true;
  }});
  leads = leads.sort((a, b) => {{
    let va = a[sortKey] || '', vb = b[sortKey] || '';
    if (typeof va === 'number') return sortAsc ? va - vb : vb - va;
    return sortAsc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
  }});
  renderLeads(leads);
}}

function sortBy(key) {{
  if (sortKey === key) sortAsc = !sortAsc;
  else {{ sortKey = key; sortAsc = false; }}
  filterLeads();
}}

function scoreClass(s) {{
  if (s >= 70) return 'hot';
  if (s >= 40) return 'warm';
  return 'cold';
}}

function tempIcon(s) {{
  if (s >= 50) return '🔥';
  if (s >= 20) return '🌡️';
  return '❄️';
}}

function sentimentIcon(s) {{
  const map = {{positivo:'Positivo',entusiasmado:'Entusiasmado',neutro:'Neutro',negativo:'Negativo',frustrado:'Frustrado'}};
  return map[s] || '—';
}}

function fmtDate(d) {{
  if (!d) return '—';
  const dt = new Date(d);
  const today = new Date();
  if (dt.toDateString() === today.toDateString()) return 'Hoje ' + dt.toLocaleTimeString('pt-BR',{{hour:'2-digit',minute:'2-digit'}});
  return dt.toLocaleDateString('pt-BR',{{day:'2-digit',month:'2-digit'}}) + ' ' + dt.toLocaleTimeString('pt-BR',{{hour:'2-digit',minute:'2-digit'}});
}}

function renderLeads(leads) {{
  const tbody = document.getElementById('leads-body');
  const empty = document.getElementById('empty-state');
  if (!leads.length) {{ tbody.innerHTML=''; empty.style.display='block'; return; }}
  empty.style.display = 'none';
  tbody.innerHTML = leads.map(l => `
    <tr onclick="openLead('${{l.phone}}','${{l.owner_id}}')">
      <td class="name">${{l.name || '—'}}</td>
      <td class="phone"><a href="https://wa.me/${{(l.phone||'').replace(/[^0-9]/g,'')}}" target="_blank" onclick="event.stopPropagation()">${{l.phone}}</a></td>
      <td><span class="score ${{scoreClass(l.lead_score||0)}}">${{tempIcon(l.lead_score||0)}} ${{l.lead_score||0}}</span></td>
      <td><span class="badge ${{l.lead_status||'novo'}}">${{l.lead_status||'novo'}}</span></td>
      <td><span class="sentiment ${{l.last_sentiment||'neutro'}}">${{sentimentIcon(l.last_sentiment)}}</span></td>
      <td class="ch">${{l.channel||'—'}}</td>
      <td class="intent">${{l.last_intent||'—'}}</td>
      <td style="color:#666">${{l.total_messages||0}}</td>
      <td class="date">${{fmtDate(l.last_contact)}}</td>
      <td class="summary">${{l.summary||'—'}}</td>
    </tr>
  `).join('');
}}

function formatSummary(raw) {{
  if (!raw || raw === '—') return '<em style="color:#555">Sem resumo ainda.</em>';
  // Remove markdown noise
  let text = raw.replace(/[*#]/g, '').replace(/\\n- /g, '\\n').trim();
  // Quebra em blocos por "Resumo" ou "Nota"
  const blocks = text.split(/(?=Resumo da Conversa|\\[Nota)/g).filter(b => b.trim());
  if (blocks.length <= 1 && text.length < 400) return `<div class="s-text">${{text}}</div>`;
  // Pega só os últimos 2 blocos pra não ficar enorme
  const recent = blocks.slice(-2);
  return recent.map((block, i) => {{
    const isNote = block.trim().startsWith('[Nota');
    const label = isNote ? 'Nota do dono' : (i === recent.length -1 ? 'Resumo mais recente' : 'Resumo anterior');
    const clean = block.replace(/^Resumo da Conversa\\s*/i, '').trim();
    return `<div class="s-label">${{label}}</div><div class="s-text">${{clean.substring(0, 300)}}${{clean.length > 300 ? '...' : ''}}</div>`;
  }}).join('');
}}

async function openLead(phone, ownerId) {{
  const lead = allLeads.find(l => l.phone === phone);
  if (!lead) return;
  document.getElementById('m-name').textContent = lead.name || phone;
  // Cards com info do lead
  document.getElementById('m-meta').innerHTML = `
    <div class="lead-cards">
      <div class="lead-card"><div class="lc-label">Contato</div><div class="lc-val"><a href="https://wa.me/${{(phone||'').replace(/[^0-9]/g,'')}}" target="_blank" style="color:#4fc3f7;text-decoration:none">${{phone}}</a></div></div>
      <div class="lead-card"><div class="lc-label">Score</div><div class="lc-val" style="color:${{(lead.lead_score||0)>=70?'#ff6b35':(lead.lead_score||0)>=40?'#ffb74d':'#666'}}">${{lead.lead_score||0}}/100</div></div>
      <div class="lead-card"><div class="lc-label">Canal</div><div class="lc-val">${{lead.channel||'desconhecido'}}</div></div>
      <div class="lead-card"><div class="lc-label">Status</div><div class="lc-val"><span class="badge ${{lead.lead_status||'novo'}}">${{lead.lead_status||'novo'}}</span></div></div>
      <div class="lead-card"><div class="lc-label">Mensagens</div><div class="lc-val">${{lead.total_messages||0}}</div></div>
      <div class="lead-card"><div class="lc-label">Último contato</div><div class="lc-val" style="font-size:12px">${{fmtDate(lead.last_contact)}}</div></div>
      <div class="lead-card"><div class="lc-label">Sentimento</div><div class="lc-val sentiment ${{lead.last_sentiment||'neutro'}}">${{sentimentIcon(lead.last_sentiment)}}</div></div>
      <div class="lead-card"><div class="lc-label">Histórico</div><div class="lc-val" style="font-size:11px">${{(lead.sentiment_history||[]).slice(-5).map(s=>`<span class="sentiment ${{s}}" style="margin-right:4px">${{sentimentIcon(s)}}</span>`).join(' → ')||'—'}}</div></div>
    </div>`;
  document.getElementById('m-summary').innerHTML = formatSummary(lead.summary);
  document.getElementById('m-messages').innerHTML = '<em style="color:#444;font-size:12px">Carregando...</em>';
  document.getElementById('modal-bg').classList.add('open');
  const r = await fetch(`/panel/lead/${{encodeURIComponent(phone)}}/messages?token=${{TOKEN}}&owner_id=${{ownerId}}&limit=10`);
  const msgs = await r.json();
  const wrap = document.getElementById('m-messages');
  if (!msgs.length) {{ wrap.innerHTML = '<em style="color:#444;font-size:12px">Sem mensagens registradas.</em>'; return; }}
  wrap.innerHTML = msgs.map(m => `
    <div class="msg ${{m.role}}">${{m.content}}<div class="msg-time">${{fmtDate(m.created_at)}}</div></div>
  `).join('');
}}

function closeModal(e) {{
  if (!e || e.target === document.getElementById('modal-bg')) {{
    document.getElementById('modal-bg').classList.remove('open');
  }}
}}

// Carrega tudo
loadStats();
loadLeads();
// Atualiza a cada 60 segundos
setInterval(() => {{ loadStats(); loadLeads(); }}, 60000);
</script>
</body>
</html>"""


def _build_knowledge_html(token: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Conhecimento — EcoZap</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f0f0f; color: #e0e0e0; min-height: 100vh; display: flex; flex-direction: column; }}
  .header {{ background: #1a1a1a; border-bottom: 1px solid #2a2a2a; padding: 16px 24px; display: flex; align-items: center; gap: 12px; flex-shrink: 0; }}
  .header h1 {{ font-size: 18px; font-weight: 600; color: #fff; }}
  nav a {{ background:transparent; border:1px solid #2a2a2a; border-radius:8px; padding:5px 14px; color:#888; font-size:13px; text-decoration:none; }}
  nav a.active {{ background:#252525; border-color:#333; color:#fff; font-weight:600; }}
  .toolbar {{ padding: 16px 24px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
  .toolbar input, .toolbar select {{ background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 8px; padding: 8px 14px; color: #e0e0e0; font-size: 14px; outline: none; }}
  .toolbar input {{ width: 260px; }}
  .toolbar input:focus, .toolbar select:focus {{ border-color: #444; }}
  .btn-add {{ background: #25a244; border: none; border-radius: 8px; padding: 8px 18px; color: #fff; font-size: 14px; font-weight: 600; cursor: pointer; margin-left: auto; }}
  .btn-add:hover {{ background: #2db84e; }}
  .content {{ flex: 1; overflow-y: auto; padding: 0 24px 32px; }}
  .cat-section {{ margin-bottom: 28px; }}
  .cat-title {{ font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #555; font-weight: 700; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }}
  .cat-count {{ background: #1e1e1e; border: 1px solid #2a2a2a; border-radius: 10px; padding: 1px 8px; font-size: 11px; color: #777; }}
  .kb-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 10px; }}
  .kb-card {{ background: #1a1a1a; border: 1px solid #222; border-radius: 10px; padding: 14px 16px; position: relative; }}
  .kb-card:hover {{ border-color: #333; }}
  .kb-card .content-text {{ font-size: 13px; color: #ccc; line-height: 1.6; padding-right: 28px; word-break: break-word; }}
  .kb-card .meta {{ font-size: 11px; color: #444; margin-top: 8px; display: flex; gap: 12px; }}
  .kb-card .meta span {{ display: flex; align-items: center; gap: 3px; }}
  .kb-card .del-btn {{ position: absolute; top: 10px; right: 10px; background: none; border: none; color: #333; font-size: 16px; cursor: pointer; line-height: 1; padding: 2px 4px; border-radius: 4px; }}
  .kb-card .del-btn:hover {{ background: #2a1515; color: #ef5350; }}
  .conf-bar {{ display: inline-block; width: 40px; height: 4px; border-radius: 2px; background: #222; overflow: hidden; vertical-align: middle; margin-right: 2px; }}
  .conf-fill {{ height: 100%; background: #25a244; border-radius: 2px; }}
  .empty {{ text-align: center; padding: 60px; color: #444; font-size: 14px; }}
  /* Modal de adição */
  .modal-bg {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,.75); z-index: 100; align-items: center; justify-content: center; }}
  .modal-bg.open {{ display: flex; }}
  .modal {{ background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px; width: 520px; max-width: 95vw; padding: 28px; }}
  .modal h2 {{ font-size: 16px; color: #fff; margin-bottom: 20px; }}
  .modal label {{ font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: .5px; display: block; margin-bottom: 6px; }}
  .modal select, .modal textarea {{ width: 100%; background: #111; border: 1px solid #2a2a2a; border-radius: 8px; padding: 10px 14px; color: #e0e0e0; font-size: 14px; outline: none; font-family: inherit; }}
  .modal select {{ margin-bottom: 16px; }}
  .modal textarea {{ min-height: 120px; resize: vertical; margin-bottom: 16px; line-height: 1.5; }}
  .modal select:focus, .modal textarea:focus {{ border-color: #444; }}
  .modal-footer {{ display: flex; gap: 10px; justify-content: flex-end; }}
  .btn-cancel {{ background: none; border: 1px solid #2a2a2a; border-radius: 8px; padding: 9px 18px; color: #888; font-size: 14px; cursor: pointer; }}
  .btn-cancel:hover {{ color: #fff; border-color: #444; }}
  .btn-save {{ background: #25a244; border: none; border-radius: 8px; padding: 9px 20px; color: #fff; font-size: 14px; font-weight: 600; cursor: pointer; }}
  .btn-save:hover {{ background: #2db84e; }}
  .cat-badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .5px; }}
  .cat-produto {{ background:#1e2030; color:#7986cb; }}
  .cat-faq {{ background:#1e2a30; color:#4fc3f7; }}
  .cat-objecao {{ background:#2a1e1e; color:#ef9a9a; }}
  .cat-estilo {{ background:#1e2a1e; color:#81c784; }}
  .cat-expertise {{ background:#2a2510; color:#ffb74d; }}
  .cat-concorrente {{ background:#2a1a2a; color:#ce93d8; }}
  .cat-depoimento {{ background:#1a2a20; color:#80cbc4; }}
  .cat-processo {{ background:#1e1e2a; color:#90caf9; }}
  .cat-aprendizado {{ background:#2a2520; color:#ffcc80; }}
  .stats-bar {{ padding: 0 24px 16px; display: flex; gap: 8px; flex-wrap: wrap; }}
  .stat-chip {{ background: #1a1a1a; border: 1px solid #222; border-radius: 8px; padding: 8px 14px; }}
  .stat-chip .sv {{ font-size: 20px; font-weight: 700; color: #fff; }}
  .stat-chip .sl {{ font-size: 10px; color: #555; margin-top: 1px; text-transform: uppercase; }}
</style>
</head>
<body>

<div class="header">
  <h1>⚡ EcoZap</h1>
  <nav style="display:flex;gap:6px;margin-left:16px">
    <a href="/panel?token={token}">Leads</a>
    <a href="/panel/knowledge-ui?token={token}" class="active">Conhecimento</a>
  </nav>
</div>

<div class="toolbar">
  <input type="text" id="kb-search" placeholder="Buscar no conhecimento..." oninput="filterKB()">
  <select id="kb-cat" onchange="filterKB()">
    <option value="">Todas as categorias</option>
    <option value="produto">Produto / Serviço</option>
    <option value="faq">FAQ</option>
    <option value="objecao">Objeção</option>
    <option value="estilo">Estilo / Tom</option>
    <option value="expertise">Expertise</option>
    <option value="concorrente">Concorrente</option>
    <option value="depoimento">Depoimento</option>
    <option value="processo">Processo</option>
    <option value="aprendizado">Aprendizado</option>
  </select>
  <button class="btn-add" onclick="openModal()">+ Adicionar conhecimento</button>
</div>

<div class="stats-bar" id="kb-stats"></div>

<div class="content" id="kb-content">
  <div class="empty">Carregando...</div>
</div>

<!-- Modal de adição -->
<div class="modal-bg" id="modal-bg" onclick="closeModal(event)">
  <div class="modal">
    <h2>Adicionar ao Conhecimento</h2>
    <label>Categoria</label>
    <select id="m-cat">
      <option value="faq">FAQ — Pergunta e Resposta</option>
      <option value="produto">Produto / Serviço</option>
      <option value="objecao">Objeção — Como lidar</option>
      <option value="estilo">Estilo / Tom de voz</option>
      <option value="expertise">Expertise / Autoridade</option>
      <option value="concorrente">Concorrente / Diferencial</option>
      <option value="depoimento">Depoimento / Prova social</option>
      <option value="processo">Processo / Contratação</option>
      <option value="aprendizado">Aprendizado automático</option>
    </select>
    <label>Conteúdo</label>
    <textarea id="m-content" placeholder="Ex: Pergunta: Quanto custa? | Resposta: O valor do plano básico é R$97/mês com 7 dias grátis."></textarea>
    <div class="modal-footer">
      <button class="btn-cancel" onclick="closeModal()">Cancelar</button>
      <button class="btn-save" onclick="saveItem()">Salvar</button>
    </div>
  </div>
</div>

<script>
const TOKEN = '{token}';
let allItems = [];
let ownerId = '';

const CAT_LABELS = {{
  produto: 'Produto / Serviço', faq: 'FAQ', objecao: 'Objeção',
  estilo: 'Estilo / Tom', expertise: 'Expertise', concorrente: 'Concorrente',
  depoimento: 'Depoimento', processo: 'Processo', aprendizado: 'Aprendizado'
}};
const CAT_ICONS = {{
  produto:'📦', faq:'❓', objecao:'🛡️', estilo:'🎨',
  expertise:'🏆', concorrente:'⚔️', depoimento:'💬', processo:'⚙️', aprendizado:'🤖'
}};

async function loadOwner() {{
  const r = await fetch(`/panel/owners?token=${{TOKEN}}`);
  const owners = await r.json();
  if (owners.length > 0) ownerId = owners[0].id;
}}

async function loadKB() {{
  if (!ownerId) return;
  const r = await fetch(`/panel/knowledge?token=${{TOKEN}}&owner_id=${{ownerId}}&limit=500`);
  allItems = await r.json();
  renderStats();
  filterKB();
}}

function renderStats() {{
  const cats = {{}};
  allItems.forEach(i => {{ cats[i.category] = (cats[i.category]||0) + 1; }});
  const bar = document.getElementById('kb-stats');
  bar.innerHTML = `
    <div class="stat-chip"><div class="sv">${{allItems.length}}</div><div class="sl">Total</div></div>
    ${{Object.entries(cats).sort((a,b)=>b[1]-a[1]).map(([cat, n]) =>
      `<div class="stat-chip"><div class="sv">${{n}}</div><div class="sl">${{CAT_LABELS[cat]||cat}}</div></div>`
    ).join('')}}
  `;
}}

function filterKB() {{
  const search = document.getElementById('kb-search').value.toLowerCase();
  const cat = document.getElementById('kb-cat').value;
  let items = allItems.filter(i => {{
    if (cat && i.category !== cat) return false;
    if (search && !(i.content||'').toLowerCase().includes(search)) return false;
    return true;
  }});
  renderKB(items);
}}

function renderKB(items) {{
  const el = document.getElementById('kb-content');
  if (!items.length) {{
    el.innerHTML = '<div class="empty">Nenhum conhecimento encontrado.<br><span style="font-size:12px;color:#333">Use /treinar no WhatsApp ou clique em "+ Adicionar" acima.</span></div>';
    return;
  }}
  // Agrupa por categoria
  const groups = {{}};
  items.forEach(i => {{
    if (!groups[i.category]) groups[i.category] = [];
    groups[i.category].push(i);
  }});
  const order = ['produto','faq','objecao','estilo','expertise','concorrente','depoimento','processo','aprendizado'];
  const sorted = [...order.filter(c => groups[c]), ...Object.keys(groups).filter(c => !order.includes(c))];
  el.innerHTML = sorted.map(cat => `
    <div class="cat-section">
      <div class="cat-title">
        ${{CAT_ICONS[cat]||'📌'}} ${{CAT_LABELS[cat]||cat}}
        <span class="cat-count">${{groups[cat].length}}</span>
      </div>
      <div class="kb-grid">
        ${{groups[cat].map(item => `
          <div class="kb-card" id="card-${{item.id}}">
            <button class="del-btn" onclick="deleteItem('${{item.id}}')" title="Remover">×</button>
            <div class="content-text">${{escHtml(item.content)}}</div>
            <div class="meta">
              <span>
                <span class="conf-bar"><span class="conf-fill" style="width:${{Math.round((item.confidence||1)*100)}}%"></span></span>
                ${{Math.round((item.confidence||1)*100)}}%
              </span>
              <span>🔄 ${{item.times_used||0}}x usado</span>
              <span>${{fmtDate(item.created_at)}}</span>
            </div>
          </div>
        `).join('')}}
      </div>
    </div>
  `).join('');
}}

function escHtml(str) {{
  return (str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function fmtDate(d) {{
  if (!d) return '';
  const dt = new Date(d);
  return dt.toLocaleDateString('pt-BR',{{day:'2-digit',month:'2-digit',year:'2-digit'}});
}}

async function deleteItem(id) {{
  if (!confirm('Remover este conhecimento?')) return;
  const card = document.getElementById('card-' + id);
  if (card) card.style.opacity = '0.3';
  const r = await fetch(`/panel/knowledge/${{id}}?token=${{TOKEN}}`, {{method:'DELETE'}});
  if ((await r.json()).ok) {{
    allItems = allItems.filter(i => i.id !== id);
    renderStats();
    filterKB();
  }}
}}

function openModal() {{ document.getElementById('modal-bg').classList.add('open'); }}
function closeModal(e) {{
  if (!e || e.target === document.getElementById('modal-bg'))
    document.getElementById('modal-bg').classList.remove('open');
}}

async function saveItem() {{
  const cat = document.getElementById('m-cat').value;
  const content = document.getElementById('m-content').value.trim();
  if (!content) {{ alert('Escreva o conteúdo antes de salvar.'); return; }}
  const btn = document.querySelector('.btn-save');
  btn.textContent = 'Salvando...'; btn.disabled = true;
  const r = await fetch(`/panel/knowledge?token=${{TOKEN}}`, {{
    method: 'POST',
    headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{owner_id: ownerId, category: cat, content, source: 'painel'}})
  }});
  const res = await r.json();
  btn.textContent = 'Salvar'; btn.disabled = false;
  if (res.ok || res.id) {{
    document.getElementById('m-content').value = '';
    closeModal();
    await loadKB();
  }} else {{
    alert('Erro ao salvar. Tente novamente.');
  }}
}}

// Init
loadOwner().then(loadKB);
</script>
</body>
</html>"""


# ── Billing: status da assinatura (API) ──────────────────────────────────────

@router.get("/panel/billing/status")
async def panel_billing_status(token: str = Query(...), owner_id: str = Query(...)):
    _check_token(token)
    db = memory.db
    owner = db.table("owners").select(
        "id, business_name, plan_id, sub_status, trial_ends_at, sub_ends_at, stripe_customer_id"
    ).eq("id", owner_id).maybe_single().execute()

    if not (owner and owner.data):
        raise HTTPException(status_code=404, detail="Owner não encontrado")

    from app.models.plans import PLANS
    from datetime import datetime
    o = owner.data
    plan = PLANS.get(o.get("plan_id", "starter"))
    month = datetime.utcnow().strftime("%Y-%m")
    usage_row = db.table("usage_logs").select("messages_count").eq("owner_id", owner_id).eq("month", month).maybe_single().execute()
    used = (usage_row.data or {}).get("messages_count", 0)

    return {
        "owner_id": owner_id,
        "business_name": o.get("business_name"),
        "plan": plan.to_dict() if plan else None,
        "sub_status": o.get("sub_status", "trial"),
        "trial_ends_at": o.get("trial_ends_at"),
        "sub_ends_at": o.get("sub_ends_at"),
        "usage": {"month": month, "used": used, "limit": plan.msg_limit if plan else 1000},
    }


# ── Billing: página HTML ─────────────────────────────────────────────────────

@router.get("/panel/billing", response_class=HTMLResponse)
async def panel_billing_ui(request: Request, token: str = Query(...), owner_id: str = Query("")):
    _check_token(token)
    return HTMLResponse(_build_billing_html(token, owner_id))


def _build_billing_html(token: str, owner_id: str) -> str:
    from app.models.plans import PLANS
    import json
    plans_json = json.dumps([p.to_dict() for p in PLANS.values()])

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>⚡ EcoZap — Billing</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#0f0f0f;color:#e2e8f0;min-height:100vh}}
nav{{background:#1a1a2e;border-bottom:1px solid #2d2d4e;padding:12px 24px;display:flex;align-items:center;gap:16px}}
nav .brand{{font-weight:700;font-size:1.1rem;color:#7c3aed}}
nav a{{color:#94a3b8;text-decoration:none;font-size:.85rem;padding:4px 10px;border-radius:6px;transition:all .2s}}
nav a:hover,nav a.active{{background:#2d2d4e;color:#e2e8f0}}
.container{{max-width:900px;margin:32px auto;padding:0 24px}}
.section{{background:#1a1a2e;border:1px solid #2d2d4e;border-radius:12px;padding:24px;margin-bottom:24px}}
.section h2{{font-size:1rem;color:#94a3b8;margin-bottom:16px;text-transform:uppercase;letter-spacing:.05em}}
.status-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
.stat-card{{background:#0f0f1a;border:1px solid #2d2d4e;border-radius:8px;padding:16px;text-align:center}}
.stat-card .val{{font-size:1.6rem;font-weight:700;color:#7c3aed}}
.stat-card .label{{font-size:.8rem;color:#64748b;margin-top:4px}}
.progress-bar{{background:#2d2d4e;border-radius:99px;height:10px;margin-top:8px;overflow:hidden}}
.progress-fill{{height:100%;border-radius:99px;background:linear-gradient(90deg,#7c3aed,#06b6d4);transition:width .5s}}
.plans-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}}
.plan-card{{background:#0f0f1a;border:2px solid #2d2d4e;border-radius:12px;padding:20px;text-align:center;transition:all .2s;cursor:pointer}}
.plan-card:hover,.plan-card.current{{border-color:#7c3aed;background:#1a1a2e}}
.plan-card.current{{position:relative}}
.plan-card.current::before{{content:'Plano atual';position:absolute;top:-10px;left:50%;transform:translateX(-50%);background:#7c3aed;color:#fff;font-size:.7rem;padding:2px 10px;border-radius:99px}}
.plan-name{{font-size:1.1rem;font-weight:700;margin-bottom:4px}}
.plan-price{{font-size:1.8rem;font-weight:800;color:#7c3aed;margin:8px 0}}
.plan-price span{{font-size:.8rem;color:#64748b;font-weight:400}}
.plan-features{{list-style:none;margin-top:12px;text-align:left}}
.plan-features li{{font-size:.82rem;color:#94a3b8;padding:3px 0}}
.plan-features li::before{{content:'✓ ';color:#10b981}}
.btn{{display:inline-block;padding:10px 20px;border-radius:8px;font-weight:600;cursor:pointer;border:none;font-size:.9rem;transition:all .2s}}
.btn-primary{{background:#7c3aed;color:#fff}}
.btn-primary:hover{{background:#6d28d9}}
.btn-danger{{background:#ef4444;color:#fff}}
.btn-danger:hover{{background:#dc2626}}
.btn-ghost{{background:transparent;border:1px solid #4b5563;color:#94a3b8}}
.badge{{display:inline-block;padding:3px 10px;border-radius:99px;font-size:.75rem;font-weight:600}}
.badge-trial{{background:#1e3a5f;color:#60a5fa}}
.badge-active{{background:#14532d;color:#4ade80}}
.badge-past_due{{background:#7c2d12;color:#fca5a5}}
.badge-canceled{{background:#374151;color:#9ca3af}}
.history-table{{width:100%;border-collapse:collapse}}
.history-table th,.history-table td{{padding:10px 12px;text-align:left;border-bottom:1px solid #2d2d4e;font-size:.85rem}}
.history-table th{{color:#64748b;font-weight:500}}
.empty{{text-align:center;color:#475569;padding:40px}}
</style>
</head>
<body>

<nav>
  <span class="brand">⚡ EcoZap</span>
  <a href="/panel?token={token}&owner_id={owner_id}">Leads</a>
  <a href="/panel/knowledge-ui?token={token}&owner_id={owner_id}">Conhecimento</a>
  <a href="/panel/billing?token={token}&owner_id={owner_id}">Billing</a>
  <a href="/panel/billing?token={token}&owner_id={owner_id}" class="active">Billing</a>
</nav>

<div class="container">

  <!-- Status atual -->
  <div class="section">
    <h2>Status da Assinatura</h2>
    <div id="status-area"><p class="empty">Carregando...</p></div>
  </div>

  <!-- Uso do mês -->
  <div class="section">
    <h2>Uso este Mês</h2>
    <div id="usage-area"><p class="empty">Carregando...</p></div>
  </div>

  <!-- Planos -->
  <div class="section">
    <h2>Planos Disponíveis</h2>
    <div class="plans-grid" id="plans-grid"></div>
  </div>

  <!-- Histórico de pagamentos -->
  <div class="section">
    <h2>Histórico</h2>
    <div id="history-area"><p class="empty">Carregando...</p></div>
  </div>

</div>

<script>
const TOKEN = '{token}';
const OWNER_ID = '{owner_id}';
const PLANS = {plans_json};
let currentStatus = {{}};

async function loadStatus() {{
  if (!OWNER_ID) return;
  const r = await fetch(`/panel/billing/status?token=${{TOKEN}}&owner_id=${{OWNER_ID}}`);
  if (!r.ok) {{ document.getElementById('status-area').innerHTML = '<p class="empty">Erro ao carregar</p>'; return; }}
  currentStatus = await r.json();
  renderStatus();
  renderUsage();
}}

function renderStatus() {{
  const s = currentStatus;
  if (!s || !s.plan) return;
  const badgeClass = 'badge-' + (s.sub_status || 'trial');
  const statusLabel = {{trial:'Trial',active:'Ativo',past_due:'Pagamento pendente',canceled:'Cancelado'}}[s.sub_status] || s.sub_status;
  document.getElementById('status-area').innerHTML = `
    <div class="status-grid">
      <div class="stat-card">
        <div class="val">${{s.plan.name}}</div>
        <div class="label">Plano atual</div>
      </div>
      <div class="stat-card">
        <div class="val"><span class="badge ${{badgeClass}}">${{statusLabel}}</span></div>
        <div class="label">Status</div>
      </div>
      <div class="stat-card">
        <div class="val">R$ ${{s.plan.price_monthly.toFixed(2)}}</div>
        <div class="label">Mensalidade</div>
      </div>
      ${{s.trial_ends_at && s.sub_status === 'trial' ? `
      <div class="stat-card">
        <div class="val" style="font-size:1rem">${{fmtDate(s.trial_ends_at)}}</div>
        <div class="label">Trial termina em</div>
      </div>` : ''}}
    </div>
    ${{s.sub_status === 'active' ? `
    <div style="margin-top:16px">
      <button class="btn btn-danger" onclick="cancelSub()">Cancelar assinatura</button>
    </div>` : ''}}
  `;
}}

function renderUsage() {{
  const u = currentStatus.usage;
  if (!u) return;
  const pct = u.limit === -1 ? 0 : Math.min(100, Math.round(u.used / u.limit * 100));
  const limitLabel = u.limit === -1 ? '∞' : u.limit.toLocaleString('pt-BR');
  const color = pct >= 90 ? '#ef4444' : pct >= 70 ? '#f59e0b' : '#7c3aed';
  document.getElementById('usage-area').innerHTML = `
    <div style="margin-bottom:8px;font-size:.9rem;color:#94a3b8">
      <strong style="color:#e2e8f0">${{u.used.toLocaleString('pt-BR')}}</strong> de ${{limitLabel}} mensagens — ${{u.month}}
    </div>
    ${{u.limit !== -1 ? `
    <div class="progress-bar"><div class="progress-fill" style="width:${{pct}}%;background:${{color}}"></div></div>
    <div style="text-align:right;font-size:.75rem;color:#64748b;margin-top:4px">${{pct}}%</div>` : '<p style="color:#4ade80">✓ Mensagens ilimitadas</p>'}}
  `;
}}

function renderPlans() {{
  const currentPlan = (currentStatus.plan || {{}}).id || 'starter';
  document.getElementById('plans-grid').innerHTML = PLANS.map(p => `
    <div class="plan-card ${{p.id === currentPlan ? 'current' : ''}}" onclick="selectPlan('${{p.id}}')">
      <div class="plan-name">${{p.name}}</div>
      <div class="plan-price">R$ ${{p.price_monthly.toFixed(0)}} <span>/mês</span></div>
      <div style="font-size:.82rem;color:#64748b;margin-bottom:8px">
        ${{p.msg_limit === -1 ? 'Mensagens ilimitadas' : p.msg_limit.toLocaleString('pt-BR') + ' msgs/mês'}} ·
        ${{p.agent_limit === -1 ? 'Agentes ilimitados' : p.agent_limit + ' agentes'}}
      </div>
      <ul class="plan-features">
        ${{p.features.map(f => `<li>${{f.replace(/_/g,' ')}}</li>`).join('')}}
      </ul>
      ${{p.id !== currentPlan ? `<button class="btn btn-primary" style="margin-top:16px;width:100%" onclick="selectPlan('${{p.id}}');event.stopPropagation()">Assinar</button>` : '<p style="margin-top:12px;font-size:.8rem;color:#4ade80">✓ Plano ativo</p>'}}
    </div>
  `).join('');
}}

async function selectPlan(planId) {{
  if ((currentStatus.plan || {{}}).id === planId) return;
  if (!confirm(`Ativar o plano ${{planId.toUpperCase()}}?`)) return;
  const r = await fetch(`/billing/checkout?token=${{TOKEN}}`, {{
    method: 'POST',
    headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{owner_id: OWNER_ID, plan_id: planId}})
  }});
  const res = await r.json();
  if (res.checkout_url) {{
    window.location.href = res.checkout_url;
  }} else {{
    alert('Erro ao criar checkout: ' + (res.detail || JSON.stringify(res)));
  }}
}}

async function cancelSub() {{
  if (!confirm('Tem certeza que deseja cancelar a assinatura?')) return;
  const r = await fetch(`/billing/cancel?token=${{TOKEN}}&owner_id=${{OWNER_ID}}`, {{method:'POST'}});
  const res = await r.json();
  if (res.status === 'canceled') {{
    alert('Assinatura cancelada.');
    loadStatus();
  }} else {{
    alert('Erro: ' + (res.detail || JSON.stringify(res)));
  }}
}}

async function loadHistory() {{
  if (!OWNER_ID) return;
  const r = await fetch(`/panel/leads?token=${{TOKEN}}&owner_id=${{OWNER_ID}}&limit=1`); // just to test auth
  // Load subscriptions history
  const rb = await fetch(`/billing/status?token=${{TOKEN}}&owner_id=${{OWNER_ID}}`);
  // history via direct query would need a dedicated endpoint — show placeholder
  document.getElementById('history-area').innerHTML = `<p class="empty" style="color:#475569">Histórico de faturas disponível no portal Stripe.</p>`;
}}

function fmtDate(d) {{
  if (!d) return '—';
  return new Date(d).toLocaleDateString('pt-BR');
}}

// Init
renderPlans();
if (OWNER_ID) {{
  loadStatus().then(loadHistory);
}} else {{
  document.querySelectorAll('.section').forEach(s => s.innerHTML += '<p class="empty">Informe owner_id na URL</p>');
}}
</script>
</body>
</html>"""
