FROM mcr.microsoft.com/playwright@sha256:cf0daee9b994042e011bc29f20cdff1a9f682a039b43fcd738f7d8a9d3bcd9d6
USER root
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /ms-playwright /opt/openfilm-browsers
WORKDIR /app
COPY containers/openfilm/package.json containers/openfilm/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
