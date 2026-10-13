FROM python:3.12-slim
WORKDIR /app
COPY Backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY Backend ./Backend
COPY Frontend ./Frontend
ENV DATABASE_PATH=/data/foodlens.db
EXPOSE 8000
CMD ["sh", "-c", "uvicorn Backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
