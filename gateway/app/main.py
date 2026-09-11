from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.ai_routes import router as ai_router
from app.api.chaos_routes import router as chaos_router
from app.api.routes import router
from app.api.stats_routes import router as stats_router
from app.db import close_pool, init_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(title="AI API Reliability Gateway", lifespan=lifespan)

# Dev-only CORS: the Next.js dashboard runs on a different origin
# (localhost:3000) and only reads from this API, so allowing all origins is
# acceptable here rather than something to lock down for a portfolio project.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(chaos_router)
app.include_router(stats_router)
app.include_router(ai_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
