# ─── Base image ──────────────────────────────────────────────────────
# WHAT: Python 3.12, slim variant.
# WHY: 3.12 is a current, broadly-supported runtime (all deps in
#      requirements.txt declare support for it). Slim keeps the image
#      small without needing a separate build stage for this project,
#      since none of our dependencies require compiling from source.
FROM python:3.12-slim

# ─── Runtime environment tweaks ───────────────────────────────────────
# WHAT: A few standard Python-in-Docker settings.
# WHY: PYTHONDONTWRITEBYTECODE skips .pyc files (no benefit in a
#      container that's rebuilt from scratch each deploy).
#      PYTHONUNBUFFERED makes print()/logging show up immediately in
#      `docker logs` / Railway's log viewer instead of being buffered.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ─── Install dependencies first (layer caching) ───────────────────────
# WHAT: Copy only requirements.txt first, then install.
# WHY: Docker caches this layer — if requirements.txt hasn't changed,
#      this step is skipped on the next build, which is most of them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── Copy the rest of the application ──────────────────────────────────
# WHAT: Copy all project files into the container.
# WHY: Paired with the .dockerignore file alongside this Dockerfile,
#      so things like .env, leads.db, and __pycache__ never end up
#      baked into the image.
COPY . .

# ─── Run as a non-root user ───────────────────────────────────────────
# WHAT: Create an unprivileged user and switch to it before running.
# WHY: If the app or a dependency is ever compromised, it shouldn't
#      have root inside the container. This costs nothing and is a
#      standard hardening step worth having in a portfolio piece.
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

# ─── Healthcheck ───────────────────────────────────────────────────────
# WHAT: Periodically hit "/" inside the container and fail if it
#       doesn't respond.
# WHY: Lets `docker ps` / most orchestrators (ECS, Swarm, etc.) see
#      that the app is actually serving, not just that the process
#      is running. Railway does its own external health check against
#      your public domain regardless, so this is mainly useful if you
#      ever run this image somewhere other than Railway.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, os; urllib.request.urlopen('http://localhost:' + os.environ.get('PORT', '8000') + '/')" || exit 1

# ─── Expose the port ────────────────────────────────────────────────
EXPOSE 8000

# ─── Run the app ────────────────────────────────────────────────────
# WHAT: Start uvicorn, binding to whatever port the platform gives us.
# WHY: Railway (and most PaaS providers) injects a $PORT environment
#      variable at runtime and routes traffic to *that* port — it does
#      not necessarily match the hardcoded 8000 from EXPOSE. The
#      previous CMD used the JSON "exec form", which does NOT expand
#      environment variables, so it would silently keep listening on
#      8000 even if Railway expected something else. Shell form (no
#      brackets) runs through /bin/sh, which does expand ${PORT}, and
#      we fall back to 8000 for local `docker run` testing where
#      $PORT isn't set.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}