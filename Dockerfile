# Slim is enough: shapely's manylinux wheels bundle GEOS, so there is no
# system geo stack to install and nothing to compile.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Where the volume is expected. `results.sqlite` is seeded from the copy baked
# into the image on first boot (see paths.ensure_db), so the app is useful
# immediately; `geo` stays empty until the cantonal downloads are uploaded, and
# until then "Neu berechnen" cannot recompute — it will still run the ÖREB check.
ENV DENSIFICATION_DB=/data/results.sqlite \
    DENSIFICATION_DATA=/data/geo

EXPOSE 8501

# $PORT is injected by Railway. `sh -c` so it is expanded rather than passed
# through literally, which an exec-form CMD would do.
CMD ["sh", "-c", "streamlit run app.py \
     --server.port ${PORT:-8501} \
     --server.address 0.0.0.0 \
     --server.headless true \
     --browser.gatherUsageStats false"]
