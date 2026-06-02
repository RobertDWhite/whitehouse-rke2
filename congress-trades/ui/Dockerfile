FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY . .
RUN npm run build

FROM nginx:1.31-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
CMD ["/bin/sh", "-c", ": ${PUBLIC_SITE:=false}; envsubst < /usr/share/nginx/html/app-config.js.template > /usr/share/nginx/html/app-config.js && /docker-entrypoint.sh nginx -g 'daemon off;'"]
EXPOSE 8080
