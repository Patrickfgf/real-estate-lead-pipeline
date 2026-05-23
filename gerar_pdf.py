# -*- coding: utf-8 -*-
"""Gera o PDF do passo a passo anti-procrastinacao do Projeto Jare.

Ingestao via APIs OFICIAIS (webhook do Wimoveis + API do DFImoveis),
sem scraping. Pipeline Python para portfolio de DE/DA/DS.
"""
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame,
    Paragraph, Spacer, Table, TableStyle, KeepTogether, PageBreak)

# ---- paleta (terracota + verde, clima quente) -------------------------------
INK       = HexColor("#2B2620")
PRIMARY   = HexColor("#C2562B")
PRIMARY_D = HexColor("#9E431F")
SECOND    = HexColor("#2E7D5B")
CREAM     = HexColor("#FBF6EF")
LIGHT     = HexColor("#EFE7DA")
MUTED     = HexColor("#8A7E70")

def fmt(t):
    """Realca trechos de `codigo` em negrito/terracota."""
    return re.sub(r'`([^`]+)`', r'<font color="#9E431F"><b>\1</b></font>', t)

# ---- estilos ----------------------------------------------------------------
S = dict(
    cover_title=ParagraphStyle('ct', fontName='Helvetica-Bold', fontSize=42, leading=46, textColor=INK),
    cover_sub  =ParagraphStyle('cs', fontName='Helvetica', fontSize=13, leading=18, textColor=MUTED),
    box_title  =ParagraphStyle('bt', fontName='Helvetica-Bold', fontSize=11, leading=15, textColor=PRIMARY_D),
    box_body   =ParagraphStyle('bb', fontName='Helvetica', fontSize=9.5, leading=15, textColor=INK),
    quote      =ParagraphStyle('q',  fontName='Helvetica-Oblique', fontSize=11, leading=16, textColor=INK),
    stat       =ParagraphStyle('st', fontName='Helvetica-Bold', fontSize=10.5, leading=15, textColor=SECOND),
    phase_t    =ParagraphStyle('pt', fontName='Helvetica-Bold', fontSize=12.5, leading=15, textColor=white),
    phase_m    =ParagraphStyle('pm', fontName='Helvetica-Bold', fontSize=9, leading=12,
                               textColor=HexColor("#FBE3D6"), alignment=TA_RIGHT),
    step_t     =ParagraphStyle('s_t', fontName='Helvetica-Bold', fontSize=10, leading=13.5, textColor=INK),
    step_d     =ParagraphStyle('s_d', fontName='Helvetica', fontSize=9, leading=12.5, textColor=MUTED),
)

USABLE = 178 * mm

# ---- componentes ------------------------------------------------------------
def hrule(color=PRIMARY, w=46*mm, thick=2.4):
    t = Table([['']], colWidths=[w], rowHeights=[thick])
    t.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), color)]))
    return t

def accent_box(flowables, stripe=PRIMARY, bg=CREAM):
    inner = Table([['', flowables]], colWidths=[3*mm, USABLE-3*mm])
    inner.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,0), stripe),
        ('BACKGROUND', (1,0), (1,0), bg),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (1,0), (1,0), 10),
        ('RIGHTPADDING', (1,0), (1,0), 10),
        ('TOPPADDING', (1,0), (1,0), 9),
        ('BOTTOMPADDING', (1,0), (1,0), 9),
        ('LEFTPADDING', (0,0), (0,0), 0),
        ('RIGHTPADDING', (0,0), (0,0), 0),
    ]))
    return inner

def checkbox():
    c = Table([['']], colWidths=[4.8*mm], rowHeights=[4.8*mm])
    c.setStyle(TableStyle([('BOX', (0,0), (-1,-1), 1, PRIMARY)]))
    return c

def step(n, title, time, desc):
    badge = Table([[str(n)]], colWidths=[9*mm], rowHeights=[9*mm])
    badge.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), PRIMARY),
        ('TEXTCOLOR', (0,0), (-1,-1), white),
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 12),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    meta = '<b><font color="#2E7D5B">%s</font></b>' % time
    if desc:
        meta += '&#160;&#160;&#160;' + fmt(desc)
    content = [Paragraph(fmt(title), S['step_t']),
               Spacer(1, 1.6*mm),
               Paragraph(meta, S['step_d'])]
    row = Table([[checkbox(), badge, content]], colWidths=[8*mm, 11*mm, 159*mm])
    row.setStyle(TableStyle([
        ('BACKGROUND', (2,0), (2,0), CREAM),
        ('VALIGN', (0,0), (1,0), 'MIDDLE'),
        ('VALIGN', (2,0), (2,0), 'TOP'),
        ('ALIGN', (0,0), (0,0), 'CENTER'),
        ('LEFTPADDING', (0,0), (0,0), 0),
        ('RIGHTPADDING', (0,0), (0,0), 0),
        ('LEFTPADDING', (2,0), (2,0), 10),
        ('RIGHTPADDING', (2,0), (2,0), 10),
        ('TOPPADDING', (2,0), (2,0), 8),
        ('BOTTOMPADDING', (2,0), (2,0), 8),
        ('TOPPADDING', (1,0), (1,0), 0),
    ]))
    return row

