# Note: Kernel module (donor_dump) should be built on target system, not in container
# The module requires kernel headers matching the host kernel version
# Build instructions are available in src/donor_dump/Makefile

# ---------- build stage for VFIO constants ----------
FROM ubuntu:22.04 AS build

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC \
    PIP_BREAK_SYSTEM_PACKAGES=1

# ── base build deps ──────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip build-essential \
        linux-headers-generic \
        pciutils kmod ca-certificates git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src

# ── Clone voltcyclone-fpga repository during build ───────────────────────────
# This eliminates user errors with git submodules
RUN mkdir -p lib && \
    git clone --depth 1 https://github.com/VoltCyclone/voltcyclone-fpga.git lib/voltcyclone-fpga && \
    echo "✓ voltcyclone-fpga cloned successfully" && \
    ls -la lib/voltcyclone-fpga/

# ── VFIO constants patching ───────────────────────────────────────────────────
COPY vfio_helper.c patch_vfio_constants.py build_vfio_constants.sh ./
COPY src/cli/vfio_constants.py ./src/cli/
RUN mkdir -p src/cli && \
    chmod +x build_vfio_constants.sh && \
    (./build_vfio_constants.sh && cp src/cli/vfio_constants.py vfio_constants_patched.py) || \
    (echo "⚠ VFIO constants build failed, using original" && cp src/cli/vfio_constants.py vfio_constants_patched.py) && \
    echo "Content of patched file:" && head -20 vfio_constants_patched.py | grep -A 10 "Ioctl numbers" || echo "No ioctl numbers section found"

# ---------- runtime ----------
FROM ubuntu:22.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC \
    PCILEECH_PRODUCTION_MODE=true \
    PCILEECH_ALLOW_MOCK_DATA=false \
    PCILEECH_CONTAINER_MODE=true

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 python3-pip pciutils bsdextrautils kmod ca-certificates git sudo \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Create non-root user and configure sudo
RUN useradd -m -r appuser && \
    echo "appuser ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers && \
    echo "Defaults !requiretty" >> /etc/sudoers

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt requirements-tui.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt -r requirements-tui.txt

# Copy application files
COPY src ./src
COPY configs ./configs
COPY pcileech.py .

# Copy voltcyclone-fpga from build stage (cloned during build)
COPY --from=build /src/lib/voltcyclone-fpga ./lib/voltcyclone-fpga

# Copy the patched VFIO constants from build stage
COPY --from=build /src/vfio_constants_patched.py ./src/cli/vfio_constants.py

# Ensure __init__.py files exist in directories that don't have them
# IMPORTANT: Do NOT overwrite existing __init__.py files that have actual content
RUN for dir in $(find ./src -type d); do \
        if [ ! -f "$dir/__init__.py" ]; then \
            touch "$dir/__init__.py"; \
        fi; \
    done

# Copy and setup entrypoint
COPY entrypoint.sh /usr/local/bin/entrypoint
RUN chmod 755 /usr/local/bin/entrypoint

# Set up environment and permissions
ENV PYTHONPATH=/app:/app/src
RUN mkdir -p /app/output && chown appuser /app/output

# Health check to verify essential dependencies
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import psutil, pydantic, sys; sys.exit(0)" || exit 1

USER appuser
ENTRYPOINT ["entrypoint"]
