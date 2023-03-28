FROM python:3.11.2-slim-bullseye

WORKDIR /app

RUN apt update \
    && apt install -y --no-install-recommends procps openjdk-17-jdk \
    && pip install python-keycloak==2.14.0 docker==6.0.1

COPY main.py ./

ENTRYPOINT ["python", "main.py"]
