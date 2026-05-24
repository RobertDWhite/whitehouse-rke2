# syntax=docker/dockerfile:1
FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py ./

EXPOSE 8080

# MCP_TOKEN and SDR_API_BASE are injected via the Deployment env.
CMD ["python", "server.py"]
