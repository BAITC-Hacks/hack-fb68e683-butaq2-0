FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Some ARM Docker VMs expose CPU features that the bundled OpenSSL cannot use
# (SIGILL / exit 132 during cryptography import). Keep TLS enabled, use portable
# crypto implementations. This ARM-specific setting has no effect on x86.
# https://docs.openssl.org/master/man3/OPENSSL_armcap/
ENV OPENSSL_armcap=0

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY app/ ./app/
COPY v2v/ ./v2v/
COPY multi-agent/src/ ./multi-agent/src/

RUN pip install --no-cache-dir . && python -c "import app.main"

USER 10001:10001

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
