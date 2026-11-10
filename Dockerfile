FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY tagtrail ./tagtrail
RUN pip install --no-cache-dir . && useradd --create-home appuser && mkdir -p /app/data && chown -R appuser /app/data
USER appuser
EXPOSE 8000
CMD ["uvicorn", "tagtrail.app:app", "--host", "0.0.0.0", "--port", "8000"]
