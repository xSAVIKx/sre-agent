# Built on the shared dependency image (docker/base.Dockerfile); this layer only adds
# source code. BASE_IMAGE is passed by cloudbuild.yaml (the GHCR image for the current
# uv.lock, or one built in the pipeline) and by docker-compose (`service:base`).
ARG BASE_IMAGE=base
FROM ${BASE_IMAGE}

COPY app/ ./app/

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
