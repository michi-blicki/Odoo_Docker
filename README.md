<a id="readme-top"></a>

[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![project_license][license-shield]][license-url]

<h3 align="center">Odoo Docker Image</h3>

<p>
    Create your own fully customizable Docker Image for Odoo
</p>

<details>
    <summary>Table of Contents</summary>
    <ol>
        <li><a href="#about-the-project">About the Project</a></li>
        <li><a href="#getting-started">Getting Started</a></li>
        <li><a href="#configuratoin">Configuration</a></li>
        <li><a href="#build-docker-image">Build Docker Image</a></li>
        <li><a href="#docker-compose">Docker Compose File</a></li>
        <li><a href="#usage">Usage</a></li>
        <li><a href="#license">License</a></li>
        <li><a href="#support">Support</a></li>
    </ol>
</details>

<!-- About the Project -->
## About the Project
This small set of scripts and configuration files let you create an individual Docker image for Odoo.

## Getting Started
  1. Clone this repository
  2. Specify required addons in addons.dev.json or addons.prod.json
  3. Build your Odoo docker image using
  4. Modify docker-compose.yml to your needs
  5. Run Docker Containers

## Configuration

### addons.json Files
If you are able to use Odoo Community Addons, you will find them somewhere in GitHub or GitLab. For both,
you are able to create entries in the addons.json files by the following syntax:
	{
		"repo": "<GitHub Repository>",
		"branch": "18.0",
		"repo_dir": "<Download Directory>",
		"addons": [
			"<Addon_A>",
            "<Addon_B">
		]
	},

If the Addon can be found on main directory, to not describe the addons-Section.

Currently, only DEV and PROD are supported: addons.dev.json, addons.prod.json.

#### Example 1 with multiple addons in one repository
	{
		"repo": "https://github.com/OCA/server-ux.git",
		"branch": "18.0",
		"repo_dir": "server-ux",
		"addons": [
			"date_range",
			"date_range_account",
			"developer_menu"
		]
	}

#### Example 2 single addon in one repository
	{
		"repo": "https://github.com/michi-blicki/l10n_ch_clubmanagement_sfv.git",
		"branch": "18.0",
		"repo_dir": "l10n_ch_clubmanagement_sfv"
	}


## Build Docker Image
Generate a new docker image using <YOUR_NAME> as label:

```sh
docker build . -t <YOUR_NAME>/odoo:18.0-[DEV|PROD] --build-arg BUILD_TYPE=[DEV|PROD]
```

## Docker Compose
### Containers
There is a sample for your docker-compose.yml file. The are 4 containers in use:
* odoo-db
  The PostgreSQL database required by Odoo.
* odoo-app
  The main Odoo application - your own created Odoo image
* odoo-web
  An NgINX reverse proxy
* certbot
  Letsencrypt Certbot to create TLS/SSL certificates

Please take a look into the example file. You will need to specify passwords as
well as, if changed or extended, the environment parameters.

#### Odoo App Container
You are able to specify any Odoo configuration parameter in the environment-Setting
of your odoo-app container. The Entrypoint script of the container will read all
environment variables parsing ODOO-keys and setting them into specified odoo.conf
before launching Odoo. Parameters within the environment section of the docker-compose.yml
will overwrite the parameter within odoo.conf.default. The odoo.conf file is generated
by the odoo_conf.py script using the odoo.conf.default and parameters from environment-section
and used then by odoo-bin launcher.

### Networks
Within the example, I'm using two separate docker networks:
* net
  This is in use for internal communication between Odoo App and PostgreSQL database
* web
  This is for external communication between Odoo App and NgINX proxy

### Volumes
* postgres_conf
  Contains the PostgreSQL configuration files
* postgres_data
  The database data with the Odoo database
* odoo_data
  Static Odoo data files
* odoo_local_share
  Odoo locally shared files
* odoo_custom_addons
  If you want to use custom addons, that are not available on GitHub/GitLab repositories
* odoo_logs
  Will contain only odoo.log
* backup
  Where backups can be created in
* nginx_conf
  Reverse Proxy configuration file, like default.conf or an odoo.conf
* nginx_cert
  TLS/SSL certificate root directory. Shall met the requierments for Letsencrypt
* nginx_logs
  NgINX reverse proxy's logs
* certb_html
  In use by Letsencrypt's certbot container for the ACME challenge

## Usage
If unchanged from the example, docker compose knows the correct order to launch the
containers. Otherwise, you will need to:
1. Launch PostgreSQL database (eq. ```docker compose up -d odoo-db```)
2. Launch Odoo appliaction(eq. ```docker compose up -d odoo-app```)
3. Launch NgINX reverse proxy (eq. ```docker compose up -d odoo-web```)

Refer to the guide of Letsencrypt's certbot container in order to create the TLS/SSL
certificates for the first time.

## License
This repository is licensed under <strong>GNU General Public License - Version 3, June 29th 2007</strong>

Any extension or modification to this repository must be reported to the owner of this repository. Perhabs,
your invention is worth updating the project to make it even better.

## Support
This repository is maintained by myself and as I'm the only maintainer and this is a off-working project,
please feel free to report issues, but do not expect reaction within suitable time.