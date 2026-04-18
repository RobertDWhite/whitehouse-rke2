# Build context must be a local clone of https://github.com/thkruz/keeptrack.space
# with the ootk submodule initialized. See BUILD.md for instructions.
# .dockerignore excludes .git so get-submodules.ts skips its git submodule update.
# The plugins-pro submodule (SSH/private) is not required for the OSS build.
FROM node:20-alpine AS builder
WORKDIR /app
RUN apk add --no-cache python3 make g++
COPY . .
RUN npm ci --ignore-scripts
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
