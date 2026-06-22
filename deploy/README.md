# Deploying to Hetzner CX22 with rootless Podman

Production runs the **web app directly on the host** (venv + gunicorn under a systemd
*user* service) and spawns per-job generator containers via **rootless Podman**. This
removes the root-Docker-socket dependency the local `docker-compose.yaml` path uses: the
container runtime runs as an unprivileged user, so a web compromise or an apworld escape
lands as a mapped subuid, not host root.

> The repo's `docker-compose.yaml` / `Dockerfile.web` remain for the local-dev path. They
> are **not** used in this deployment.

## 1. Provision the server

- Create a Hetzner Cloud **CX22** (2 vCPU / 4 GB, x86_64 — required by the AP
  `linux-x86_64` frozen build), Debian 12, with your SSH key.
- Point a subdomain `A` record at the server's public IP.
- Firewall:
  ```sh
  sudo ufw allow 22,80,443/tcp
  sudo ufw enable
  ```
  Port 5000 stays closed — gunicorn binds to `127.0.0.1` only.

## 2. Create the runtime user + Podman

```sh
sudo adduser --disabled-password --gecos "" archi
sudo loginctl enable-linger archi            # user services run without a login session
sudo apt-get update && sudo apt-get install -y podman python3-venv git

# the rest as the archi user:
sudo -iu archi
systemctl --user enable --now podman.socket  # -> /run/user/$(id -u)/podman/podman.sock
podman info >/dev/null && echo "rootless podman OK"
```

## 3. Deploy the app (as `archi`)

```sh
cd ~
git clone <this-repo-url> archipelago-generator
cd archipelago-generator

# Generator image (built with Podman, not Docker):
podman build -f Dockerfile.generator -t archipelago-generator:0.6.7 .

# Web app venv:
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

mkdir -p ~/ap-jobs

# systemd user service:
mkdir -p ~/.config/systemd/user
cp deploy/archi-web.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now archi-web
systemctl --user status archi-web            # should be active
```

## 4. TLS / reverse proxy (as root)

```sh
sudo apt-get install -y caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudoedit /etc/caddy/Caddyfile                 # set your real subdomain
sudo systemctl reload caddy
```

Caddy auto-provisions a Let's Encrypt cert once DNS resolves.

## 5. Verify end-to-end

1. `podman ps` (as `archi`) works against the rootless socket.
2. `curl -s localhost:5000/` returns a redirect to a `/rooms/<id>` URL.
3. Browser at `https://your.subdomain` — valid cert, room page loads.
4. Upload a built-in-game template YAML → Generate → a download link to `AP_<seed>.zip`
   appears; download and open it.
5. Repeat with a YAML that needs an uploaded `.apworld` to confirm `custom_worlds` loading.
6. While a job runs, `podman inspect <id>` shows `NetworkDisabled`, the mem/CPU caps, no
   capabilities, and `no-new-privileges`.
7. The output zip is world-readable and the reaper removes the job dir after
   `AP_JOB_TTL` without permission errors.

## Updating

```sh
sudo -iu archi
cd ~/archipelago-generator && git pull
.venv/bin/pip install -r requirements.txt          # if deps changed
podman build -f Dockerfile.generator -t archipelago-generator:0.6.7 .   # if generator changed
systemctl --user restart archi-web
```
