# The React frontend, built once and served as static files.
#
# Two stages: Node builds the bundle, nginx serves it. The Node toolchain
# never reaches the runtime image -- a production container that ships npm and
# a full node_modules tree is ~400 MB of attack surface for zero benefit, since
# nothing is compiled after the build.
#
# Vite inlines `import.meta.env.VITE_*` at build time, so these are build args
# rather than runtime environment. Changing one means rebuilding the image;
# that is the trade for a fully static, cacheable bundle.

FROM node:22-alpine AS builder

WORKDIR /app

# Dependencies first, so a source-only change reuses the install layer.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./

# Defaults to the real API. The sample corpus is a development affordance and
# must not be what a deployed container serves -- it would look like a working
# product while showing fabricated drawings.
ARG VITE_USE_MOCKS=false
ARG VITE_API_BASE=/api/v1
ENV VITE_USE_MOCKS=$VITE_USE_MOCKS
ENV VITE_API_BASE=$VITE_API_BASE

RUN npm run build


# nginx-unprivileged rather than the stock image: it runs as uid 101 and
# listens on 8080, matching the non-root posture of api.Dockerfile and
# streamlit.Dockerfile. The stock nginx image starts its master process as
# root purely to bind port 80, which nothing here needs.
FROM nginxinc/nginx-unprivileged:1.27-alpine

COPY --chown=nginx:nginx docker/nginx/security-headers.conf /etc/nginx/security-headers.conf
COPY --chown=nginx:nginx docker/nginx/frontend.conf /etc/nginx/conf.d/default.conf
COPY --from=builder --chown=nginx:nginx /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q --spider http://localhost:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
