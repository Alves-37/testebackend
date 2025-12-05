from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from sqlalchemy import select, func
from app.routers import health, produtos, usuarios, clientes, vendas, auth, categorias, ws
from app.routers import metricas, relatorios, empresa_config, admin, dividas
from app.routers import abastecimentos
from app.db.session import engine, AsyncSessionLocal
from app.db.base import DeclarativeBase
from app.db.models import User
from app.core.security import get_password_hash

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Verificar e criar tabelas se necessário
    print("Iniciando backend...")
    try:
        async with engine.begin() as conn:
            print("Verificando estrutura do PostgreSQL...")
            await conn.run_sync(DeclarativeBase.metadata.create_all)
            print("Estrutura do banco verificada!")

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
