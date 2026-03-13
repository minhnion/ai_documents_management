# ---------------------------------------
# Stage 1: Build the frontend
# ---------------------------------------
FROM node:20-slim AS frontend-builder

# Setup pnpm
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
RUN corepack enable pnpm

WORKDIR /app/web

# Copy package files and install dependencies using a cache mount
COPY web/package.json web/pnpm-lock.yaml* ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile

# Copy the rest of the frontend source code and build
# (Note: The original file missed copying the source code before building)
COPY web/ ./
RUN pnpm build


# ---------------------------------------
# Stage 2: Final runtime environment
# ---------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy requirements and install Python dependencies using a cache mount
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Copy built frontend assets from the builder stage
COPY --from=frontend-builder /app/web /app/web

# Copy backend application code
COPY app/ ./app/
COPY .env.example .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
