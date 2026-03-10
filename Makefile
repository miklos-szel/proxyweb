basedir = /usr/local/proxyweb
secret_key=$(shell cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 16 | head -n 1)

# Build configuration — override on the command line as needed:
#   PLATFORM  target architecture (default: linux/amd64)
#             e.g. make proxyweb-build PLATFORM=linux/arm64
#   TAG       Docker image tag (default: latest)
#             e.g. make proxyweb-build TAG=1.2.3
PLATFORM ?= linux/amd64
TAG ?= latest

proxyweb-build:
	docker build --platform $(PLATFORM) -t proxyweb/proxyweb:$(TAG) .

proxyweb-build-nocache:
	docker build --platform $(PLATFORM) --no-cache -t proxyweb/proxyweb:$(TAG) .

proxyweb-run-local: proxyweb-build
	docker run -h proxyweb --name proxyweb --network="host" -d proxyweb/proxyweb:$(TAG)

proxyweb-run: proxyweb-build
	docker run -h proxyweb --name proxyweb -p 5000:5000 -d proxyweb/proxyweb:$(TAG)

proxyweb-run-mappedconf:
	docker run --mount type=bind,source="`pwd`/config/config.yml",target="/app/config.yml" -h proxyweb --name proxyweb --network="host" -d proxyweb/proxyweb:$(TAG)

proxyweb-run-mapped: proxyweb-build
	docker run --mount type=bind,source="`pwd`/",target="/app/" -h proxyweb --name proxyweb -p 5000:5000  -d proxyweb/proxyweb:$(TAG)

proxyweb-login: proxyweb-run
	docker exec -it proxyweb bash

proxyweb-pull:
	docker pull proxyweb/proxyweb:$(TAG)

proxyweb-push:
	docker push proxyweb/proxyweb:$(TAG)

proxyweb-destroy:
	docker stop proxyweb && docker rm proxyweb

proxyweb-sync:
	./sync_to_container.sh

install:
	useradd -s /bin/false -d $(basedir)  proxyweb
	apt update && apt install python3-pip python3-venv -y
	mkdir -p $(basedir)/
	cp -r . $(basedir)/
	chown -R proxyweb  $(basedir)/config/
	sed -i "s/12345678901234567890/${secret_key}/" ${basedir}/config/config.yml
	python3 -m venv $(basedir)/
	$(basedir)/bin/pip3 install -r $(basedir)/requirements.txt
	cp misc/proxyweb.service /etc/systemd/system/
	systemctl daemon-reload
	systemctl enable proxyweb
	systemctl start proxyweb
	systemctl status proxyweb

uninstall:
	-systemctl stop proxyweb
	-systemctl disable proxyweb
	-userdel proxyweb
	-rm /etc/systemd/system/proxyweb.service
	-rm -rf $(basedir)

proxyweb-start:
	systemctl start proxyweb

proxyweb-stop:
	systemctl stop proxyweb

