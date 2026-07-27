FROM eclipse-temurin:21-jdk-alpine

RUN adduser -D -u 1000 runner || true
USER 1000:1000
WORKDIR /work
