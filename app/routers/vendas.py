from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func
from sqlalchemy.orm import selectinload
from typing import List
import uuid
from datetime import datetime
from pydantic import ValidationError

from ..db.database import get_db_session
from sqlalchemy.exc import IntegrityError
from app.db.models import Produto, Venda, ItemVenda, User
from app.core.realtime import manager as realtime_manager
from ..schemas.venda import VendaCreate, VendaUpdate, VendaResponse

router = APIRouter(prefix="/api/vendas", tags=["vendas"])

@router.get("/", response_model=List[VendaResponse])
async def listar_vendas(db: AsyncSession = Depends(get_db_session)):
    """Lista todas as vendas."""
    try:
        result = await db.execute(
            select(Venda)
            .options(
                selectinload(Venda.itens),
                selectinload(Venda.cliente),
                selectinload(Venda.usuario),
            )
            .where(Venda.cancelada == False)
        )
        vendas = result.scalars().all()
        # Injetar nome do usuário (vendedor) para o schema incluir
        for v in vendas:
            try:
                setattr(v, 'usuario_nome', getattr(getattr(v, 'usuario', None), 'nome', None))
            except Exception:
                setattr(v, 'usuario_nome', None)
        return [VendaResponse.model_validate(v) for v in vendas]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar vendas: {str(e)}")

@router.get("/id/{venda_id}", response_model=VendaResponse)
async def obter_venda(venda_id: str, db: AsyncSession = Depends(get_db_session)):
    """Obtém uma venda específica por UUID."""
    try:
        result = await db.execute(
            select(Venda)
            .options(
                selectinload(Venda.itens),
                selectinload(Venda.cliente),
                selectinload(Venda.usuario),
            )
            .where(Venda.id == venda_id)
        )
        venda = result.scalar_one_or_none()
        
        if not venda:
            raise HTTPException(status_code=404, detail="Venda não encontrada")
        
        try:
            setattr(venda, 'usuario_nome', getattr(getattr(venda, 'usuario', None), 'nome', None))
        except Exception:
            setattr(venda, 'usuario_nome', None)
        return VendaResponse.model_validate(venda)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de venda inválido")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter venda: {str(e)}")

