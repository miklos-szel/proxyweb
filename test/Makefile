.PHONY: up down test test-keep logs shell-proxysql shell-mysql clean

# Bring the stack up in the background (waits for all healthchecks to pass)
up:
	docker compose up -d --build --wait

# Stop and remove all containers, networks, and volumes
down:
	docker compose down --volumes --remove-orphans

# Build the stack, run tests, then tear everything down
test:
	bash run_tests.sh

# Same as test but leave the stack running afterwards
test-keep:
	bash run_tests.sh --keep

# Tail logs from all services
logs:
	docker compose logs -f

# Open a mysql client on the ProxySQL admin interface
shell-proxysql:
	docker compose exec proxysql mysql -h 127.0.0.1 -P 6032 -u radmin -pradmin

# Open a mysql client on the backend MySQL server
shell-mysql:
	docker compose exec mysql mysql -u root -prootpass testdb

# Remove containers, volumes, and any locally built proxyweb image
clean:
	docker compose down --volumes --rmi local --remove-orphans
rebuild-proxyweb:
	docker compose up -d --build --force-recreate --no-deps proxyweb
