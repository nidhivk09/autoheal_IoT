FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The webapp imports controller/fault_injector.py from the repo root (see
# config/settings.py's sys.path insertion), so bring that directory along.
COPY controller /app/controller
COPY webapp /app/webapp

WORKDIR /app/webapp
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

# All configuration comes from environment variables - injected by whatever
# platform runs this image (Container Apps / AKS), never baked into it.
# migrate + collectstatic run at container start, not build time, since
# settings.py requires POSTGRES_URL/DJANGO_SECRET_KEY to even import.
ENTRYPOINT ["./docker-entrypoint.sh"]
