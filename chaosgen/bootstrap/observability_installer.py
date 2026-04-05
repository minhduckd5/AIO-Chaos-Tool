"""
Observability Installer — tiered installation of Prometheus, Grafana, and Loki.

Tier 1 — Kubernetes:  helm install kube-prometheus-stack + Loki
Tier 2 — Docker:      inject services into docker-compose.yml
Tier 3 — Bare Metal:  generate install_prometheus.sh / install_loki.sh (no SSH)
Tier 4 — Cloud/Serverless: generate Terraform snippets

CRITICAL (Blind Spot 3): Every tier that shells out calls check_prerequisites()
first. Missing binaries raise PrerequisiteError with install URLs — never a
raw FileNotFoundError traceback.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import yaml

from chaosgen.bootstrap.exceptions import BootstrapError, PrerequisiteError
from chaosgen.schemas.discovery import EnvironmentProfile, EnvironmentType, ObservabilityProfile

logger = logging.getLogger(__name__)

_BINARY_INSTALL_URLS: dict[str, str] = {
    "helm":    "https://helm.sh/docs/intro/install/",
    "kubectl": "https://kubernetes.io/docs/tasks/tools/",
    "docker":  "https://docs.docker.com/get-docker/",
}

_HELM_PROMETHEUS_REPO = "https://prometheus-community.github.io/helm-charts"
_HELM_GRAFANA_REPO = "https://grafana.github.io/helm-charts"


def check_prerequisites(required: list[str]) -> None:
    """
    Verify each binary in `required` exists on PATH.
    Raises PrerequisiteError listing all missing binaries with install URLs.
    """
    missing = [
        f"  - '{b}': {_BINARY_INSTALL_URLS.get(b, 'see documentation')}"
        for b in required
        if shutil.which(b) is None
    ]
    if missing:
        raise PrerequisiteError(
            "The following required binaries are missing. Install them before running bootstrap:\n"
            + "\n".join(missing)
        )


class ObservabilityInstaller:
    def __init__(
        self,
        env_profile: EnvironmentProfile,
        obs_profile: ObservabilityProfile,
        namespace: str = "monitoring",
        compose_file: str | None = None,
        output_dir: str = ".",
    ) -> None:
        self._env = env_profile
        self._obs = obs_profile
        self._namespace = namespace
        self._compose_file = compose_file
        self._output_dir = Path(output_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def install(self) -> list[str]:
        """
        Execute the appropriate tier.
        Returns a list of human-readable action summaries.
        """
        env = self._env.type

        if env == EnvironmentType.KUBERNETES:
            return self._install_tier1_kubernetes()
        if env == EnvironmentType.DOCKER_COMPOSE:
            return self._install_tier2_docker()
        if env in (EnvironmentType.BARE_METAL, EnvironmentType.CLOUD_VM):
            return self._install_tier3_scripts()
        if env == EnvironmentType.SERVERLESS:
            return self._install_tier4_terraform()

        return [f"Unsupported environment '{env.value}' — no automatic bootstrap available."]

    # ------------------------------------------------------------------
    # Tier 1 — Kubernetes (Helm)
    # ------------------------------------------------------------------

    def _install_tier1_kubernetes(self) -> list[str]:
        check_prerequisites(["helm", "kubectl"])  # Blind Spot 3 fix

        actions: list[str] = []
        ns = self._namespace

        self._run(["kubectl", "create", "namespace", ns, "--dry-run=client", "-o", "yaml"])
        self._run(["kubectl", "apply", "-f", "-"])

        if not self._obs.has_metrics:
            try:
                self._run(["helm", "repo", "add", "prometheus-community", _HELM_PROMETHEUS_REPO])
                self._run(["helm", "repo", "update"])
                self._run([
                    "helm", "upgrade", "--install", "kube-prometheus",
                    "prometheus-community/kube-prometheus-stack",
                    "--namespace", ns,
                    "--create-namespace",
                    "--set", "grafana.enabled=true",
                ])
                actions.append(f"Installed kube-prometheus-stack in namespace '{ns}'")
            except BootstrapError as exc:
                actions.append(f"kube-prometheus-stack install failed: {exc}")

        if not self._obs.has_logs:
            try:
                self._run(["helm", "repo", "add", "grafana", _HELM_GRAFANA_REPO])
                self._run(["helm", "repo", "update"])
                self._run([
                    "helm", "upgrade", "--install", "loki",
                    "grafana/loki-stack",
                    "--namespace", ns,
                    "--create-namespace",
                ])
                actions.append(f"Installed Loki stack in namespace '{ns}'")
            except BootstrapError as exc:
                actions.append(f"Loki install failed: {exc}")

        return actions

    # ------------------------------------------------------------------
    # Tier 2 — Docker Compose injection
    # ------------------------------------------------------------------

    def _install_tier2_docker(self) -> list[str]:
        check_prerequisites(["docker"])  # Blind Spot 3 fix

        compose_path = self._compose_file
        if compose_path is None:
            for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml"):
                if Path(name).exists():
                    compose_path = name
                    break
        if compose_path is None:
            raise BootstrapError("No docker-compose.yml found in current directory.")

        with open(compose_path, encoding="utf-8") as f:
            compose: dict = yaml.safe_load(f) or {}

        services: dict = compose.setdefault("services", {})
        volumes: dict = compose.setdefault("volumes", {})
        actions: list[str] = []

        if not self._obs.has_metrics and "prometheus" not in services:
            services["prometheus"] = {
                "image": "prom/prometheus:latest",
                "ports": ["9090:9090"],
                "volumes": ["prometheus_data:/prometheus"],
                "restart": "unless-stopped",
            }
            volumes["prometheus_data"] = None
            actions.append("Injected Prometheus service into docker-compose.yml")

        if not self._obs.has_metrics and "grafana" not in services:
            services["grafana"] = {
                "image": "grafana/grafana:latest",
                "ports": ["3000:3000"],
                "volumes": ["grafana_data:/var/lib/grafana"],
                "environment": {"GF_SECURITY_ADMIN_PASSWORD": "admin"},
                "restart": "unless-stopped",
            }
            volumes["grafana_data"] = None
            actions.append("Injected Grafana service into docker-compose.yml")

        if not self._obs.has_logs and "loki" not in services:
            services["loki"] = {
                "image": "grafana/loki:latest",
                "ports": ["3100:3100"],
                "restart": "unless-stopped",
            }
            actions.append("Injected Loki service into docker-compose.yml")

        with open(compose_path, "w", encoding="utf-8") as f:
            yaml.dump(compose, f, default_flow_style=False, sort_keys=False)

        actions.append(f"Updated {compose_path} — run: docker compose up -d")
        return actions

    # ------------------------------------------------------------------
    # Tier 3 — Bare Metal / VM script generation (no SSH, no credentials)
    # ------------------------------------------------------------------

    def _install_tier3_scripts(self) -> list[str]:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        actions: list[str] = []

        if not self._obs.has_metrics:
            script = self._output_dir / "install_prometheus.sh"
            script.write_text(self._prometheus_install_script(), encoding="utf-8")
            actions.append(f"Generated {script} — run on the target host as root")

            prom_cfg = self._output_dir / "prometheus.yml"
            prom_cfg.write_text(self._prometheus_config(), encoding="utf-8")
            actions.append(f"Generated {prom_cfg} — copy to /etc/prometheus/prometheus.yml")

        if not self._obs.has_logs:
            script = self._output_dir / "install_loki.sh"
            script.write_text(self._loki_install_script(), encoding="utf-8")
            actions.append(f"Generated {script} — run on the target host as root")

        return actions

    # ------------------------------------------------------------------
    # Tier 4 — Cloud / Serverless (Terraform snippet generation)
    # ------------------------------------------------------------------

    def _install_tier4_terraform(self) -> list[str]:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        tf_file = self._output_dir / "observability.tf"
        tf_file.write_text(self._cloudwatch_terraform_snippet(), encoding="utf-8")
        return [
            f"Generated {tf_file} — add to your Terraform project and run terraform apply",
        ]

    # ------------------------------------------------------------------
    # Script templates
    # ------------------------------------------------------------------

    @staticmethod
    def _prometheus_install_script() -> str:
        return """\
