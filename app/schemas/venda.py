from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime
import uuid


_MODEL_CONFIG_IGNORE_EXTRA = {
    "extra": "ignore",
}

class ItemVendaBase(BaseModel):
    produto_id: str
    quantidade: int = Field(..., ge=0)
    peso_kg: Optional[float] = Field(0.0, ge=0)
    # Permitir zero para compatibilidade com dados antigos
    preco_unitario: float = Field(..., ge=0)
    subtotal: float = Field(..., ge=0)
    taxa_iva: Optional[float] = Field(0.0, ge=0)
    base_iva: Optional[float] = Field(0.0, ge=0)
    valor_iva: Optional[float] = Field(0.0, ge=0)

    model_config = _MODEL_CONFIG_IGNORE_EXTRA

class ItemVendaCreate(ItemVendaBase):
    pass

class ItemVendaResponse(ItemVendaBase):
    id: str
    venda_id: str
    created_at: datetime
    updated_at: datetime

    @field_validator('id', 'venda_id', 'produto_id', mode='before')
    @classmethod
    def convert_uuid_to_str(cls, v):
        if isinstance(v, uuid.UUID):
            return str(v)
        return v

    @field_validator('preco_unitario', 'subtotal', 'peso_kg', 'quantidade', 'taxa_iva', 'base_iva', 'valor_iva', mode='before')
    @classmethod
    def default_zeros(cls, v):
        # Normaliza None para 0 para evitar erros de validação vindos do banco
        if v is None:
            return 0
        return v

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            uuid.UUID: lambda v: str(v)
        }

class VendaBase(BaseModel):
    usuario_id: Optional[str] = None
    cliente_id: Optional[str] = None
    # Observação: alguns fluxos de cancelamento/devolução podem enviar total=0.
    total: float = Field(..., ge=0)
    desconto: Optional[float] = Field(0.0, ge=0)
    aplicar_iva: Optional[bool] = True
    forma_pagamento: str = Field(..., min_length=1, max_length=50)
    observacoes: Optional[str] = None

    model_config = _MODEL_CONFIG_IGNORE_EXTRA

class VendaCreate(VendaBase):
    uuid: Optional[str] = None
    itens: Optional[List[ItemVendaCreate]] = Field(default_factory=list)
    created_at: Optional[datetime] = None

    model_config = _MODEL_CONFIG_IGNORE_EXTRA

class VendaUpdate(BaseModel):
    usuario_id: Optional[str] = None
    cliente_id: Optional[str] = None
    total: Optional[float] = Field(None, ge=0)
    desconto: Optional[float] = Field(None, ge=0)
    forma_pagamento: Optional[str] = Field(None, min_length=1, max_length=50)
    observacoes: Optional[str] = None
    cancelada: Optional[bool] = None

    model_config = _MODEL_CONFIG_IGNORE_EXTRA

class VendaResponse(VendaBase):
    id: str
    usuario_nome: Optional[str] = None
    cancelada: bool
    created_at: datetime
    updated_at: datetime
    itens: List[ItemVendaResponse] = Field(default_factory=list)

    @field_validator('id', 'usuario_id', 'cliente_id', mode='before')
    @classmethod
    def convert_uuid_to_str(cls, v):
        if isinstance(v, uuid.UUID):
            return str(v)
        return v

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            uuid.UUID: lambda v: str(v)
        }
