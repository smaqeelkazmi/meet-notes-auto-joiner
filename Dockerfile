FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

USER root
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TZ=Asia/Karachi \
    MEET_NOTES_PROFILE_DIR=/profile \
    MEET_NOTES_STATE_DB=/data/state.db \
    MEET_NOTES_LOG_DIR=/data/logs \
    MEET_NOTES_CREDENTIALS_FILE=/secrets/credentials.json \
    MEET_NOTES_TOKEN_FILE=/data/token.json \
    MEET_NOTES_NO_SANDBOX=true

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && mkdir -p /data/logs /profile /secrets /config \
    && chown -R pwuser:pwuser /data /profile /config

USER pwuser
ENTRYPOINT ["meet-notes"]
CMD ["run"]