#!/usr/bin/env bash
# Generated by ChaosGen — Prometheus installation for bare metal / VM
set -euo pipefail

PROM_VERSION="2.51.0"
PROM_USER="prometheus"
PROM_DIR="/etc/prometheus"
DATA_DIR="/var/lib/prometheus"

echo "[ChaosGen] Installing Prometheus ${PROM_VERSION}..."
useradd --no-create-home --shell /bin/false ${PROM_USER} 2>/dev/null || true
mkdir -p ${PROM_DIR} ${DATA_DIR}

curl -LO "https://github.com/prometheus/prometheus/releases/download/v${PROM_VERSION}/prometheus-${PROM_VERSION}.linux-amd64.tar.gz"
tar xzf prometheus-*.linux-amd64.tar.gz
cp prometheus-*/prometheus /usr/local/bin/
cp prometheus-*/promtool  /usr/local/bin/
cp -r prometheus-*/consoles  ${PROM_DIR}/
cp -r prometheus-*/console_libraries ${PROM_DIR}/
cp prometheus.yml ${PROM_DIR}/prometheus.yml

chown -R ${PROM_USER}:${PROM_USER} ${PROM_DIR} ${DATA_DIR}

cat > /etc/systemd/system/prometheus.service <<EOF
[Unit]
Description=Prometheus
After=network.target

