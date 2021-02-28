FROM python:3.8-slim-buster

WORKDIR /app

COPY requirements.txt requirements.txt

RUN apt-get update -y
RUN apt install libgl1-mesa-glx -y
RUN apt-get install git -y
RUN pip3 install -r requirements.txt

COPY . .