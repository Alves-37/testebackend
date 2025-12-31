from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy import select, func, text
import uuid
from app.routers import health, produtos, usuarios, clientes, vendas, auth, categorias, ws
from app.routers import metricas, relatorios, empresa_config, admin, dividas
from app.routers import abastecimentos
from app.db.session import engine, AsyncSessionLocal
from app.db.base import DeclarativeBase
from app.db.models import User, Produto
from app.core.security import get_password_hash

SERVICO_IMPRESSAO_UUID = "157c293f-5995-4a83-9d2a-e02f811dd5f4"
SERVICO_IMPRESSAO_CODIGO = "SERVICO_IMPRESSAO"

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Verificar e criar tabelas se necessário
    print("Iniciando backend...")
    try:
        async with engine.begin() as conn:
            print("Verificando estrutura do PostgreSQL...")
            await conn.run_sync(DeclarativeBase.metadata.create_all)
            print("Estrutura do banco verificada!")

            # Migração leve: permitir codigo NULL em produtos (cliente pode enviar codigo vazio)
            try:
                await conn.execute(text("""
                    UPDATE produtos
                    SET codigo = NULL
                    WHERE codigo IS NOT NULL AND BTRIM(codigo) = ''
                """))
                await conn.execute(text("ALTER TABLE produtos ALTER COLUMN codigo DROP NOT NULL"))
            except Exception as mig_prod_e:
                print(f"Aviso: migração leve de 'produtos.codigo' falhou: {mig_prod_e}")

            # Migração leve: armazenar custo por item de venda (evita lucro = faturamento quando custo do produto for 0)
            try:
                await conn.execute(text(
                    "ALTER TABLE itens_venda ADD COLUMN IF NOT EXISTS preco_custo_unitario DOUBLE PRECISION DEFAULT 0"
                ))
            except Exception as mig_itens_e:
                print(f"Aviso: migração leve de 'itens_venda.preco_custo_unitario' falhou: {mig_itens_e}")

            # Migração leve para a tabela 'abastecimentos'
            try:
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS abastecimentos (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        updated_at TIMESTAMPTZ DEFAULT NOW()
                    );
                """))
                await conn.execute(text("ALTER TABLE abastecimentos ADD COLUMN IF NOT EXISTS produto_id UUID"))
                await conn.execute(text("ALTER TABLE abastecimentos ADD COLUMN IF NOT EXISTS usuario_id UUID"))
                await conn.execute(text("ALTER TABLE abastecimentos ADD COLUMN IF NOT EXISTS quantidade DOUBLE PRECISION"))
                await conn.execute(text("ALTER TABLE abastecimentos ADD COLUMN IF NOT EXISTS custo_unitario DOUBLE PRECISION DEFAULT 0"))
                await conn.execute(text("ALTER TABLE abastecimentos ADD COLUMN IF NOT EXISTS total_custo DOUBLE PRECISION DEFAULT 0"))
                await conn.execute(text("ALTER TABLE abastecimentos ADD COLUMN IF NOT EXISTS observacao TEXT"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_abast_produto ON abastecimentos(produto_id)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_abast_usuario ON abastecimentos(usuario_id)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_abast_created ON abastecimentos(created_at)"))
                await conn.execute(text("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.table_constraints 
                            WHERE constraint_name = 'fk_abastecimentos_produto') THEN
                            ALTER TABLE abastecimentos
                            ADD CONSTRAINT fk_abastecimentos_produto FOREIGN KEY (produto_id) REFERENCES produtos(id);
                        END IF;
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.table_constraints 
                            WHERE constraint_name = 'fk_abastecimentos_usuario') THEN
                            ALTER TABLE abastecimentos
                            ADD CONSTRAINT fk_abastecimentos_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios(id);
                        END IF;
                    END $$;
                """))
            except Exception as mig_e:
                print(f"Aviso: migração leve de 'abastecimentos' falhou: {mig_e}")

        # Garantir usuário técnico Neotrix para autoLogin do PDV online
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(func.lower(User.usuario) == func.lower("Neotrix"))
            )
            user = result.scalar_one_or_none()
            if not user:
                user = User(
                    nome="Neotrix Tecnologias",
                    usuario="Neotrix",
                    senha_hash=get_password_hash("842384"),
                    is_admin=True,
                    ativo=True,
                )
                session.add(user)
                await session.commit()

            # Garantir produto interno SERVICO_IMPRESSAO (uuid fixo para sync do PDV3)
            try:
                pid = uuid.UUID(SERVICO_IMPRESSAO_UUID)
                res_prod = await session.execute(select(Produto).where(Produto.id == pid))
                prod = res_prod.scalar_one_or_none()
                if not prod:
                    # Se existir por código com UUID diferente, não criamos (código é unique)
                    res_codigo = await session.execute(select(Produto).where(Produto.codigo == SERVICO_IMPRESSAO_CODIGO))
                    prod_by_code = res_codigo.scalar_one_or_none()
                    if prod_by_code:
                        print(
                            f"[SEED] Aviso: já existe produto codigo={SERVICO_IMPRESSAO_CODIGO} mas id={prod_by_code.id}. "
                            f"Esperado id={SERVICO_IMPRESSAO_UUID}. Seed não aplicado."
                        )
                    else:
                        session.add(
                            Produto(
                                id=pid,
                                codigo=SERVICO_IMPRESSAO_CODIGO,
                                nome="Serviço de Impressão",
                                descricao="Serviço de impressão e cópias",
                                preco_custo=0.0,
                                preco_venda=0.0,
                                estoque=0.0,
                                estoque_minimo=0.0,
                                categoria_id=None,
                                venda_por_peso=False,
                                unidade_medida="serv",
                                taxa_iva=0.0,
                                ativo=True,
                            )
                        )
                        await session.commit()
                        print(f"[SEED] Produto SERVICO_IMPRESSAO criado com id={SERVICO_IMPRESSAO_UUID}")
            except Exception as seed_e:
                print(f"[SEED] Falha ao garantir SERVICO_IMPRESSAO: {seed_e}")
    except Exception as e:
        print(f"Erro ao conectar com o banco: {e}")
        # Continue mesmo com erro de banco para permitir healthcheck
        pass
    
    yield
    
    # Shutdown
    print("Encerrando backend...")
    try:
        await engine.dispose()
    except:
        pass

app = FastAPI(
    title="PDV3 Hybrid Backend",
    description="API for PDV3 online/offline synchronization.",
    version="0.1.0",
    lifespan=lifespan
)

# CORS (Cross-Origin Resource Sharing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for hybrid client access
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Incluir routers
app.include_router(health.router)
app.include_router(categorias.router)
app.include_router(produtos.router)
app.include_router(usuarios.router)
app.include_router(clientes.router)
app.include_router(vendas.router)
app.include_router(metricas.router)
app.include_router(auth.router)
app.include_router(ws.router)
app.include_router(relatorios.router)
app.include_router(empresa_config.router)
app.include_router(admin.router)
app.include_router(dividas.router)
app.include_router(abastecimentos.router)

@app.get("/")
async def read_root():
    return {"message": "PDV3 Backend is running!"}