@router.post("/", response_model=VendaResponse)
async def criar_venda(venda: VendaCreate, db: AsyncSession = Depends(get_db_session)):
    """Cria uma nova venda."""
    try:
        # Criar nova venda
        venda_uuid = uuid.uuid4()
        if hasattr(venda, 'uuid') and venda.uuid:
            try:
                venda_uuid = uuid.UUID(venda.uuid)
            except ValueError:
                venda_uuid = uuid.uuid4()

        # Idempotência: se a venda já existir (mesmo UUID), retornar e NÃO baixar estoque novamente.
        try:
            existing = await db.execute(
                select(Venda)
                .options(selectinload(Venda.itens), selectinload(Venda.cliente), selectinload(Venda.usuario))
                .where(Venda.id == venda_uuid)
            )
            venda_existente = existing.scalar_one_or_none()
            if venda_existente:
                try:
                    setattr(venda_existente, 'usuario_nome', getattr(getattr(venda_existente, 'usuario', None), 'nome', None))
                except Exception:
                    setattr(venda_existente, 'usuario_nome', None)
                return VendaResponse.model_validate(venda_existente)
        except Exception:
            pass
        
        # Converter cliente_id se fornecido
        cliente_uuid = None
        if venda.cliente_id:
            try:
                cliente_uuid = uuid.UUID(venda.cliente_id)
            except ValueError:
                cliente_uuid = None
        
        # Converter usuario_id se fornecido
        usuario_uuid = None
        if hasattr(venda, 'usuario_id') and venda.usuario_id:
            try:
                usuario_uuid = uuid.UUID(venda.usuario_id)
            except ValueError:
                usuario_uuid = None

        aplicar_iva = bool(getattr(venda, 'aplicar_iva', True))

        nova_venda = Venda(
            id=venda_uuid,
            usuario_id=usuario_uuid,
            cliente_id=cliente_uuid,
            total=venda.total,
            desconto=venda.desconto or 0.0,
            forma_pagamento=venda.forma_pagamento,
            observacoes=venda.observacoes,
            cancelada=False,
            # Preservar a data original da venda, se enviada pelo cliente
            created_at=venda.created_at if getattr(venda, 'created_at', None) else None
        )
        
        db.add(nova_venda)
        await db.flush()  # Para obter o ID da venda
        
        # Criar itens da venda se fornecidos
        if hasattr(venda, 'itens') and venda.itens:
            for item_data in venda.itens:
                # Validar UUID de produto individualmente para evitar 500 genérico
                try:
                    produto_uuid = uuid.UUID(item_data.produto_id)
                except (ValueError, TypeError):
                    raise HTTPException(status_code=400, detail=f"produto_id inválido: {item_data.produto_id}")

                # Verificar existência do produto para evitar erro de FK
                result_prod = await db.execute(select(Produto).where(Produto.id == produto_uuid))
                produto_db = result_prod.scalar_one_or_none()
                if not produto_db:
                    raise HTTPException(status_code=400, detail=f"Produto inexistente no servidor: {item_data.produto_id}")

                # Calcular IVA com base na taxa do produto
                quantidade = max(1, int(item_data.quantidade or 0))
                peso_kg = getattr(item_data, 'peso_kg', 0.0)
                preco_unitario = float(item_data.preco_unitario)
                subtotal = float(item_data.subtotal)

                # Custo unitário congelado no momento da venda.
                # Preferir valor enviado pelo cliente (PDV3). Se vier 0/None, usar custo atual do produto.
                try:
                    custo_unit = float(getattr(item_data, 'preco_custo_unitario', 0.0) or 0.0)
                except Exception:
                    custo_unit = 0.0
                if custo_unit <= 1e-9:
                    try:
                        custo_unit = float(getattr(produto_db, 'preco_custo', 0.0) or 0.0)
                    except Exception:
                        custo_unit = 0.0

                taxa_iva = float(getattr(produto_db, 'taxa_iva', 0.0) or 0.0)

                # Se o cliente decidiu "Sem IVA", não registrar IVA nesta venda.
                if not aplicar_iva:
                    taxa_iva = 0.0
                    base_iva = subtotal
                    valor_iva = 0.0
                elif taxa_iva > 0:
                    fator = 1 + (taxa_iva / 100.0)
                    base_iva = subtotal / fator
                    valor_iva = subtotal - base_iva
                else:
                    base_iva = subtotal
                    valor_iva = 0.0

                item = ItemVenda(
                    venda_id=nova_venda.id,
                    produto_id=produto_uuid,
                    quantidade=quantidade,
                    peso_kg=peso_kg,
                    preco_unitario=preco_unitario,
                    subtotal=subtotal,
                    preco_custo_unitario=custo_unit,
                    taxa_iva=taxa_iva,
                    base_iva=base_iva,
                    valor_iva=valor_iva,
                )
                db.add(item)

                # Baixar estoque no servidor
                try:
                    nome_prod = str(getattr(produto_db, 'nome', '') or '').strip().lower()
                    codigo_prod = str(getattr(produto_db, 'codigo', '') or '').strip().lower()
                    try:
                        categoria_id_prod = getattr(produto_db, 'categoria_id', None)
                        categoria_id_prod = int(categoria_id_prod) if categoria_id_prod is not None else None
                    except Exception:
                        categoria_id_prod = None

                    is_servico = False
                    if codigo_prod.startswith('srv') or codigo_prod.startswith('serv'):
                        is_servico = True
                    if ('servi' in nome_prod) or ('impress' in nome_prod):
                        # cobre "serviço/servico" e "impressão/impressao"
                        is_servico = True

                    # Categorias alinhadas com PDV3 (ver /api/categorias):
                    # 10=Impressão e Cópias, 14=Gráfica, 15=Serviços
                    if categoria_id_prod in (10, 14, 15):
                        is_servico = True

                    if is_servico:
                        # Serviços não controlam estoque
                        continue

                    delta = float(quantidade)
                    if bool(getattr(produto_db, 'venda_por_peso', False)):
                        try:
                            pk = float(peso_kg or 0.0)
                        except Exception:
                            pk = 0.0
                        if pk > 0:
                            delta = pk
                    try:
                        estoque_atual = float(getattr(produto_db, 'estoque', 0.0) or 0.0)
                    except Exception:
                        estoque_atual = 0.0
                    if estoque_atual < (delta - 1e-9):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Estoque insuficiente para '{produto_db.nome}'. Disponível={estoque_atual}, solicitado={delta}",
                        )
                    setattr(produto_db, 'estoque', estoque_atual - delta)
                except HTTPException:
                    raise
                except Exception as stock_ex:
                    raise HTTPException(status_code=500, detail=f"Falha ao baixar estoque no servidor: {str(stock_ex)}")
        
        await db.commit()
        await db.refresh(nova_venda)

        # Recarregar com relacionamentos para evitar falhas de serialização/response validation
        try:
            result_full = await db.execute(
                select(Venda)
                .options(selectinload(Venda.itens), selectinload(Venda.cliente), selectinload(Venda.usuario))
                .where(Venda.id == nova_venda.id)
            )
            venda_full = result_full.scalar_one_or_none() or nova_venda
        except Exception:
            venda_full = nova_venda

        # Broadcast evento em tempo real para clientes conectados
        try:
            payload = {
                "ts": datetime.utcnow().isoformat(),
                "data": {
                    "id": str(nova_venda.id),
                    "usuario_id": str(nova_venda.usuario_id) if getattr(nova_venda, 'usuario_id', None) else None,
                    "total": float(nova_venda.total or 0),
                    "desconto": float(nova_venda.desconto or 0),
                    "forma_pagamento": nova_venda.forma_pagamento,
                    "created_at": getattr(nova_venda, 'created_at', None).isoformat() if getattr(nova_venda, 'created_at', None) else None,
                }
            }
            await realtime_manager.broadcast("venda.created", payload)
        except Exception:
            # Não falhar a requisição caso broadcast dê erro
            pass

        try:
            try:
                setattr(venda_full, 'usuario_nome', getattr(getattr(venda_full, 'usuario', None), 'nome', None))
            except Exception:
                setattr(venda_full, 'usuario_nome', None)
            return VendaResponse.model_validate(venda_full)
        except ValidationError as ve:
            # Quando a validação de response falha, o FastAPI normalmente retorna 500 genérico.
            # Fornecemos detail explícito para diagnosticar payload/model.
            raise HTTPException(status_code=500, detail=f"Falha ao serializar resposta da venda: {str(ve)}")
    except HTTPException as he:
        # Propagar erros HTTP explícitos (ex.: produto inexistente -> 400)
        await db.rollback()
        raise he
    except IntegrityError as ie:
        # Possíveis causas: UUID duplicado, FK de produto inexistente, etc.
        await db.rollback()
        msg = str(ie.orig) if getattr(ie, 'orig', None) else str(ie)
        # Quando for chave duplicada, retornar 409 para o cliente tratar como 'já existe'
        if "duplicate key" in msg.lower() or "unique" in msg.lower():
            raise HTTPException(status_code=409, detail=f"Venda já existe (conflito de chave): {msg}")
        raise HTTPException(status_code=400, detail=f"Violação de integridade ao criar venda: {msg}")
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao criar venda: {str(e)}")

