# Setup: deploying apworlds.gg

Concise, replicable steps for the production deploy: web app on the host + per-job
**rootless Podman** containers + **Caddy** TLS. Tested on a Hetzner CX22 (Ubuntu 26.04,
x86_64, ~3.8 GB RAM). Domain DNS is proxied through Cloudflare.

Replace `apworlds.gg`, the IP, and the GitHub URL as needed.

## 0. Local: unlock the SSH key once

The passphrase-protected key authenticates both GitHub and the server. Load it into an
agent so subsequent `ssh`/`git` don't reprompt:

```sh
ssh-agent -a /tmp/ap-deploy.sock
SSH_AUTH_SOCK=/tmp/ap-deploy.sock ssh-add ~/.ssh/id_ed25519   # enter passphrase
```

Prefix remote commands with `SSH_AUTH_SOCK=/tmp/ap-deploy.sock`, and use `ssh -A` when the
server needs your key (git clone/pull).

## 1. DNS

Point `apworlds.gg` (A/AAAA) at the server. With Cloudflare proxy on, that's the edge IP;
the Let's Encrypt HTTP-01 challenge still reaches the origin. (Add a `www` record too if you
want the `www`→apex redirect.)

## 2. Provision the host (as root)

`ssh root@<IP>`, then:

```sh
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg podman uidmap slirp4netns \
    fuse-overlayfs python3-venv git ufw

# Caddy official apt repo
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
apt-get update && apt-get install -y caddy

# Firewall (allow SSH first)
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

# Unprivileged runtime user for rootless Podman
adduser --disabled-password --gecos "" archi
loginctl enable-linger archi
grep -q '^archi:' /etc/subuid || usermod --add-subuids 100000-165535 archi
grep -q '^archi:' /etc/subgid || usermod --add-subgids 100000-165535 archi

# Let your key log in directly as archi (for agent-forwarded git)
install -d -m700 -o archi -g archi /home/archi/.ssh
install -m600 -o archi -g archi /root/.ssh/authorized_keys /home/archi/.ssh/authorized_keys
```

## 3. Deploy the app (as archi)

`ssh -A archi@<IP>`, then:

```sh
export XDG_RUNTIME_DIR=/run/user/$(id -u)            # needed for systemctl --user over SSH
systemctl --user enable --now podman.socket          # -> $XDG_RUNTIME_DIR/podman/podman.sock

ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts
git clone git@github.com:harmonherring/archipelago-generator.git
cd archipelago-generator

podman build -f Dockerfile.generator -t archipelago-generator:0.6.7 .   # ~740 MB, slow
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
mkdir -p ~/ap-jobs

mkdir -p ~/.config/systemd/user
cp deploy/archi-web.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now archi-web
```

The unit (`deploy/archi-web.service`) points `DOCKER_HOST` at the Podman socket and caps
each job at `AP_MEM_LIMIT=1536m` so two concurrent jobs fit in ~3.8 GB.

## 4. TLS / reverse proxy (as root)

```sh
cp /home/archi/archipelago-generator/deploy/Caddyfile /etc/caddy/Caddyfile
systemctl reload caddy        # provisions the Let's Encrypt cert once DNS resolves
```

## 5. Verify

```sh
# on the server (as archi):
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/      # 303
# from anywhere:
curl -sI https://apworlds.gg                                         # 303, valid cert
```

Full generation smoke test (built-in DLCQuest game) from the server:

```sh
B=http://127.0.0.1:5000
printf 'name: Tester\ngame: DLCQuest\nDLCQuest: {}\n' > /tmp/t.yaml
RID=$(curl -s -o /dev/null -w '%{redirect_url}' "$B/" | sed 's#.*/rooms/##')
curl -s -F "yamls=@/tmp/t.yaml" "$B/rooms/$RID/uploads" >/dev/null
curl -s -X POST "$B/rooms/$RID/generate" >/dev/null
sleep 20 && curl -s "$B/rooms/$RID/state"      # expect "status":"done" + a download link
```

## Updating

```sh
ssh -A archi@<IP>
cd ~/archipelago-generator && git pull
.venv/bin/pip install -r requirements.txt                                   # if deps changed
podman build -f Dockerfile.generator -t archipelago-generator:0.6.7 .       # if generator changed
export XDG_RUNTIME_DIR=/run/user/$(id -u) && systemctl --user restart archi-web
```
