# FlagPilot's sandbox image: the tools the challenges ask for, so that a missing tool is no longer a failure cause, and
# one container per attempt (no network, no capabilities, not root: see flagpilot/sandbox.py).
#
#   docker build -t flagpilot-sandbox deploy/container        # or: podman build -t flagpilot-sandbox deploy/container
#   FLAGPILOT_CONTAINER_IMAGE=flagpilot-sandbox python3 -m flagpilot doctor
#
# With Apptainer (clusters): apptainer build flagpilot-sandbox.sif deploy/container/flagpilot.def
#   FLAGPILOT_CONTAINER_IMAGE=$PWD/flagpilot-sandbox.sif python3 -m flagpilot doctor
FROM python:3.12-slim-bookworm
ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY apt.txt pip.txt /opt/flagpilot/
RUN echo "wireshark-common wireshark-common/install-setuid boolean false" | debconf-set-selections \
 && apt-get update \
 && grep -v '^#' /opt/flagpilot/apt.txt | xargs apt-get install -y --no-install-recommends \
 && gem install --no-document zsteg \
 && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN grep -v '^#' /opt/flagpilot/pip.txt | xargs pip install
WORKDIR /work
