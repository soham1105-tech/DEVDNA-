FROM python:3.11-slim

WORKDIR /srv

COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY app ./app
COPY sample_project ./sample_project

# Experiments run the bundled sample's pytest suite inside this container.
ENV DEVDNA_DATA_DIR=/data
VOLUME /data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
