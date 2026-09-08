FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# All configuration comes from environment variables (see .env.example at the
# repo root) - injected via the AKS Deployment manifest / Kubernetes Secret,
# never baked into the image.
CMD ["python", "main.py"]