def phase_header(num, title, meta):
    left = Paragraph('FASE %02d&#160;&#160;&#160;&#160;%s' % (num, title), S['phase_t'])
    right = Paragraph(meta, S['phase_m'])
    h = Table([[left, right]], colWidths=[128*mm, 50*mm])
    h.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), INK),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (0,0), 11),
        ('RIGHTPADDING', (1,0), (1,0), 11),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    return h

# ---- conteudo ---------------------------------------------------------------
PHASES = [
 (0, "Preparar o terreno", "~22 min - 8 passos", [
    ("Criar a pasta `Jare/` em Documentos.", "1 min", "So a pasta. Pronto = ela existe. Comecar ja e metade do caminho."),
    ("Abrir o terminal dentro da pasta e rodar `git init`.", "2 min", "Pronto = aparece 'Initialized empty Git repository'."),
    ("Criar o ambiente virtual: `python -m venv .venv` e ativar.", "3 min", "Ativo = aparece `(.venv)` no inicio da linha do terminal."),
    ("Criar `requirements.txt` com as libs base.", "3 min", "fastapi, uvicorn, requests, pandas, duckdb, python-dotenv, streamlit, plotly, pydantic. So listar."),
    ("Instalar tudo: `pip install -r requirements.txt`.", "5 min", "Deixa rodando. Pronto = terminou sem erro vermelho."),
    ("Criar as pastas `src/`, `data/raw/`, `notebooks/` e um `README.md` vazio.", "3 min", "Esqueleto do projeto montado."),
    ("Criar `.gitignore` com `.venv/`, `.env` e `data/`.", "2 min", "Evita subir lixo e segredo pro GitHub."),
    ("Primeiro commit, criar o repo no GitHub e dar push.", "3 min", "Pronto = repo online. Marco psicologico forte."),
 ]),
 (1, "Garantir os acessos oficiais", "~16 min - 4 passos", [
    ("Enviar o email de ativacao do webhook pro suporte do Wimoveis (use `email_wimoveis.txt`).", "5 min", "atendimento@imovelweb.com.br. Sem isso o lead nao chega."),
    ("Enviar o email pedindo credenciais da API do DFImoveis (use `email_dfimoveis.txt`).", "5 min", "API oficial (HTTP GET + token). Scraping e proibido no termo de uso deles."),
    ("Ler a doc do payload do Wimoveis e anotar os campos.", "3 min", "LeadName, LeadEmail, LeadTelephone, Message, ExternalId, BusinessType, BrokerEmail, LeadOrigin."),
    ("Montar um lead de exemplo em `data/raw/lead_exemplo.json`.", "3 min", "Pra desenvolver tudo sem depender do suporte responder."),
 ]),
 (2, "Receber leads do Wimoveis (webhook)", "~25 min - 6 passos", [
    ("Criar `src/api.py` com FastAPI e a rota `POST /webhook/wimoveis`.", "5 min", "Rodar com `uvicorn src.api:app --reload`."),
    ("Logar no terminal o JSON que chega na rota.", "5 min", "Pronto = voce ve o payload aparecendo."),
    ("Validar o lead com um modelo Pydantic.", "5 min", "Garante que todo lead tem nome, contato e ExternalId."),
    ("Testar enviando o `lead_exemplo.json` com `curl` (ou Thunder Client).", "5 min", "Pronto = retorna 200 e o lead foi logado."),
    ("Salvar cada lead recebido em `data/raw/leads.jsonl`.", "3 min", "Uma linha por lead. Nada se perde."),
    ("Commit: 'webhook wimoveis recebendo leads'.", "2 min", ""),
 ]),
 (3, "Puxar leads do DFImoveis (API)", "~22 min - 5 passos", [
    ("Criar `src/pull_dfimoveis.py` que faz o GET na API com o token do `.env`.", "5 min", ""),
    ("Parsear a resposta JSON virando uma lista de leads.", "5 min", ""),
    ("Padronizar pro mesmo formato de campos do Wimoveis.", "5 min", "Mesmos nomes de campo nas duas fontes."),
    ("Marcar `fonte = 'dfimoveis'` e anexar no `leads.jsonl`.", "5 min", ""),
    ("Commit: 'integracao api dfimoveis'.", "2 min", ""),
 ]),
 (4, "Guardar em banco", "~22 min - 5 passos", [
    ("Criar `src/db.py` conectando num DuckDB local `data/jare.duckdb`.", "5 min", "DuckDB = SQL local, zero configuracao."),
    ("Definir o schema da tabela `leads`.", "5 min", "external_id, fonte, nome, email, telefone, mensagem, tipo_negocio, corretor, recebido_em."),
    ("Funcao `salvar_leads(lista)` que insere os dados.", "5 min", ""),
    ("Carregar o `leads.jsonl` no banco e conferir com um `SELECT count(*)`.", "5 min", "Pronto = o numero bate."),
    ("Commit: 'persistencia em duckdb'.", "2 min", ""),
 ]),
 (5, "Limpar, deduplicar e enriquecer", "~21 min - 5 passos", [
    ("Carregar a tabela num DataFrame pandas em `notebooks/explorar.ipynb`.", "3 min", ""),
    ("Deduplicar pelo `external_id` (o mesmo lead pode chegar 2x).", "5 min", "Um lead nao pode virar dois cards."),
    ("Normalizar telefone (so digitos, com DDD) e e-mail (minusculo).", "5 min", ""),
    ("Enriquecer: extrair DDD/cidade e criar a flag `tem_contato_valido`.", "5 min", ""),
    ("Salvar a tabela limpa `leads_clean` e dar commit.", "3 min", ""),
 ]),
 (6, "Pontuar os leads (Data Science)", "~17 min - 4 passos", [
    ("Definir 3 ou 4 criterios de prioridade do lead.", "5 min", "Tem telefone, mensagem detalhada, tipo de negocio, recencia."),
    ("Criar `src/score.py` com pontuacao por regras, de 0 a 100.", "5 min", "Comeca simples. Modelo de ML fica pra v2."),
    ("Adicionar a coluna `score` e ordenar do mais quente pro mais frio.", "5 min", ""),
    ("Commit: 'lead scoring por regras'.", "2 min", "Anote no README que da pra evoluir pra modelo."),
 ]),
 (7, "Entregar no Trello (cliente)", "~35 min - 8 passos", [
    ("Criar o board com as listas: Novos, Em contato, Visita, Proposta, Ganhos.", "5 min", ""),
    ("Gerar a API key e o token do Trello.", "5 min", "Em trello.com/app-key."),
    ("Salvar as credenciais (Trello e tokens das fontes) no `.env`.", "3 min", "Nunca commitar esse arquivo."),
    ("Criar `src/trello.py` e fazer 1 card de teste via API.", "5 min", "Pronto = o card aparece no board."),
    ("Mapear lead em card: nome, contato, mensagem, corretor e o score como label.", "5 min", "Bate exatamente com o pedido do cliente."),
    ("Criar card so pra lead novo (marcador `jare-ext:<external_id>` na descricao).", "5 min", "Mesma logica de dedup que voce ja tinha pensado."),
    ("Ligar tudo: lead chega no webhook e o card nasce no Trello sozinho.", "5 min", "Momento 'uau' pra mostrar pro cliente."),
    ("Commit: 'integracao com o trello'.", "2 min", ""),
 ]),
 (8, "Dashboard (Data Analytics)", "~27 min - 6 passos", [
    ("Criar `app.py` com Streamlit e rodar `streamlit run app.py`.", "5 min", "Pronto = abre sozinho no navegador."),
    ("Mostrar total de leads, por fonte e por corretor.", "5 min", ""),
    ("Grafico: leads recebidos por dia.", "5 min", ""),
    ("Grafico: leads por tipo de negocio e por faixa de score.", "5 min", ""),
    ("Adicionar filtros por fonte, corretor e periodo.", "5 min", ""),
    ("Commit: 'dashboard em streamlit'.", "2 min", ""),
 ]),
 (9, "Orquestrar e publicar (Data Engineering)", "~27 min - 6 passos", [
    ("Criar `run_pipeline.py`: ler novos leads, limpar, pontuar, enviar pro Trello.", "5 min", ""),
    ("Adicionar logging e `try/except` por etapa.", "5 min", "Pra saber exatamente onde quebrou."),
    ("Subir o FastAPI no seu VPS Hetzner (reaproveita o Caddy/HTTPS ja planejado).", "5 min", "O webhook precisa de URL publica com https."),
    ("Agendar o `pull_dfimoveis` a cada 5 min (cron no VPS ou Agendador).", "5 min", "DFImoveis e polling."),
    ("Testar ponta a ponta: lead de teste, card no Trello, ponto no dashboard.", "5 min", "Pronto = roda sozinho."),
    ("Commit: 'pipeline no ar'.", "2 min", ""),
 ]),
 (10, "Empacotar pro portfolio", "~26 min - 6 passos", [
    ("README: o que e, o problema que resolve, e a frase-pipeline la no topo.", "5 min", ""),
    ("Desenhar o diagrama de arquitetura (Excalidraw ou Mermaid).", "5 min", "Webhook + API, banco, score, Trello e dashboard."),
    ("Tirar prints do dashboard e dos cards no Trello pro README.", "5 min", ""),
    ("Listar as skills no README.", "3 min", "Python, FastAPI, webhooks, integracao de APIs, DuckDB/SQL, pandas, Streamlit, deploy/orquestracao."),
    ("Publicar o repo e postar o projeto no LinkedIn com a frase-pipeline.", "5 min", "Recrutador precisa conseguir achar."),
    ("Commit final e revisar o repo publico pelo celular.", "3 min", "Ultima conferida. Entregue!"),
 ]),
]

