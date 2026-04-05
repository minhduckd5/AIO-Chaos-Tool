FROM python:3.12-slim

WORKDIR /app

# --- START MODIFICATION ---
# MODIFIED: Optimize for faster rebuilds and smaller image.
# - Remove unnecessary OS packages (pure-python/wheel deps should install cleanly on slim)
# - Use BuildKit pip cache mounts when available
# - Maximize layer caching by copying dependency manifests first

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /app/requirements.txt

# Copy only packaging metadata first (keeps editable install layer cacheable)
COPY setup.py /app/setup.py
COPY README.md /app/README.md

# Copy project
COPY . /app

# If your project is a package, install it editable for dev workflow
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -e ".[dev]"

# Default shell
CMD ["bash"]
# --- END MODIFICATION ---