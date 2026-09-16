from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.draft import router as draft_router

app = FastAPI(title="MLBB Draft API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite dev
        "http://127.0.0.1:5173",
        "https://djsudartha.github.io",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(draft_router, prefix="/draft")


@app.get("/")
def root():
    return {"message": "MLBB Draft Backend Running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/draft")
def draft_routes():
    return {
        "endpoints": [
            "/draft/recommend-bans",
            "/draft/advise-bans",
            "/draft/recommend-picks",
            "/draft/advise-picks",
        ]
    }
