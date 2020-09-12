FROM python:3.8@sha256:c7853c950ced571d51987ba48ba5153c640ae78794639891806e03e57a321439

RUN apt-get update && apt-get install -y docker.io

COPY server/requirements.txt .
RUN pip3 install -r requirements.txt

WORKDIR /temp
COPY . ./
RUN pip3 install .

WORKDIR /server
COPY server/ ./
CMD python3 -u ./server.py
