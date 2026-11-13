# Build context must be a local clone of https://github.com/thkruz/keeptrack.space
# with the ootk submodule initialized. See BUILD.md for instructions.
# .dockerignore excludes .git so get-submodules.ts skips its git submodule update.
# The plugins-pro submodule (SSH/private) is not required for the OSS build.
#
# Targets:
#   (default)  full source build: npm build + patched nginx runtime.
#   repack     re-base an already-built release onto the current patched nginx
#              runtime without rebuilding the app (security-only refresh; keeps
#              the shipped web assets byte-identical). Context can be this dir:
#                docker buildx build --target repack \
#                  --build-arg APP_IMAGE=registry.internal.white.fm/keeptrack:<ver> .
ARG APP_IMAGE=registry.internal.white.fm/keeptrack:12.1.3

FROM node:26-alpine AS builder
WORKDIR /app
RUN apk add --no-cache python3 make g++
COPY . .
RUN npm ci --ignore-scripts
RUN npm run build

FROM nginx:1.31-alpine@sha256:fed7e14be83cbd2661a75cdc5b872d936cf155d46c327084d30b8df19ae88d72 AS runtime
# Pull current Alpine security fixes (openssl, brotli, ...) on top of the base.
RUN apk upgrade --no-cache
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

FROM ${APP_IMAGE} AS prebuilt

FROM runtime AS repack
COPY --from=prebuilt /usr/share/nginx/html /usr/share/nginx/html

FROM runtime
COPY --from=builder /app/dist /usr/share/nginx/html
