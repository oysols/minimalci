FROM python:3.8@sha256:2b8823de22d5434cd9b7aff963e9299ef52605402bd0d4ef23e45f55333a5d4c

RUN apt-get update && apt-get install -y docker.io

COPY server/requirements.txt .
RUN pip3 install -r requirements.txt

WORKDIR /workdir
COPY . ./
RUN pip3 install .

WORKDIR /server
COPY server/ ./
CMD python3 -u ./server.py
