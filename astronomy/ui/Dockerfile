FROM node:24-slim AS builder
WORKDIR /app
COPY package.json .
RUN npm install && npm install --no-save @rollup/rollup-linux-x64-gnu
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.31-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
