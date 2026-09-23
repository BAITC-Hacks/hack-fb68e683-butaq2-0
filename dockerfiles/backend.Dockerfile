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
COPY telephony/telephony/ ./telephony/telephony/
COPY faceid/ ./faceid/

RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake && \
    pip install --no-cache-dir '.[telephony]' && \
    pip uninstall -y opencv-python && pip install --no-cache-dir --force-reinstall 'opencv-python-headless>=4.10,<5' && \
    python -c 'from insightface.app import FaceAnalysis; import app.main' && \
    apt-get purge -y build-essential cmake && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* && \
    mkdir -p /home/app && chown 10001:10001 /home/app

ENV HOME=/home/app
USER 10001:10001

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
