# syntax=docker/dockerfile:1
FROM python:3.13-slim

# 3.13 and not "latest": ultralytics pulls torch, and torch had no 3.14 wheels
# when this was written — the build fails outright there rather than degrading.

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Keep Playwright's browsers on a fixed path rather than under ~/.cache, so
    # the layer they land in is predictable and does not depend on $HOME.
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

WORKDIR /app

# Torch first, and explicitly from the CPU index. This is the single biggest
# decision in the file: pip's default torch wheel for Linux bundles the whole
# NVIDIA CUDA stack — well over 2 GB of libraries — for a machine that has no
# GPU and will never use them. The CPU build is a fraction of the size and
# identical for our purposes; detection.py runs YOLO on CPU either way.
# Installed as its own layer so it is cached across rebuilds of the app.
# Pinned to the versions this app was actually verified against locally, which
# the CPU index carries for cp313. Unpinned, a future torch could land here
# without anyone having run the detection path against it.
RUN pip install --no-cache-dir \
        torch==2.13.0 torchvision==0.28.0 \
        --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
# torch/torchvision are already satisfied above, so this resolves the rest
# without reaching for the default (CUDA) wheels.
RUN pip install --no-cache-dir -r requirements.txt

# Chromium plus its system libraries. Needed because the image-search scrapers
# drive a real browser to upload a photo: these sites reject JS-synthesised
# file events, so there is no cheaper path. --with-deps pulls the shared
# libraries a headless Chromium needs, which the slim base does not carry.
RUN python -m playwright install --with-deps chromium

COPY . .

# Fly sets PORT; the default keeps `docker run -p 8080:8080` working unchanged.
ENV PORT=8080
EXPOSE 8080

# 0.0.0.0, not 127.0.0.1 — inside a container the loopback interface is only
# reachable from the container itself, so binding there makes the app look dead
# to the platform's health checks.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
