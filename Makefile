basedir = /usr/local/proxyweb
secret_key=$(shell cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 16 | head -n 1)

proxyweb-build:
	docker build --platform   linux/amd64 -t proxyweb/proxyweb:latest .

proxyweb-build-nocache:
	docker build --no-cache -t proxyweb/proxyweb:latest .

proxyweb-run-local: proxyweb-build
	docker run -h proxyweb --name proxyweb --network="host" -d proxyweb/proxyweb:latest

proxyweb-run: proxyweb-build
	docker run -h proxyweb --name proxyweb -p 5000:5000 -d proxyweb/proxyweb:latest

proxyweb-run-mappedconf:
	docker run --mount type=bind,source="`pwd`/config/config.yml",target="/app/config.yml" -h proxyweb --name proxyweb --network="host" -d proxyweb/proxyweb:latest

proxyweb-run-mapped:
	docker run --mount type=bind,source="`pwd`/",target="/app/" -h proxyweb --name proxyweb -p 5000:5000  -d proxyweb/proxyweb:latest

proxyweb-login: proxyweb-run
	docker exec -it proxyweb bash

proxyweb-pull:
	docker pull proxyweb/proxyweb:latest

proxyweb-push:
	docker push proxyweb/proxyweb:latest

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

