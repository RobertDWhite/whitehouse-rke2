FROM node:20-alpine AS builder
WORKDIR /app

RUN apk add --no-cache git python3 make g++

# Clone with submodules so the prebuild script can init src/engine/ootk
RUN git clone --depth 1 --recurse-submodules --shallow-submodules \
    https://github.com/thkruz/keeptrack.space.git .

# Install dependencies (skip lifecycle scripts; we run the build explicitly)
RUN npm ci --ignore-scripts

# prebuild: generate translations + submodule check (already done above)
# build:    rspack via build-manager.ts → output to ./dist
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