[Service]
User=${PROM_USER}
ExecStart=/usr/local/bin/prometheus \\\\
  --config.file=${PROM_DIR}/prometheus.yml \\\\
  --storage.tsdb.path=${DATA_DIR}
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable prometheus
systemctl start prometheus
echo "[ChaosGen] Prometheus running on :9090"
"""

    @staticmethod
    def _prometheus_config() -> str:
        return """\
# Generated by ChaosGen
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]

  # Add your application targets below:
  # - job_name: "my-app"
  #   static_configs:
  #     - targets: ["my-app-host:8080"]
"""

    @staticmethod
    def _loki_install_script() -> str:
        return """\
#!/usr/bin/env bash
# Generated by ChaosGen — Loki installation for bare metal / VM
set -euo pipefail

LOKI_VERSION="2.9.4"

echo "[ChaosGen] Installing Loki ${LOKI_VERSION}..."
curl -LO "https://github.com/grafana/loki/releases/download/v${LOKI_VERSION}/loki-linux-amd64.zip"
unzip -o loki-linux-amd64.zip
chmod +x loki-linux-amd64
mv loki-linux-amd64 /usr/local/bin/loki

cat > /etc/systemd/system/loki.service <<EOF
[Unit]
Description=Loki
After=network.target

[Service]
ExecStart=/usr/local/bin/loki -config.file=/etc/loki/loki-config.yaml
Restart=always

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /etc/loki
cat > /etc/loki/loki-config.yaml <<EOF
auth_enabled: false
server:
  http_listen_port: 3100
ingester:
  lifecycler:
    ring:
      kvstore:
        store: inmemory
      replication_factor: 1
schema_config:
  configs:
    - from: 2020-10-24
      store: boltdb-shipper
      object_store: filesystem
      schema: v11
      index:
        prefix: index_
        period: 24h
storage_config:
  boltdb_shipper:
    active_index_directory: /tmp/loki/index
    shared_store: filesystem
    cache_location: /tmp/loki/boltdb-cache
  filesystem:
    directory: /tmp/loki/chunks
EOF

systemctl daemon-reload
systemctl enable loki
systemctl start loki
echo "[ChaosGen] Loki running on :3100"
"""

    @staticmethod
    def _cloudwatch_terraform_snippet() -> str:
        return """\
# Generated by ChaosGen — CloudWatch → Prometheus Remote Write
# Add to your existing Terraform configuration

resource "aws_prometheus_workspace" "chaosgen" {
  alias = "chaosgen-metrics"
}

resource "aws_iam_role" "prometheus_remote_write" {
  name = "chaosgen-prometheus-remote-write"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "aps.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "prometheus_remote_write" {
  role       = aws_iam_role.prometheus_remote_write.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonPrometheusRemoteWriteAccess"
}

output "prometheus_endpoint" {
  value = aws_prometheus_workspace.chaosgen.prometheus_endpoint
}
"""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, cmd: list[str]) -> None:
        logger.debug("Running: %s", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise BootstrapError(
                f"Command failed: {' '.join(cmd)}\n"
                f"stdout: {exc.stdout}\nstderr: {exc.stderr}"
            ) from exc
