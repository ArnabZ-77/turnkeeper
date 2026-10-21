# Stage 1: build the SPA
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY web/ ./
RUN npm run build

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
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
