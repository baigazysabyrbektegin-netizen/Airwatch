# AirWatch: one image with the API, the processed data and the built frontend (deployed on Render).
# Data are not in git: they come from a GitHub Release archive made by scripts/pack_data.py.
#   docker build --build-arg DATA_URL=<release asset url> --build-arg DATA_SHA256=<sha256> -t airwatch .
#   docker run --rm -m 512m -p 8000:8000 airwatch

FROM node:22-alpine AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM alpine:3.21 AS data
ARG DATA_URL
ARG DATA_SHA256
RUN apk add --no-cache curl
RUN test -n "$DATA_URL" && test -n "$DATA_SHA256" || { echo "DATA_URL and DATA_SHA256 build args are required"; exit 1; }
RUN curl -fsSL "$DATA_URL" -o /tmp/data.tar.gz \
 && echo "$DATA_SHA256  /tmp/data.tar.gz" | sha256sum -c - \
 && mkdir /data && tar -xzf /tmp/data.tar.gz -C /data \
 && test -d /data/processed && test -d /data/manifest

FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=data /data data/
COPY --from=web /src/frontend/dist frontend/dist
RUN useradd --uid 1000 --no-create-home airwatch
USER airwatch
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT} --workers 1"]
