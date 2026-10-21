# ---------------------------------------------------------------- dashboard
FROM node:22-slim AS web
WORKDIR /web
COPY web-src/package.json web-src/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web-src/ ./
RUN npx tsc --noEmit && npx vite build --outDir /web/dist --emptyOutDir

# ---------------------------------------------------------------- libsecp256k1
# The exact version and modules Electrum-Mars pins (contrib/make_libsecp256k1.sh).
FROM debian:bookworm-slim AS secp
ARG LIBSECP_VERSION=1253a27756540d2ca526b2061d98d54868e9177c
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates build-essential autoconf automake libtool \
 && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/bitcoin-core/secp256k1.git /secp \
 && cd /secp && git checkout "${LIBSECP_VERSION}^{commit}" \
 && ./autogen.sh \
 && ./configure --enable-module-recovery --enable-experimental --enable-module-ecdh \
      --disable-benchmark --disable-tests --disable-exhaustive-tests --disable-static --enable-shared \
 && make -j4 \
 && cp -L .libs/libsecp256k1.so.0 /libsecp256k1.so.0

# ---------------------------------------------------------------- runtime
FROM python:3.11-slim-bookworm
ARG ELECTRUM_MARS_REF=v4.4.2
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ATOMICSWAPD_DATA=/data

COPY --from=secp /libsecp256k1.so.0 /usr/local/lib/libsecp256k1.so.0
RUN ldconfig

# Electrum-Mars from its release tag, plus native scrypt (fast header checks)
# and cryptography (DNSSEC, wallet file encryption).
RUN pip install "https://github.com/marscoin/electrum-mars/archive/refs/tags/${ELECTRUM_MARS_REF}.tar.gz" \
      scrypt cryptography \
 && python -c "import electrum_mars.ecc_fast, scrypt; print('electrum-mars ok')"

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY atomicswapd ./atomicswapd
COPY --from=web /web/dist ./atomicswapd/web
RUN pip install . && atomicswapd --version

RUN useradd --create-home --uid 1000 maker && mkdir -p /data && chown maker:maker /data
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["atomicswapd"]
