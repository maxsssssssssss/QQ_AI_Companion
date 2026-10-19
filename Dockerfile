ARG PYTHON_BASE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
FROM ${PYTHON_BASE}

# Upstream sources are supplied from an immutable, hash-checked git archive.
# The build context intentionally excludes runtime data, credentials and .git.
COPY lockfiles /opt/locks
RUN --mount=type=secret,id=ca_bundle,required=true \
    PIP_CERT=/run/secrets/ca_bundle \
    python -m pip install --no-cache-dir --require-hashes \
        -r /opt/locks/build-tools.txt
RUN --mount=type=secret,id=ca_bundle,required=true \
    SSL_CERT_FILE=/run/secrets/ca_bundle \
    uv pip install --system --no-cache --require-hashes --no-build-isolation \
        -r /opt/locks/astrbot-requirements.txt \
    && python -m pip check

COPY AstrBot /AstrBot
RUN groupadd --gid 10001 astrbot \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /state astrbot \
    && mkdir /state \
    && chown 10001:10001 /state \
    && chmod -R a+rX,a-w /AstrBot

ENV ASTRBOT_ROOT=/state \
    PYTHONPATH=/AstrBot \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/state
WORKDIR /state
USER 10001:10001
CMD ["python", "/harness/launch_astrbot.py"]
