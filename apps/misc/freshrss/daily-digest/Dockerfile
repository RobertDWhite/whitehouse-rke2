FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY digest.py ./digest.py

ENTRYPOINT ["python", "/app/digest.py"]
