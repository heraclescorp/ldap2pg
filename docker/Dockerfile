FROM debian:bullseye-slim

ARG LDAP2PG_VERSION

RUN apt-get update -y && apt-get install -y --no-install-recommends \
    libsasl2-modules \
    python3 \
    python3-ldap \
    python3-pip \
    python3-psycopg2 \
    python3-setuptools \
    python3-yaml;

# Set LANG for execution order of entrypoint.d run parts.
ENV LANG en_US.utf8
WORKDIR /workspace

ADD ldap2pg /workspace/ldap2pg

ADD setup.py setup.cfg /workspace/

RUN set -ex; \
    pip3 --no-cache-dir install .; \
    ldap2pg --version; \
    :

COPY ./docker/docker-entrypoint.sh /usr/local/bin
RUN mkdir /docker-entrypoint.d
ENTRYPOINT ["docker-entrypoint.sh"]
