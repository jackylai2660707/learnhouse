FROM python:3.12-alpine

# Preinstalled libraries teachers can rely on in exercises without network access
# (the sandbox runs with --network=none).
RUN pip install --no-cache-dir --root-user-action=ignore \
      numpy==2.2.1 \
      pandas==2.2.3 \
      pytest==8.3.4

# The sandbox runs as an unprivileged fixed uid; /work is bind-mounted at runtime.
RUN adduser -D -u 1000 runner
USER runner
WORKDIR /work