@router.put("/{venda_id}", response_model=VendaResponse)
async def atualizar_venda(venda_id: str, venda: VendaUpdate, db: AsyncSession = Depends(get_db_session)):
    """Atualiza uma venda existente."""
    try:
        # Buscar venda existente
        result = await db.execute(select(Venda).where(Venda.id == venda_id))
        venda_existente = result.scalar_one_or_none()
        
        if not venda_existente:
            raise HTTPException(status_code=404, detail="Venda não encontrada")
        
        # Atualizar campos
        update_data = {}
        if venda.cliente_id is not None:
            try:
                update_data[Venda.cliente_id] = uuid.UUID(venda.cliente_id) if venda.cliente_id else None
            except ValueError:
                update_data[Venda.cliente_id] = None
        # Atualizar usuario_id (UUID) se fornecido
        if hasattr(venda, 'usuario_id') and venda.usuario_id is not None:
            try:
                update_data[Venda.usuario_id] = uuid.UUID(venda.usuario_id) if venda.usuario_id else None
            except ValueError:
                update_data[Venda.usuario_id] = None
        if venda.total is not None:
            update_data[Venda.total] = venda.total
        if venda.desconto is not None:
            update_data[Venda.desconto] = venda.desconto
        if venda.forma_pagamento is not None:
            update_data[Venda.forma_pagamento] = venda.forma_pagamento
        if venda.observacoes is not None:
            update_data[Venda.observacoes] = venda.observacoes
        if venda.cancelada is not None:
            update_data[Venda.cancelada] = venda.cancelada
        
        update_data[Venda.updated_at] = datetime.utcnow()
        
        # IMPORTANTE: passar o dicionário diretamente (chaves são Column)
        await db.execute(
            update(Venda).where(Venda.id == venda_id).values(update_data)
        )
        await db.commit()
        
        # Retornar venda atualizada
        result = await db.execute(
            select(Venda)
            .options(selectinload(Venda.itens), selectinload(Venda.cliente), selectinload(Venda.usuario))
            .where(Venda.id == venda_id)
        )
        venda_atualizada = result.scalar_one()
        try:
            setattr(venda_atualizada, 'usuario_nome', getattr(getattr(venda_atualizada, 'usuario', None), 'nome', None))
        except Exception:
            setattr(venda_atualizada, 'usuario_nome', None)
        return venda_atualizada
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar venda: {str(e)}")

