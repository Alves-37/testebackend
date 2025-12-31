from io import BytesIO, StringIO
from typing import List
from datetime import datetime, timedelta
import uuid
import csv
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.database import get_db_session
from app.db.models import Produto, Venda, ItemVenda, User, Cliente, EmpresaConfig

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib import colors

router = APIRouter(prefix="/api/relatorios", tags=["relatorios"])


LOGO_PATH = Path(__file__).resolve().parents[2] / "img" / "nelson.jpg"


def _add_header(story, styles, titulo: str, subtitulo: str | None = None, empresa: EmpresaConfig | None = None):
    """Adiciona cabeçalho padrão com logo + dados da empresa + título/subtítulo."""
    # Logo (se existir)
    if LOGO_PATH.exists():
        try:
            logo = Image(str(LOGO_PATH), width=25 * mm, height=25 * mm)
            story.append(logo)
            story.append(Spacer(1, 4))
        except Exception:
            pass

    # Dados da empresa
    if empresa is not None:
        nome = (empresa.nome or "").strip()
        linha1 = nome or ""

        detalhes = []
        if empresa.nuit:
            detalhes.append(f"NUIT: {empresa.nuit}")
        if empresa.telefone:
            detalhes.append(f"Tel: {empresa.telefone}")
        if empresa.email:
            detalhes.append(f"Email: {empresa.email}")

        linha2 = " | ".join(detalhes) if detalhes else ""
        linha3 = (empresa.endereco or "").strip()

        if linha1:
            story.append(Paragraph(linha1, styles["Heading3"]))
        if linha2:
            story.append(Paragraph(linha2, styles["Normal"]))
        if linha3:
            story.append(Paragraph(linha3, styles["Normal"]))
        if linha1 or linha2 or linha3:
            story.append(Spacer(1, 6))

    # Título e subtítulo do relatório
    story.append(Paragraph(titulo, styles["Title"]))
    if subtitulo:
        story.append(Paragraph(subtitulo, styles["Normal"]))

    story.append(Spacer(1, 8))


def _build_produtos_pdf(produtos: List[Produto], titulo: str, empresa: EmpresaConfig | None = None) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=20 * mm, bottomMargin=20 * mm)

    styles = getSampleStyleSheet()
    story = []

    _add_header(story, styles, titulo, empresa=empresa)

    data = [["Código", "Nome", "Preço venda", "Estoque", "Estoque mín."]]
    for p in produtos:
        data.append([
            p.codigo or "",
            p.nome or "",
            f"MT {p.preco_venda:,.2f}",
            f"{p.estoque}",
            f"{p.estoque_minimo}",
        ])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
    ]))

    story.append(table)
    doc.build(story)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf


@router.get("/produtos", response_class=StreamingResponse)
async def relatorio_produtos(baixo_estoque: bool = False, db: AsyncSession = Depends(get_db_session)):
    stmt = select(Produto).where(Produto.ativo == True)
    result = await db.execute(stmt)
    produtos = result.scalars().all()

    if baixo_estoque:
        def is_baixo(p: Produto) -> bool:
            # Ignorar categoria "Serviços" (id 15) no cálculo de baixo estoque
            try:
                if getattr(p, "categoria_id", None) == 15:
                    return False
            except Exception:
                pass

            estoque = float(p.estoque or 0)
            minimo = float(p.estoque_minimo or 0)
            return (minimo > 0 and estoque <= minimo) or (minimo <= 0 and estoque <= 5)

        produtos = [p for p in produtos if is_baixo(p)]

    # Buscar dados da empresa
    cfg_result = await db.execute(select(EmpresaConfig))
    empresa = cfg_result.scalars().first()

    titulo = "Produtos" if not baixo_estoque else "Produtos com baixo estoque"
    pdf_bytes = _build_produtos_pdf(produtos, titulo, empresa=empresa)

    filename = "produtos.pdf" if not baixo_estoque else "produtos_baixo_estoque.pdf"

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _parse_date_ymd(value: str) -> datetime:
    try:
        return datetime.fromisoformat(f"{value}T00:00:00")
    except Exception:
        raise HTTPException(status_code=400, detail="Parâmetro de data inválido. Use YYYY-MM-DD")


