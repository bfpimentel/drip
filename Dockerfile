FROM docker.io/library/python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY app.py ./
COPY templates/ ./templates/
COPY static/ ./static/

RUN pip install .

RUN mkdir -p /app/uploads

EXPOSE 7123

CMD ["python", "app.py"]
