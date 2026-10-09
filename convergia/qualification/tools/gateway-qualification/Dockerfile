FROM convergia-jasmin-candidate:local
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /suite
COPY tools/smpp-lab/package.json tools/smpp-lab/package-lock.json ./tools/smpp-lab/
RUN cd tools/smpp-lab && npm ci --omit=dev
COPY tools/gateway-qualification ./qualification
COPY scripts/setup-cmi-bind-only.py ./scripts/setup-cmi-bind-only.py
ENV NODE_PATH=/suite/tools/smpp-lab/node_modules
RUN python qualification/prepare.py
