# python:3.12-slim (Debian base). Standard, widely-used base image -- used
# only as a runtime environment, not incorporated into this project's own
# source, so it does not affect chrono_engine/bot's MIT licensing.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY chrono_engine/ chrono_engine/
COPY bot/ bot/

# .env is mounted or passed via --env-file at run time -- never baked into
# the image (see .dockerignore and README).
CMD ["python", "-m", "bot.bot"]
