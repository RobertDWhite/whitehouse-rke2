FROM python:3.14-slim

# poppler-utils -> pdftotext (text) + pdftoppm (rasterize for OCR); tesseract-ocr -> OCR engine
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY ingest ./ingest

RUN useradd -u 1000 -m appuser && chown -R appuser /srv
USER 1000
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
