# Stage 1: build the SPA
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY web/ ./
# vite.config.ts targets ../app/static for local dev; the image build writes to /web/dist instead
RUN npm run build -- --outDir dist --emptyOutDir

# Stage 2: Python runtime
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
# The toolkit's pinned framework adapters are not needed; install it without deps.
RUN pip install --upgrade pip \
 && pip install "paypal-agent-toolkit==1.11.0" --no-deps \
 && pip install .
COPY --from=web /web/dist ./app/static
COPY scripts ./scripts
# Run as an unprivileged user; /srv/data is the only writable path (set TK_DB_PATH=/tmp/... or mount a disk there).
RUN useradd --system --uid 10001 --no-create-home turnkeeper  && mkdir -p /srv/data  && chown turnkeeper /srv/data
USER turnkeeper
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
