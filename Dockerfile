FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ARG APP_VERSION="unknown"
ARG VCS_REVISION="unknown"

LABEL org.opencontainers.image.source="https://github.com/NVX-11/release-reliability-lab" \
      org.opencontainers.image.revision="${VCS_REVISION}" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /service

RUN groupadd --system app && useradd --system --gid app --home-dir /nonexistent app

COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

COPY --chown=app:app app ./app

USER app
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
