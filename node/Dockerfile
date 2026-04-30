FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .

RUN if [ -f requirements.txt ]; then \
    pip install --no-cache-dir -r requirements.txt; \
fi

COPY . .

RUN useradd -m appuser
USER appuser

CMD ["python", "task.py"]