@router.delete("/{venda_id}")
async def deletar_venda(venda_id: str, db: AsyncSession = Depends(get_db_session)):
    """Deletar uma venda específica."""
    try:
        # Buscar a venda
        stmt = select(Venda).where(Venda.id == venda_id)
        result = await db.execute(stmt)
        venda = result.scalar_one_or_none()
        
        if not venda:
            raise HTTPException(status_code=404, detail="Venda não encontrada")

        # Regra: só pode excluir se já estiver anulada/cancelada
        if not bool(getattr(venda, 'cancelada', False)):
            raise HTTPException(status_code=400, detail="Somente vendas ANULADAS podem ser excluídas")
        
        # Deletar itens da venda primeiro (devido à foreign key)
        stmt_itens = delete(ItemVenda).where(ItemVenda.venda_id == venda_id)
        await db.execute(stmt_itens)
        
        # Deletar a venda
        await db.delete(venda)
        await db.commit()

        # Broadcast realtime: venda deletada (antes do return)
        try:
            await realtime_manager.broadcast("venda.deleted", {
                "ts": datetime.utcnow().isoformat(),
                "data": {"id": str(venda_id)}
            })
        except Exception:
            pass

        return {"message": "Venda deletada com sucesso"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao deletar venda: {str(e)}")

@router.get("/usuario/{usuario_id}")
async def listar_vendas_usuario(
    usuario_id: str,
    data_inicio: str = None,
    data_fim: str = None,
    status_filter: str = None,
    db: AsyncSession = Depends(get_db_session)
):
    """Listar vendas de um usuário específico com filtros opcionais."""
    try:
        # Parse de UUID do usuário (ignorar filtro se inválido)
        usuario_uuid = None
        try:
            usuario_uuid = uuid.UUID(usuario_id) if usuario_id else None
        except Exception:
            usuario_uuid = None

        # Query base
        stmt = select(Venda).options(selectinload(Venda.itens), selectinload(Venda.cliente), selectinload(Venda.usuario))

        # Filtrar por usuário
        if usuario_uuid is not None:
            stmt = stmt.where(Venda.usuario_id == usuario_uuid)

        # Aplicar filtros de data se fornecidos (intervalo [inicio, fim+1d))
        if data_inicio:
            try:
                d1 = datetime.fromisoformat(f"{data_inicio}T00:00:00")
            except Exception:
                raise HTTPException(status_code=400, detail="data_inicio inválida. Use YYYY-MM-DD")
            stmt = stmt.where(Venda.created_at >= d1)
        if data_fim:
            try:
                d2 = datetime.fromisoformat(f"{data_fim}T00:00:00")
            except Exception:
                raise HTTPException(status_code=400, detail="data_fim inválida. Use YYYY-MM-DD")
            # fim exclusivo = dia seguinte 00:00
            from datetime import timedelta
            d2_exclusive = d2 + timedelta(days=1)
            stmt = stmt.where(Venda.created_at < d2_exclusive)
            
        # Aplicar filtro de status se fornecido
        if status_filter:
            if status_filter == "Não Fechadas":
                stmt = stmt.where(Venda.cancelada == False)
            elif status_filter == "Fechadas":
                stmt = stmt.where(Venda.cancelada == True)
        else:
            # Padrão: somente não canceladas (consistente com listar_vendas)
            stmt = stmt.where(Venda.cancelada == False)
        
        # Ordenar por data mais recente
        stmt = stmt.order_by(Venda.created_at.desc())
        
        result = await db.execute(stmt)
        vendas = result.scalars().all()
        
        # Injetar atributo transitório 'usuario_nome' para o schema incluir
        respostas = []
        for v in vendas:
            try:
                setattr(v, 'usuario_nome', getattr(getattr(v, 'usuario', None), 'nome', None))
            except Exception:
                setattr(v, 'usuario_nome', None)
            # Serialização resiliente: ignora registros quebrados
            try:
                respostas.append(VendaResponse.model_validate(v))
            except Exception:
                # opcional: logar
                continue
        
        return respostas
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar vendas do usuário: {str(e)}")

@router.get("/periodo")
async def listar_vendas_periodo(
    data_inicio: str,
    data_fim: str,
    usuario_id: str = None,
    limit: int = None,
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session)
):
    """Listar vendas em um período específico com paginação."""
    try:
        # Validar datas e construir intervalo [início, fim+1d)
        try:
            d1 = datetime.fromisoformat(f"{data_inicio}T00:00:00")
            d2 = datetime.fromisoformat(f"{data_fim}T00:00:00")
        except Exception:
            raise HTTPException(status_code=400, detail="Parâmetros de data inválidos. Use YYYY-MM-DD")

        from datetime import timedelta
        d2_exclusive = d2 + timedelta(days=1)

        # Query base
        stmt = select(Venda).options(selectinload(Venda.itens), selectinload(Venda.cliente), selectinload(Venda.usuario))

        # Filtrar por período
        stmt = stmt.where(Venda.created_at >= d1, Venda.created_at < d2_exclusive)

        # Padrão: excluir vendas canceladas (consistente com listar_vendas)
        stmt = stmt.where(Venda.cancelada == False)

        # Filtrar por usuário se especificado e válido (UUID)
        if usuario_id is not None:
            try:
                usuario_uuid = uuid.UUID(usuario_id)
                stmt = stmt.where(Venda.usuario_id == usuario_uuid)
            except Exception:
                # Ignora filtro se não for UUID válido
                pass
        
        # Ordenar por data mais recente
        stmt = stmt.order_by(Venda.created_at.desc())
        
        # Aplicar paginação se especificada
        if limit:
            stmt = stmt.limit(limit).offset(offset)
        
        result = await db.execute(stmt)
        vendas = result.scalars().all()
        
        # Injetar atributo transitório 'usuario_nome' para o schema incluir
        respostas = []
        for v in vendas:
            try:
                setattr(v, 'usuario_nome', getattr(getattr(v, 'usuario', None), 'nome', None))
            except Exception:
                setattr(v, 'usuario_nome', None)
            try:
                respostas.append(VendaResponse.model_validate(v))
            except Exception:
                # Ignora registros com dados inconsistentes
                continue
        
        return respostas
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar vendas do período: {str(e)}")

