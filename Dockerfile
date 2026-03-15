ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ARG APP_VERSION=dev

LABEL org.opencontainers.image.title="bw-totp-sidecar"
LABEL org.opencontainers.image.version="${APP_VERSION}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl unzip ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN curl -L "https://vault.bitwarden.com/download/?app=cli&platform=linux" -o /tmp/bw.zip \
 && unzip /tmp/bw.zip -d /usr/local/bin \
 && chmod +x /usr/local/bin/bw \
 && rm -f /tmp/bw.zip

WORKDIR /app
COPY VERSION /app/VERSION
COPY app.py /app/app.py

ENV PORT=8080
EXPOSE 8080

CMD ["python3", "/app/app.py"]
