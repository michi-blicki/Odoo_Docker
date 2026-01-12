FROM ubuntu:noble

SHELL ["/bin/bash", "-euxo", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Zurich

ENV LANG=C.UTF-8
ENV ODOO_VERSION=18.0
ARG ODOO_RELEASE=18.0
ARG BUILD_TYPE=DEV

# ---------------------------------------------------------
# Software Packages
# ---------------------------------------------------------
RUN apt-get update \
 && apt-get upgrade -f --fix-missing -y ca-certificates \
 && apt-get install -f --fix-missing -y software-properties-common \
        adduser \
        apt-utils \
        bash \
        build-essential \
        curl \
        fontconfig \
        fonts-dejavu-core \
        fonts-freefont-ttf \
        fonts-freefont-otf \
        fonts-font-awesome \
        fonts-roboto-unhinted \
        git \
        gzip \
        libffi-dev \
        libjpeg-dev \
        libjpeg8-dev \
        libldap2-dev \
        libpq-dev \
        libsasl2-dev \
        libssl-dev \
        libxml2-dev \
        libxslt1-dev \
        nodejs \
        npm \
        postgresql-client \
        python3-dev \
        python3-pip \
        python3-setuptools \
        python3-wheel \
        swig \
        tar \
        xfonts-75dpi \
        xfonts-base \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------
# wkthmltopdf
# ---------------------------------------------------------
RUN curl -o wkhtmltox.deb -sSL https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.jammy_amd64.deb \
    && apt-get install -f --fix-missing -y ./wkhtmltox.deb \
    && rm -rf /var/lib/apt/lists/* ./wkhtmltox.deb

# ---------------------------------------------------------
# LESS-Compiler
# ---------------------------------------------------------
RUN npm install -g less less-plugin-clean-css

# ---------------------------------------------------------
# Odoo User and Directories
# ---------------------------------------------------------
RUN groupadd -g 200 srv \
 && useradd -c "Odoo Service User" -d /srv/odoo -g 200 -G 200 -r -s /bin/bash -u 2000 odoo \
 && mkdir -p /srv/odoo/addons \
             /srv/odoo/downloads \
             /srv/odoo/odoo-community-addons \
             /srv/odoo/odoo-custom-addons \
             /srv/backup \
             /var/log/odoo \
             /var/lib/odoo \
             /srv/odoo/.local/share/Odoo \
 && chown -R odoo:srv /srv/odoo /srv/backup /var/log/odoo /var/lib/odoo \
 && chmod -R 755 /srv/odoo \
 && echo 'export PATH=${PATH}:/srv/odoo/odoo:/srv/odoo/.local/bin' >> /srv/odoo/.bashrc

# ---------------------------------------------------------
# Odoo Scripts and Configurations
# ---------------------------------------------------------
COPY scripts /srv/odoo/scripts
COPY etc /srv/odoo/etc
RUN chown -R odoo:srv /srv/odoo/scripts /srv/odoo/etc \
 && chmod -R 750 /srv/odoo/scripts \
 && find /srv/odoo/etc -type f -exec chmod 640 {} \; \
 && ln -s /srv/odoo/etc/logrotate_odoo.conf /etc/logrotate.d/odoo.conf 

# ---------------------------------------------------------
# Switch to User Context
# ---------------------------------------------------------
USER odoo
WORKDIR /srv/odoo
ENV PATH="${PATH}:/srv/odoo/.local/bin"

# ---------------------------------------------------------
# clone Odoo Source
# ---------------------------------------------------------
RUN git clone --branch=${ODOO_VERSION} --depth=1 https://github.com/odoo/odoo.git odoo \
 && ls -la /srv/odoo

# ---------------------------------------------------------
# Community Addons
# ---------------------------------------------------------
RUN if [ "$BUILD_TYPE" == "PROD" ]; then \
        cp -p /srv/odoo/etc/addons.prod.json /srv/odoo/etc/addons.json; \
    else \
        cp -p /srv/odoo/etc/addons.dev.json /srv/odoo/etc/addons.json; \
    fi
RUN python3 /srv/odoo/scripts/fetch-addons.py

# ---------------------------------------------------------
# Python requirements
# => Odoo delivers its own requirements.txt by git clone:
#    /srv/odoo/requirements.txt
# => fetch-addons.py will created an additional file to:
#    /src/odoo/etc/addons-requirements.txt
# ---------------------------------------------------------
RUN pip install --break-system-packages -r /srv/odoo/odoo/requirements.txt \
 && pip install --break-system-packages -r /srv/odoo/etc/addons_requirements.txt \
 && pip install --break-system-packages -r /srv/odoo/etc/custom_requirements.txt \
 && rm -rf /srv/odoo/.cache/pip


# ---------------------------------------------------------
# Finalizing Odoo Build
# ---------------------------------------------------------
ENV ODOO_RC=/srv/odoo/etc/odoo.conf
ENV PATH=/srv/odoo/.local/bin:${PATH}

VOLUME ["/srv/odoo/etc", "/srv/odoo/addons", "/srv/odoo/odoo-community-addons", "/srv/odoo/odoo-custom-addons", "/srv/odoo/scripts", "/var/lib/odoo", "/var/log/odoo", "/srv/odoo/.local/share/Odoo", "/srv/backup"]

EXPOSE 8069/tcp
EXPOSE 8072/tcp

ENTRYPOINT ["/srv/odoo/scripts/entrypoint.sh"]
CMD ["/usr/bin/python3", "/srv/odoo/odoo/odoo-bin", "-c", "/srv/odoo/etc/odoo.conf"]
