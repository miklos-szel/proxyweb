# ProxyWeb
Open Source Web UI for [ProxySQL](https://proxysql.com/)

## Table of Contents

- [Introduction](#introduction)
- [Features](#features)
- [Setup](#setup)
  - [Install ProxyWeb next to ProxySQL](#install-proxyweb-next-to-proxysql)
  - [Install it as a systemd service (Ubuntu)](#install-it-as-a-systemd-service-ubuntu)
  - [Install ProxyWeb to work with remote ProxySQL servers](#install-proxyweb-to-work-with-remote-proxysql-servers)


![ProxyWeb ui](misc/images/ProxyWeb_main.jpg)

## Introduction
ProxyWeb is a modern, lightweight web-based interface for ProxySQL, the popular high-performance MySQL proxy. Designed for simplicity and full control, ProxyWeb allows administrators to manage ProxySQL servers, users, query rules, and variables—all through an intuitive web UI.

## Features:
- Clean and responsive design
- [Multi-server support](misc/images/ProxyWeb_servers.jpg)
- [Configurable reporting](misc/images/ProxyWeb_report.jpg)
- Global and per-server options
- Hide unused tables (global or per-server basis)
- Sort content by any column (asc/desc)
- Online config editor
- Narrow-down content search
- Paginate content
- Command history and SQL dropdown menu 
- Adhoc MySQL queries
- Basic authentication


# Setup

## Prerequisites

- Docker installed on your system
- Git installed
- Basic understanding of ProxySQL and MySQL

## Install ProxyWeb next to ProxySQL
With Docker:
```
docker run -h proxyweb --name proxyweb --network="host" -d proxyweb/proxyweb:latest
```

## Building from source

```bash
make proxyweb-build                        # linux/amd64, tag: latest
make proxyweb-build PLATFORM=linux/arm64   # cross-compile for ARM
make proxyweb-build TAG=1.2.3              # custom tag
make proxyweb-build PLATFORM=linux/arm64 TAG=1.2.3
```

| Variable   | Default        | Description                        |
|------------|----------------|------------------------------------|
| `PLATFORM` | `linux/amd64`  | Target architecture passed to `docker build --platform` |
| `TAG`      | `latest`       | Docker image tag (`proxyweb/proxyweb:<TAG>`) |
## Install it as a systemd service (Ubuntu)
```
git clone https://github.com/miklos-szel/proxyweb
cd proxyweb
make install
```
Visit  [http://ip_of_the_host:5000/setting/edit](http://ip_of_the_host:5000/setting/edit) first and adjust the credentials if needed.
The default connection is the local one with the default credentials.


## Install ProxyWeb to work with remote ProxySQL servers
### Configure ProxySQL for remote admin access

ProxySQL only allows local admin connections by default.

In order to enable remote connections you have to enable it in ProxySQL:

```
set admin-admin_credentials="admin:admin;radmin:radmin";
load admin variables to runtime; save admin variables to disk;
```

After this we can connect to the ProxySQL with:
- username: radmin
- password: radmin
- port: 6032 (default)

Run:
```
docker run -h proxyweb --name proxyweb -p 5000:5000 -d proxyweb/proxyweb:latest
```

Visit [http://ip_of_the_host:5000/setting/edit](http://ip_of_the_host:5000/setting/edit) first and edit the `servers`
section.

> [!NOTE]  
> Basic authentication is turned on by default in the latest version, default credentials are as follows:
>
> - username: admin 
>
> - password: admin42
> 
> These can be changed by editing the config file.


---

### Features on the roadmap
- ability to edit tables
- better input validation

---
### Credits:

- Thanks for René Cannaò and the SysOwn team  [ProxySQL](https://proxysql.com/).
- Tripolszky 'Tripy' Zsolt


