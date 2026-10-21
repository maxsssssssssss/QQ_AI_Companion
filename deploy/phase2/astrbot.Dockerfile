ARG ASTRBOT_BASE=soulter/astrbot@sha256:0c3b8ef7485dbd15176637133c23dfbdb80675d174545a997648b9c1db7d53c1
FROM ${ASTRBOT_BASE}
USER root
COPY lockfiles /opt/phase2-locks
COPY wheelhouse /opt/phase2-wheels
# The upstream image includes the release-only commitizen tool, whose deprecated<2
# requirement conflicts with the locked runtime deprecated==3.0.0.
RUN python -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version' \
    && python -m pip install --no-cache-dir --no-index --find-links /opt/phase2-wheels --no-deps --require-hashes \
        -r /opt/phase2-locks/build-tools.txt \
    && uv pip install --system --no-cache --no-index --find-links /opt/phase2-wheels --no-deps --require-hashes --no-build-isolation \
        -r /opt/phase2-locks/astrbot-requirements.txt \
    && uv pip uninstall --system commitizen \
    && python -m pip check \
    && chmod -R a+rX,a-w /AstrBot
COPY security /opt/phase2-runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/phase2-runtime
WORKDIR /AstrBot
USER 10001:10001
CMD ["python", "main.py"]
