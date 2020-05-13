FROM python:3.8@sha256:ad7fb5bb4770e08bf10a895ef64a300b288696a1557a6d02c8b6fba98984b86a

RUN apt-get update && apt-get install -y docker.io

COPY server/requirements.txt .
RUN pip3 install -r requirements.txt

WORKDIR /workdir
COPY . ./
RUN pip3 install .

WORKDIR /server
COPY server/ ./
CMD python3 -u ./server.py
