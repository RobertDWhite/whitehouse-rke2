FROM nimlang/nim:2.2.4-alpine AS build

RUN apk add --no-cache git libsass-dev sassc openssl-dev pcre-dev

WORKDIR /src
RUN git clone --depth 1 https://github.com/zedeus/nitter.git .

RUN nimble build -y -d:release -d:nimPreviewHashRef && \
    nimble scss && \
    strip nitter

FROM alpine:3.24
RUN apk add --no-cache libsass openssl pcre
COPY --from=build /src/nitter /usr/local/bin/nitter
COPY --from=build /src/public /public
WORKDIR /
EXPOSE 8080
CMD ["/usr/local/bin/nitter"]
