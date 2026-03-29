FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn httpx pydantic python-dotenv rich websockets

COPY app/ .

CMD ["python", "main.py"]