# ---- montagem ---------------------------------------------------------------
def build():
    story = []

    # capa
    story.append(Spacer(1, 24*mm))
    story.append(Paragraph('Projeto <font color="#C2562B">Jar&#233;</font>', S['cover_title']))
    story.append(Spacer(1, 3*mm))
    story.append(hrule())
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph("Passo a passo anti-procrastinacao para construir o pipeline "
                           "de leads imobiliarios em Python.", S['cover_sub']))
    story.append(Spacer(1, 9*mm))

    frase = ('<b>A frase-pipeline (cole no README e no LinkedIn):</b><br/><br/>'
             '"Pipeline que <b>ingere</b> leads de 2 portais imobiliarios via '
             '<b>webhook e API oficial</b>, <b>limpa, deduplica e enriquece</b> os dados, '
             '<b>armazena</b> em banco, <b>pontua</b> a qualidade do lead e <b>carrega</b> '
             'no Trello e num dashboard, <b>orquestrado</b> num servidor."')
    story.append(accent_box([Paragraph(frase, S['quote'])], stripe=SECOND, bg=HexColor("#F1F6F2")))
    story.append(Spacer(1, 7*mm))

    regras = [
        Paragraph("Como usar este guia", S['box_title']),
        Spacer(1, 3*mm),
        Paragraph("1. &#160;Faca <b>um passo por vez</b>. Olhe so o proximo, nunca a lista inteira.", S['box_body']),
        Paragraph("2. &#160;Cada passo = <b>no maximo 5 min</b> pra comecar. Se engrenar, continue.", S['box_body']),
        Paragraph("3. &#160;Marque o quadradinho ao terminar. Aquele tique solta dopamina.", S['box_body']),
        Paragraph("4. &#160;Travou? Faca a <b>versao porca</b> e siga. Refina depois.", S['box_body']),
        Paragraph("5. &#160;Faca <b>commit a cada fase</b>. Progresso visivel mata a procrastinacao.", S['box_body']),
    ]
    story.append(accent_box(regras))
    story.append(Spacer(1, 7*mm))
    story.append(Paragraph("63 passos &#160;&#8226;&#160; ~4h de trabalho focado, fatiado em pedacos "
                           "de no maximo 5 min cada.", S['stat']))
    story.append(PageBreak())

    # fases
    n = 1
    for num, title, meta, steps in PHASES:
        block = [phase_header(num, title, meta), Spacer(1, 3*mm), step(n, *steps[0])]
        story.append(KeepTogether(block))
        story.append(Spacer(1, 3*mm))
        n += 1
        for st in steps[1:]:
            story.append(step(n, *st))
            story.append(Spacer(1, 3*mm))
            n += 1
        story.append(Spacer(1, 5*mm))

    return story

def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LIGHT)
    canvas.setLineWidth(0.7)
    canvas.line(16*mm, 13.5*mm, (210-16)*mm, 13.5*mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(16*mm, 9*mm, "Projeto Jare  -  passo a passo anti-procrastinacao")
    canvas.drawRightString((210-16)*mm, 9*mm, str(doc.page))
    canvas.restoreState()

def main():
    out = r"C:\Users\patrick\Documents\Projeto_Jaré\Jare_passo_a_passo.pdf"
    doc = BaseDocTemplate(out, pagesize=A4,
                          leftMargin=16*mm, rightMargin=16*mm,
                          topMargin=20*mm, bottomMargin=18*mm,
                          title="Projeto Jare - passo a passo", author="Jare")
    frame = Frame(16*mm, 18*mm, USABLE, 297*mm-20*mm-18*mm, id='main',
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='all', frames=[frame], onPage=footer)])
    doc.build(build())
    print("OK ->", out)

if __name__ == "__main__":
    main()