@router.get("/vendas", response_class=StreamingResponse)
async def relatorio_vendas(
    data_inicio: str,
    data_fim: str,
    usuario_id: str | None = None,
    db: AsyncSession = Depends(get_db_session),
):
    """Relatório detalhado de vendas em PDF para o período/usuário informado."""
    d1 = _parse_date_ymd(data_inicio)
    d2 = _parse_date_ymd(data_fim)
    d2_exclusive = d2 + timedelta(days=1)

    stmt = (
        select(Venda)
        .options(
            selectinload(Venda.itens).selectinload(ItemVenda.produto),
            selectinload(Venda.cliente),
            selectinload(Venda.usuario),
        )
        .where(Venda.created_at >= d1, Venda.created_at < d2_exclusive, Venda.cancelada == False)
    )

    if usuario_id is not None:
        try:
            uid = uuid.UUID(usuario_id)
            stmt = stmt.where(Venda.usuario_id == uid)
        except Exception:
            pass

    result = await db.execute(stmt)
    vendas = result.scalars().all()

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm)
    styles = getSampleStyleSheet()
    story = []

    # Dados da empresa
    cfg_result = await db.execute(select(EmpresaConfig))
    empresa = cfg_result.scalars().first()

    titulo = "Relatório de Vendas"
    subtitulo = f"Período: {data_inicio} a {data_fim}"
    _add_header(story, styles, titulo, subtitulo, empresa=empresa)

    header = ["Data", "Vendedor", "Cliente", "Forma pag.", "Total (MT)"]
    data = [header]
    total_geral = 0.0

    for v in vendas:
        dt = getattr(v, "created_at", None)
        data_str = dt.strftime("%Y-%m-%d %H:%M") if isinstance(dt, datetime) else ""
        vendedor = getattr(getattr(v, "usuario", None), "nome", "") or "-"
        cliente = getattr(getattr(v, "cliente", None), "nome", "") or "-"
        forma = v.forma_pagamento or "-"
        total = float(v.total or 0)
        total_geral += total
        data.append([data_str, vendedor, cliente, forma, f"MT {total:,.2f}"])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
    ]))

    story.append(table)
    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Total geral: MT {total_geral:,.2f}", styles["Heading3"]))

    # Tabela de itens vendidos (detalhe por produto)
    itens_header = ["Data", "Produto", "Qtd", "Preço unit.", "Subtotal (MT)"]
    itens_data = [itens_header]

    for v in vendas:
        dt = getattr(v, "created_at", None)
        data_str = dt.strftime("%Y-%m-%d %H:%M") if isinstance(dt, datetime) else ""
        for it in getattr(v, "itens", []) or []:
            prod_nome = getattr(getattr(it, "produto", None), "nome", "") or "-"
            qtd = float(getattr(it, "peso_kg", 0) or 0) if getattr(it, "peso_kg", 0) else float(getattr(it, "quantidade", 0) or 0)
            preco_unit = float(getattr(it, "preco_unitario", 0) or 0)
            subtotal = float(getattr(it, "subtotal", 0) or 0)
            itens_data.append([
                data_str,
                prod_nome,
                f"{qtd:,.2f}",
                f"MT {preco_unit:,.2f}",
                f"MT {subtotal:,.2f}",
            ])

    if len(itens_data) > 1:
        story.append(Spacer(1, 12))
        story.append(Paragraph("Itens vendidos", styles["Heading3"]))
        itens_table = Table(itens_data, repeatRows=1)
        itens_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
        ]))
        story.append(itens_table)

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=vendas_periodo.pdf"},
    )


@router.get("/financeiro", response_class=StreamingResponse)
async def relatorio_financeiro(
    data_inicio: str,
    data_fim: str,
    usuario_id: str | None = None,
    db: AsyncSession = Depends(get_db_session),
):
    """Relatório financeiro resumido (faturamento, custo, lucro, ticket etc.) em PDF."""
    d1 = _parse_date_ymd(data_inicio)
    d2 = _parse_date_ymd(data_fim)
    d2_exclusive = d2 + timedelta(days=1)

    # Buscar vendas do período
    stmt_v = (
        select(Venda)
        .options(selectinload(Venda.itens))
        .where(Venda.created_at >= d1, Venda.created_at < d2_exclusive, Venda.cancelada == False)
    )
    if usuario_id is not None:
        try:
            uid = uuid.UUID(usuario_id)
            stmt_v = stmt_v.where(Venda.usuario_id == uid)
        except Exception:
            pass

    result_v = await db.execute(stmt_v)
    vendas = result_v.scalars().all()

    # Mapear custos por produto
    result_p = await db.execute(select(Produto))
    produtos = result_p.scalars().all()
    custo_por_produto = {str(p.id): float(p.preco_custo or 0) for p in produtos}

    faturamento = 0.0
    custo_total = 0.0
    itens_total = 0.0

    for v in vendas:
        for it in getattr(v, "itens", []) or []:
            pid = str(it.produto_id)
            preco_unit = float(it.preco_unitario or 0)
            qtd = float(it.peso_kg or 0) if getattr(it, "peso_kg", 0) else float(it.quantidade or 0)
            try:
                custo_unit_item = float(getattr(it, 'preco_custo_unitario', 0) or 0)
            except Exception:
                custo_unit_item = 0.0
            custo_unit_prod = float(custo_por_produto.get(pid, 0))
            custo_unit = custo_unit_item if custo_unit_item > 1e-9 else custo_unit_prod
            faturamento += preco_unit * qtd
            custo_total += custo_unit * qtd
            itens_total += qtd

    lucro = faturamento - custo_total
    qtd_vendas = len(vendas)
    ticket_medio = faturamento / qtd_vendas if qtd_vendas > 0 else 0.0

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    story = []

    # Dados da empresa
    cfg_result = await db.execute(select(EmpresaConfig))
    empresa = cfg_result.scalars().first()

    titulo = "Relatório Financeiro"
    subtitulo = f"Período: {data_inicio} a {data_fim}"
    _add_header(story, styles, titulo, subtitulo, empresa=empresa)

    rows = [
        ["Faturamento", f"MT {faturamento:,.2f}"],
        ["Custo", f"MT {custo_total:,.2f}"],
        ["Lucro", f"MT {lucro:,.2f}"],
        ["Qtd. vendas", str(qtd_vendas)],
        ["Ticket médio", f"MT {ticket_medio:,.2f}"],
        ["Itens vendidos", f"{itens_total:,.2f}"],
    ]

    table = Table(rows, colWidths=[80 * mm, 80 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f766e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
    ]))

    story.append(table)
    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=relatorio_financeiro.pdf"},
    )


