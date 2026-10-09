FROM python:3.10.19-slim-bookworm@sha256:23f63358922e79a794f71be8f3723c84e5ccca9638af3f74456dc73d6184499e
ARG SOURCE_COMMIT
LABEL org.opencontainers.image.source="https://github.com/convergia/jasmin"
LABEL org.opencontainers.image.revision=$SOURCE_COMMIT
LABEL org.opencontainers.image.licenses="Apache-2.0"
WORKDIR /src
COPY convergia/requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir --no-deps -r /build/requirements.txt
COPY jasmin ./jasmin
COPY misc ./misc
COPY setup.py MANIFEST.in README.rst LICENSE requirements.txt requirements-test.txt ./
RUN pip wheel --no-deps --no-build-isolation --wheel-dir /build/wheels . \
    && pip install --no-deps /build/wheels/jasmin-*.whl && pip check
RUN mkdir -p /etc/jasmin/store /etc/jasmin/resource /var/log/jasmin \
    && cp misc/config/resource/* /etc/jasmin/resource/
RUN test -n "$SOURCE_COMMIT" && printf '%s\n' "$SOURCE_COMMIT" > /build/source-commit
WORKDIR /
CMD ["jasmind.py", "--help"]
