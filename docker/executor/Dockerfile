FROM python:3.12-slim

# The Docker CLI talks to the host daemon over the mounted socket; sandbox
# containers are siblings of this one, not children.
#
# Debian's docker.io package does not ship the `docker` client binary here, so
# pull the official static CLI instead. Only the client is needed — no daemon.
ARG DOCKER_CLI_VERSION=27.5.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && case "$(uname -m)" in \
         x86_64)  DOCKER_ARCH=x86_64 ;; \
         aarch64) DOCKER_ARCH=aarch64 ;; \
         *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DOCKER_ARCH}/docker-${DOCKER_CLI_VERSION}.tgz" \
       -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /tmp docker/docker \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && chmod +x /usr/local/bin/docker \
    && rm -rf /tmp/docker.tgz /tmp/docker \
    && docker --version

RUN pip install --no-cache-dir \
      "fastapi==0.115.6" \
      "uvicorn[standard]==0.34.0" \
      "pydantic==2.10.4"

WORKDIR /app
COPY docker/executor/app.py /app/app.py
COPY docker/executor/images /app/images
COPY apps/api/config/code-language-capabilities.json /app/code-language-capabilities.json

EXPOSE 2358

HEALTHCHECK --interval=30s --timeout=5s --start-period=15m --retries=3 \
    CMD curl -fsS http://localhost:2358/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "2358", "--workers", "1"]
