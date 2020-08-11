#!/usr/bin/env python3

import logging

from urllib.parse import urlparse

from ops.charm import CharmBase

# from ops.framework import StoredState
from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    # MaintenanceStatus,
    # WaitingStatus,
    # ModelError,
)

logger = logging.getLogger(__name__)

REQUIRED_SETTINGS = ["user", "pass"]
# Secrets
USER_SECRET_PATH = "/secrets/user"
USER_SECRET_KEY_NAME = "user"
PASS_SECRET_PATH = "/secrets/pass"
PASS_SECRET_KEY_NAME = "pass"

# We expect the transmission container to use the
# default ports
HTTP_PORT = 9091
TORRENT_PORT = 51413


class TransmissionCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)

        # Register all of the events we want to observe
        self.framework.observe(self.on.config_changed, self.configure_pod)
        self.framework.observe(self.on.start, self.configure_pod)
        self.framework.observe(self.on.upgrade_charm, self.configure_pod)

    def _check_password(self, password):
        if len(password) < 8:
            return "password must have at least 8 characters"

    def _check_settings(self):
        problems = []
        config = self.model.config

        for setting in REQUIRED_SETTINGS:
            if not config.get(setting):
                problem = f"missing config {setting}"
                problems.append(problem)

        passwd_problem = self._check_password(config["pass"])
        if passwd_problem:
            problems.append(passwd_problem)
        return ";".join(problems)

    def _make_pod_image_details(self):
        config = self.model.config
        image_details = {
            "imagePath": config["transmission_image_path"],
        }
        if config["transmission_image_username"]:
            image_details.update(
                {
                    "username": config["transmission_image_username"],
                    "password": config["transmission_image_password"],
                }
            )
        return image_details

    def _make_pod_ports(self):
        return [
            {"name": "http", "containerPort": HTTP_PORT, "protocol": "TCP"},
            {"name": "tcp", "containerPort": TORRENT_PORT, "protocol": "TCP",},
            {"name": "udp", "containerPort": TORRENT_PORT, "protocol": "UDP",},
        ]

    def _make_pod_envconfig(self):
        config = self.model.config

        return {
            "PUID": config["puid"],
            "PGID": config["pgid"],
            "FILE__USER": USER_SECRET_PATH,
            "FILE__PASS": PASS_SECRET_PATH,
            "TZ": config["timezone"],
        }

    def _make_pod_command(self):
        return [
            "sh",
            "-c",
            "echo -e 'ID=ubuntu\nVERSION_ID=\"20.04\"' > /etc/os-release && /init",
        ]

    def _make_pod_volume_config(self):
        return [
            {
                "name": "secrets",
                "mountPath": "/secrets",
                "secret": {
                    "name": f"{self.app.name}-secrets",
                    "files": [
                        {"key": USER_SECRET_KEY_NAME, "path": "user", "mode": 0o444,},
                        {"key": PASS_SECRET_KEY_NAME, "path": "pass", "mode": 0o444,},
                    ],
                },
            }
        ]

    def _make_pod_ingress_resources(self):
        site_url = self.model.config["site_url"]

        if not site_url:
            return

        parsed = urlparse(site_url)

        if not parsed.scheme.startswith("http"):
            return

        max_file_size = self.model.config["max_file_size"]
        ingress_whitelist_source_range = self.model.config[
            "ingress_whitelist_source_range"
        ]

        annotations = {
            "nginx.ingress.kubernetes.io/proxy-body-size": "{}m".format(max_file_size)
        }

        if ingress_whitelist_source_range:
            annotations[
                "nginx.ingress.kubernetes.io/whitelist-source-range"
            ] = ingress_whitelist_source_range

        ingress = {
            "name": "{}-ingress".format(self.app.name),
            "annotations": annotations,
            "spec": {
                "rules": [
                    {
                        "host": parsed.hostname,
                        "http": {
                            "paths": [
                                {
                                    "path": "/",
                                    "backend": {
                                        "serviceName": self.app.name,
                                        "servicePort": HTTP_PORT,
                                    },
                                }
                            ]
                        },
                    }
                ]
            },
        }
        return [ingress]

    def _make_pod_secrets(self):
        return [
            {
                "name": f"{self.app.name}-secrets",
                "type": "Opaque",
                "stringData": {
                    USER_SECRET_KEY_NAME: self.model.config["user"],
                    PASS_SECRET_KEY_NAME: self.model.config["pass"],
                },
            }
        ]

    def configure_pod(self, event):
        # Continue only if the unit is the leader
        if not self.unit.is_leader():
            self.unit.status = ActiveStatus()
            return
        # Check problems in the settings
        problems = self._check_settings()
        if problems:
            self.unit.status = BlockedStatus(problems)
            return

        self.unit.status = BlockedStatus("Assembling pod spec")
        image_details = self._make_pod_image_details()
        ports = self._make_pod_ports()
        env_config = self._make_pod_envconfig()
        command = self._make_pod_command()
        volume_config = self._make_pod_volume_config()
        ingress_resources = self._make_pod_ingress_resources()
        secrets = self._make_pod_secrets()

        pod_spec = {
            "version": 3,
            "containers": [
                {
                    "name": self.framework.model.app.name,
                    "imageDetails": image_details,
                    "ports": ports,
                    "envConfig": env_config,
                    "command": command,
                    "volumeConfig": volume_config,
                }
            ],
            "kubernetesResources": {
                "ingressResources": ingress_resources or [],
                "secrets": secrets,
            },
        }
        self.model.pod.set_spec(pod_spec)
        self.unit.status = ActiveStatus()


if __name__ == "__main__":
    main(TransmissionCharm)
