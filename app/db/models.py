from sqlalchemy import Column, String, Boolean, Integer, Float, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import DeclarativeBase
from datetime import datetime
from typing import Optional
import uuid


class User(DeclarativeBase):
    __tablename__ = "usuarios"

    nome: Mapped[str] = mapped_column(String(100), nullable=False)
    usuario: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    senha_hash: Mapped[str] = mapped_column(String, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    # Campos adicionais para alinhar com o cliente PDV3
    nivel: Mapped[int] = mapped_column(Integer, default=1)
    salario: Mapped[float] = mapped_column(Float, default=0.0)
    pode_abastecer: Mapped[bool] = mapped_column(Boolean, default=False)
    pode_gerenciar_despesas: Mapped[bool] = mapped_column(Boolean, default=False)
    pode_fazer_devolucao: Mapped[bool] = mapped_column(Boolean, default=False)


class Produto(DeclarativeBase):
    __tablename__ = "produtos"

    codigo: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    descricao: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    preco_custo: Mapped[float] = mapped_column(Float, default=0.0)
    preco_venda: Mapped[float] = mapped_column(Float, nullable=False)
    estoque: Mapped[float] = mapped_column(Float, default=0.0)
    estoque_minimo: Mapped[float] = mapped_column(Float, default=0.0)
    categoria_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    venda_por_peso: Mapped[bool] = mapped_column(Boolean, default=False)
    unidade_medida: Mapped[str] = mapped_column(String(10), default='un')
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    # IVA: taxa padrão aplicada ao produto (ex.: 0, 16, etc.) e código de imposto opcional
    taxa_iva: Mapped[float] = mapped_column(Float, default=0.0)
    codigo_imposto: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)


class Cliente(DeclarativeBase):
    __tablename__ = "clientes"

    nome: Mapped[str] = mapped_column(String(100), nullable=False)
    documento: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    telefone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    endereco: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)


class Venda(DeclarativeBase):
    __tablename__ = "vendas"

    usuario_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    cliente_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("clientes.id"), nullable=True)
    total: Mapped[float] = mapped_column(Float, nullable=False)
    desconto: Mapped[float] = mapped_column(Float, default=0.0)
    forma_pagamento: Mapped[str] = mapped_column(String(50), nullable=False)
    observacoes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cancelada: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Relacionamentos
    usuario: Mapped[Optional["User"]] = relationship("User")
    cliente: Mapped[Optional["Cliente"]] = relationship("Cliente", back_populates="vendas")
    itens: Mapped[list["ItemVenda"]] = relationship("ItemVenda", back_populates="venda")


class ItemVenda(DeclarativeBase):
    __tablename__ = "itens_venda"

    venda_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("vendas.id"), nullable=False)
    produto_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("produtos.id"), nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    peso_kg: Mapped[float] = mapped_column(Float, default=0.0)
    preco_unitario: Mapped[float] = mapped_column(Float, nullable=False)
    subtotal: Mapped[float] = mapped_column(Float, nullable=False)
    # Campos de IVA calculados no momento da venda
    taxa_iva: Mapped[float] = mapped_column(Float, default=0.0)
    base_iva: Mapped[float] = mapped_column(Float, default=0.0)
    valor_iva: Mapped[float] = mapped_column(Float, default=0.0)
    
    # Relacionamentos
    venda: Mapped["Venda"] = relationship("Venda", back_populates="itens")
    produto: Mapped["Produto"] = relationship("Produto")


class EmpresaConfig(DeclarativeBase):
    __tablename__ = "empresa_config"

    nome: Mapped[str] = mapped_column(String(200), default="")
    nuit: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    telefone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    endereco: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    logo_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class Divida(DeclarativeBase):
    """Modelo de dívida no backend, alinhado ao esquema local.

    Usa UUID como chave primária, mas mantém os mesmos campos conceituais
    (valor_total, valor_original, desconto_aplicado, percentual_desconto, valor_pago, status, observacao).
    """

    __tablename__ = "dividas"

    # ID local opcional (inteiro) para mapear com o SQLite do PDV3
    id_local: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    cliente_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("clientes.id"), nullable=True)
    usuario_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)
    data_divida: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valor_total: Mapped[float] = mapped_column(Float, nullable=False)
    valor_original: Mapped[float] = mapped_column(Float, default=0.0)
    desconto_aplicado: Mapped[float] = mapped_column(Float, default=0.0)
    percentual_desconto: Mapped[float] = mapped_column(Float, default=0.0)
    valor_pago: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="Pendente")
    observacao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    cliente: Mapped[Optional["Cliente"]] = relationship("Cliente")
    usuario: Mapped[Optional["User"]] = relationship("User")
    itens: Mapped[list["ItemDivida"]] = relationship("ItemDivida", back_populates="divida")
    pagamentos: Mapped[list["PagamentoDivida"]] = relationship("PagamentoDivida", back_populates="divida")


class ItemDivida(DeclarativeBase):
    __tablename__ = "itens_divida"

    divida_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("dividas.id"), nullable=False)
    produto_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("produtos.id"), nullable=False)
    quantidade: Mapped[float] = mapped_column(Float, nullable=False)
    preco_unitario: Mapped[float] = mapped_column(Float, nullable=False)
    subtotal: Mapped[float] = mapped_column(Float, nullable=False)
    peso_kg: Mapped[float] = mapped_column(Float, default=0.0)

    divida: Mapped["Divida"] = relationship("Divida", back_populates="itens")
    produto: Mapped["Produto"] = relationship("Produto")


class PagamentoDivida(DeclarativeBase):
    __tablename__ = "pagamentos_divida"

    divida_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("dividas.id"), nullable=False)
    data_pagamento: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valor: Mapped[float] = mapped_column(Float, nullable=False)
    forma_pagamento: Mapped[str] = mapped_column(String(50), nullable=False)
    usuario_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True)

    divida: Mapped["Divida"] = relationship("Divida", back_populates="pagamentos")
    usuario: Mapped[Optional["User"]] = relationship("User")


# Adicionar relacionamentos reversos
Cliente.vendas = relationship("Venda", back_populates="cliente")


class Abastecimento(DeclarativeBase):
    """Registro de abastecimentos de estoque."""
    __tablename__ = "abastecimentos"

    produto_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("produtos.id"), nullable=False, index=True)
    usuario_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True, index=True)
    quantidade: Mapped[float] = mapped_column(Float, nullable=False)
    custo_unitario: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_custo: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    observacao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    produto: Mapped["Produto"] = relationship("Produto")
    usuario: Mapped[Optional["User"]] = relationship("User")
