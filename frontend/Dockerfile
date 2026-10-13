# ---------------------------------------------------------------------------
# AirDesk web UI — multi-stage build.
#   stage 1: build the Vite bundle
#   stage 2: serve it from nginx and reverse-proxy /api to the api service
# ---------------------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

# Install dependencies first so this layer is cached across source edits.
# NOTE: `npm ci` requires the lockfile; `npm install` is the fallback.
COPY package*.json ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY . .

# Vite inlines VITE_* at build time. Leave empty to use same-origin requests,
# which is what the nginx container proxies below.
ARG VITE_API_URL=
ENV VITE_API_URL=${VITE_API_URL}
ENV NODE_ENV=production

RUN npm run build


FROM nginx:1.27-alpine AS runtime

# SPA-aware config: unknown paths fall through to index.html, and /api is
# proxied to the FastAPI container by service name.
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
