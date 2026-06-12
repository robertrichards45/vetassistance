FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY . /app

ENV SECRET_KEY=local-dev-change-me \
    DATABASE_URL=sqlite:////app/local_test.db \
    MAX_CONTENT_LENGTH=1073741824 \
    AI_ENABLED=0

EXPOSE 5000
CMD ["python", "app.py"]
