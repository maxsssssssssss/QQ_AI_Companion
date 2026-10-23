FROM qq-companion/python-base:3.12-34386ef0cb08
COPY lockfiles /locks
COPY wheelhouse /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links /wheels --no-deps --require-hashes -r /locks/build-tools.txt \
    && uv pip install --system --no-cache --no-index --find-links /wheels --no-deps --require-hashes --no-build-isolation -r /locks/astrbot-requirements.txt \
    && python -m pip check
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /harness
USER 1000:1000
