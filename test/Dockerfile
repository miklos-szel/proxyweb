FROM python:3.14.0b1-bookworm
LABEL maintainer="email@miklos-szel.com"

ENV DEBIAN_FRONTEND=noninteractive

COPY ./requirements.txt /app/requirements.txt

WORKDIR /app

RUN pip3 install -r requirements.txt

COPY . /app
RUN cp /app/misc/entry.sh /app/ && chmod 755 /app/entry.sh

# Use the apt-packaged mysql client — avoids fragile CDN downloads in the test env
RUN apt-get update -y && \
    apt-get install -y default-mysql-client && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENTRYPOINT [ "./entry.sh" ]
CMD [ "app.py" ]
