FROM alpine:3.21

# gcc/g++ for C and C++ exercises. Compilation happens inside the sandbox.
RUN apk add --no-cache gcc g++ musl-dev libc-dev

RUN adduser -D -u 1000 runner
USER runner
WORKDIR /work
