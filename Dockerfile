FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY livestock_app ./livestock_app
COPY migrations ./migrations
COPY wsgi.py .

RUN useradd --system --uid 10001 --create-home appuser \
    && chown -R appuser:appuser /app
USER 10001:10001

EXPOSE 5000

CMD ["flask", "--app", "wsgi:app", "run", "--host", "0.0.0.0", "--port", "5000"]
