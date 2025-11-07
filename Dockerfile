# LipidFinder WebApp Dockerfile
# - Python 3.11 slim base
# - Installs system deps + R (for XCMS route)
# - Installs Python deps, Flask, and Gunicorn
# - Runs the app under Gunicorn on port 8000

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt

# System packages: build tools, curl for healthcheck, git (optional), R base
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    r-base \
    r-base-dev \
    libcurl4-openssl-dev \
    libssl-dev \
    libxml2-dev \
    libz-dev \
    libpng-dev \
    libjpeg-dev \
  && rm -rf /var/lib/apt/lists/*

# Python dependencies + web server packages
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir flask gunicorn

# Copy source
COPY . /app

## Install Bioconductor packages required by docs/xcms.R
## If build time is a concern, you can remove packages you don't need.
RUN R -e "install.packages('BiocManager', repos='https://cloud.r-project.org')" \
    && R -e "BiocManager::install(c('xcms','MSnbase','BiocParallel'), ask=FALSE)" \
    && R -e "install.packages(c('caTools','readr','ggplot2','pheatmap','RColorBrewer','ggrepel','gridExtra','plotly'), repos='https://cloud.r-project.org')"

EXPOSE 8080

# Use Gunicorn to serve Flask app
CMD ["gunicorn", "-b", "0.0.0.0:8080", "webapp.app:app"]