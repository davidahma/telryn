ARG RUNTIME_IMAGE=cgr.dev/chainguard/python:latest@sha256:f6e5c2e056393d48fa8afa1ea58a1394db4cd5de1a8f0f48105c8e88fcd4f9d3

# Collect universal Python wheels without executing binaries for the target platform.
FROM --platform=$BUILDPLATFORM python:3.15.2-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS dependencies
COPY requirements-bedrock.txt requirements-container.txt /build/
# Only universal Python wheels may cross from the build host to the target image.
RUN python -m pip install --disable-pip-version-check --no-cache-dir --no-compile \
    --platform any --only-binary=:all: --require-hashes \
    --target /opt/wheels -r /build/requirements-container.txt \
    && install -d -m 0700 -o 65532 -g 65532 /volumes/data /volumes/trust

# Ask the pinned runtime for its pure-Python install path on the build host.
# Only universal modules cross architectures. Isolated Python still uses this directory.
FROM --platform=$BUILDPLATFORM ${RUNTIME_IMAGE} AS layout
COPY --from=dependencies /opt/wheels/ /tmp/wheels/
RUN ["/usr/bin/python", "-I", "-c", "import shutil, sysconfig; shutil.copytree('/tmp/wheels', '/tmp/runtime-root' + sysconfig.get_path('purelib'))"]

FROM ${RUNTIME_IMAGE}
ARG REVISION=local
LABEL org.opencontainers.image.title="Telryn" \
      org.opencontainers.image.source="https://github.com/davidahma/telryn" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.revision="${REVISION}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 \
    TELRYN_TRUST_DIR=/trust/team HOME=/data
COPY --from=layout --chown=0:0 /tmp/runtime-root/ /
COPY --from=dependencies --chown=65532:65532 /volumes/ /
WORKDIR /opt/telryn
COPY LICENSE ./
COPY telryn/ telryn/
COPY examples/ examples/
COPY deadlines/ deadlines/
COPY tests/ tests/
USER 65532:65532
EXPOSE 8765
ENTRYPOINT ["/usr/bin/python", "-m", "telryn"]
CMD ["desk", "--out", "/data/runs", "--container-port", "8765", "--no-browser"]
