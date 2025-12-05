from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, asc
from typing import Optional, List
from datetime import datetime
import uuid

from app.db.database import get_db_session
from app.db.models import Abastecimento, Produto, User

router = APIRouter(prefix="/api/abastecimentos", tags=["Abastecimentos"]) 

@router.get("/historico")
async def get_historico_abastecimentos(
    data_inicial: Optional[str] = Query(None, description="YYYY-MM-DD"),
    data_final: Optional[str] = Query(None, description="YYYY-MM-DD"),
    usuario_id: Optional[str] = Query(None),
    produto_id: Optional[str] = Query(None),
    pagina: int = Query(1, ge=1),
    limite: int = Query(50, ge=1, le=200),
    ordenacao: str = Query("created_at_desc", pattern="^(created_at_desc|created_at_asc)$"),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        conditions = []

        # Intervalo de datas
        if data_inicial:
            try:
                di = datetime.fromisoformat(data_inicial)
                conditions.append(Abastecimento.created_at >= di)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="data_inicial inválida")
        if data_final:
            try:
                # incluir o dia inteiro
                df = datetime.fromisoformat(data_final)
                conditions.append(Abastecimento.created_at <= df)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="data_final inválida")

        # Filtros opcionais por IDs
        if produto_id:
            try:
                pid = uuid.UUID(produto_id)
                conditions.append(Abastecimento.produto_id == pid)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="produto_id inválido")
        if usuario_id:
            try:
                uid = uuid.UUID(usuario_id)
                conditions.append(Abastecimento.usuario_id == uid)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="usuario_id inválido")

        # Base query
        query = select(Abastecimento)
        if conditions:
            query = query.where(and_(*conditions))

        # Ordenação
        if ordenacao == "created_at_asc":
            query = query.order_by(asc(Abastecimento.created_at))
        else:
            query = query.order_by(desc(Abastecimento.created_at))

        # Paginação
        offset = (pagina - 1) * limite
        result = await db.execute(query.offset(offset).limit(limite + 1))
        rows: List[Abastecimento] = result.scalars().all()
        has_next = len(rows) > limite
        items = rows[:limite]

        def serialize(a: Abastecimento):
            return {
                "id": str(a.id),
                "produto_id": str(a.produto_id),
                "produto_nome": getattr(a.produto, "nome", None),
                "codigo": getattr(a.produto, "codigo", None),
                "quantidade": float(a.quantidade or 0),
                "custo_unitario": float(a.custo_unitario or 0),
                "total_custo": float(a.total_custo or 0),
                "usuario_id": str(a.usuario_id) if a.usuario_id else None,
                "usuario_nome": getattr(a.usuario, "nome", None) if a.usuario else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "observacao": a.observacao,
            }

        payload = [serialize(a) for a in items]

        return {
            "items": payload,
            "pagina": pagina,
            "limite": limite,
            "has_next": has_next,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao buscar histórico: {e}")