@router.put("/{venda_id}/cancelar", response_model=VendaResponse)
async def cancelar_venda(venda_id: str, db: AsyncSession = Depends(get_db_session)):
    """Anula (cancela) uma venda (cancelada=True)."""
    try:
        # Atualizar flag cancelada
        await db.execute(
            update(Venda)
            .where(Venda.id == venda_id)
            .values({Venda.cancelada: True, Venda.updated_at: datetime.utcnow()})
        )
        await db.commit()

        # Retornar venda atualizada
        result = await db.execute(
            select(Venda)
            .options(selectinload(Venda.itens), selectinload(Venda.cliente), selectinload(Venda.usuario))
            .where(Venda.id == venda_id)
        )
        venda_atualizada = result.scalar_one_or_none()
        if not venda_atualizada:
            raise HTTPException(status_code=404, detail="Venda não encontrada")

        try:
            setattr(venda_atualizada, 'usuario_nome', getattr(getattr(venda_atualizada, 'usuario', None), 'nome', None))
        except Exception:
            setattr(venda_atualizada, 'usuario_nome', None)

        # Broadcast realtime: venda cancelada
        try:
            await realtime_manager.broadcast("venda.cancelled", {
                "ts": datetime.utcnow().isoformat(),
                "data": {"id": str(venda_atualizada.id), "cancelada": True}
            })
        except Exception:
            pass

        return venda_atualizada
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao cancelar venda: {str(e)}")
