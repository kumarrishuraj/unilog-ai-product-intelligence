# ---- frontend build ---------------------------------------------------
FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- runtime ----------------------------------------------------------
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/    ./backend/
COPY evaluation/ ./evaluation/
COPY scripts/    ./scripts/
COPY data/packs/ ./data/packs/
COPY data/raw/   ./data/raw/
COPY --from=ui /ui/dist ./frontend/dist

RUN mkdir -p data/reference data/processed data/uploads

EXPOSE 8000
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
