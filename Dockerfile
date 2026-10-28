# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LOCAL_DRAFT_ADVISOR_BACKEND=tfidf \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    PORT=10000

WORKDIR /app
RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 ml2 \
    && useradd --uid 10001 --gid ml2 --no-create-home --home-dir /app --shell /usr/sbin/nologin ml2

COPY backend/requirements-serving.txt ./backend/requirements-serving.txt
RUN python -m pip install --no-cache-dir -r backend/requirements-serving.txt

# .dockerignore permits only the serving import closure and the pinned artifact.
COPY backend/ ./backend/
RUN python -c "from backend.main import app; from backend.services.modeling.advisor_pipeline import advise_bans, recommend_picks; from backend.services.modeling.contextual_mvp import load_snapshot; assert load_snapshot()['counts']['games'] == 17; assert recommend_picks(strict_turn=False)['recommendations']; assert advise_bans()['recommendation']['recommendations']; print('Serving imports, artifact, picks and advice verified')"

USER 10001:10001
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '10000') + '/health', timeout=3).read()"]
CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port \"${PORT:-10000}\""]
