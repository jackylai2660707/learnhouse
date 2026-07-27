FROM node:22-alpine

# tsx lets TypeScript exercises run without a separate compile step.
# Installed globally so it resolves with --no-install inside the offline sandbox.
RUN npm install -g tsx@4.19.2 typescript@5.7.2 && npm cache clean --force

RUN adduser -D -u 1000 runner || true
USER 1000:1000
WORKDIR /work
