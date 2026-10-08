FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY requirements.lock ./
COPY autoqc ./autoqc
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
EXPOSE 8810
CMD ["python", "-m", "autoqc.serve"]