@router.get("/faturas-mensal", response_class=StreamingResponse)
async def exportar_faturas_mensal(
    ano: int,
    mes: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Exporta faturas (vendas não canceladas) de um mês em CSV para apoio contabilístico/AT.

    Ainda não é SAF-T, mas já consolida os dados fiscais básicos por documento.
    """
    try:
        # Período [inicio, fim+1d)
        d1 = datetime(ano, mes, 1)
        if mes == 12:
            d2 = datetime(ano + 1, 1, 1)
        else:
            d2 = datetime(ano, mes + 1, 1)
    except Exception:
        raise HTTPException(status_code=400, detail="Parâmetros de ano/mês inválidos")

    stmt = (
        select(Venda)
        .options(selectinload(Venda.itens), selectinload(Venda.cliente), selectinload(Venda.usuario))
        .where(Venda.created_at >= d1, Venda.created_at < d2, Venda.cancelada == False)
        .order_by(Venda.created_at.asc())
    )

    result = await db.execute(stmt)
    vendas = result.scalars().all()

    # Usar StringIO para escrever texto e depois codificar para bytes
    text_buffer = StringIO()
    writer = csv.writer(text_buffer, delimiter=';', lineterminator='\n')

    # Cabeçalho CSV
    writer.writerow([
        "data_hora",
        "id_venda",
        "vendedor",
        "cliente_nome",
        "cliente_documento",
        "forma_pagamento",
        "total",
        "desconto",
        "observacoes",
    ])

    for v in vendas:
        dt = getattr(v, "created_at", None)
        data_str = dt.strftime("%Y-%m-%d %H:%M:%S") if isinstance(dt, datetime) else ""
        vendedor = getattr(getattr(v, "usuario", None), "nome", "") or "-"
        cliente_nome = getattr(getattr(v, "cliente", None), "nome", "") or "-"
        cliente_doc = getattr(getattr(v, "cliente", None), "documento", "") or ""
        forma = v.forma_pagamento or "-"
        total = float(v.total or 0)
        desconto = float(v.desconto or 0)
        obs = v.observacoes or ""

        writer.writerow([
            data_str,
            str(getattr(v, "id", "")),
            vendedor,
            cliente_nome,
            cliente_doc,
            forma,
            f"{total:.2f}",
            f"{desconto:.2f}",
            obs.replace('\n', ' ').replace('\r', ' '),
        ])

    csv_text = text_buffer.getvalue()
    csv_bytes = csv_text.encode('utf-8')
    buffer = BytesIO(csv_bytes)
    filename = f"faturas_{ano}_{mes:02d}.csv"

    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache",
        },
    )


@router.get("/iva")
async def resumo_iva(
    data_inicio: str,
    data_fim: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Resumo de IVA por taxa em um período (base, imposto e faturamento)."""
    d1 = _parse_date_ymd(data_inicio)
    d2 = _parse_date_ymd(data_fim)
    d2_exclusive = d2 + timedelta(days=1)

    # Buscar itens de vendas não canceladas no período
    stmt = (
        select(ItemVenda)
        .join(Venda, ItemVenda.venda_id == Venda.id)
        .where(
            Venda.created_at >= d1,
            Venda.created_at < d2_exclusive,
            Venda.cancelada == False,
        )
    )

    result = await db.execute(stmt)
    itens = result.scalars().all()

    resumo: dict[float, dict] = {}
    for it in itens:
        taxa = float(getattr(it, "taxa_iva", 0.0) or 0.0)
        base = float(getattr(it, "base_iva", 0.0) or 0.0)
        iva = float(getattr(it, "valor_iva", 0.0) or 0.0)
        if taxa not in resumo:
            resumo[taxa] = {"taxa_iva": taxa, "base_total": 0.0, "iva_total": 0.0}
        resumo[taxa]["base_total"] += base
        resumo[taxa]["iva_total"] += iva

    # Converter para lista ordenada por taxa
    resultado = []
    for taxa in sorted(resumo.keys()):
        base_total = resumo[taxa]["base_total"]
        iva_total = resumo[taxa]["iva_total"]
        faturamento_total = base_total + iva_total
        resultado.append(
            {
                "taxa_iva": taxa,
                "base_total": base_total,
                "iva_total": iva_total,
                "faturamento_total": faturamento_total,
            }
        )

    return {"data_inicio": data_inicio, "data_fim": data_fim, "itens": resultado}
