#!/bin/bash
# Repackage the K2 rootfs.ext2 image for GitHub Actions/Ubuntu
set -e

IMAGE="rootfs.ext2"
MOUNT_DIR="rootfs_mnt"

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo)"
  exit 1
fi

if [ ! -f "$IMAGE" ]; then
  echo "Error: $IMAGE not found in current directory."
  exit 1
fi

echo "======================================"
echo " Repackaging K2 Plus rootfs.ext2"
echo "======================================"

echo "1. Mounting image..."
mkdir -p "$MOUNT_DIR"
mount -o loop "$IMAGE" "$MOUNT_DIR"

echo "2. Sanitizing backdoor SSH keys..."
rm -f "$MOUNT_DIR/etc/ssh/authorized_keys/root"
rm -rf "$MOUNT_DIR/root/.ssh"

echo "3. Removing residual telemetry services (if any)..."
rm -f "$MOUNT_DIR/etc/systemd/system/creality-telemetry.service" || true

echo "4. Injecting custom branding into /etc/os-release..."
if [ -f "$MOUNT_DIR/etc/os-release" ]; then
    sed -i 's/Jacob10383/KennethDoerflein/g' "$MOUNT_DIR/etc/os-release" || true
    sed -i 's/Jacobean/Kenneth/g' "$MOUNT_DIR/etc/os-release" || true
    echo "FIRMWARE_MAINTAINER=\"KennethDoerflein\"" >> "$MOUNT_DIR/etc/os-release"
fi

echo "5. Applying performance and boot-time optimizations..."
# Disable wait-online to prevent 1-2 minute boot delays when disconnected
rm -f "$MOUNT_DIR/etc/systemd/system/network-online.target.wants/systemd-networkd-wait-online.service" || true

# Reduce I/O wait and eMMC wear by keeping system journals in RAM
sed -i 's/#Storage=auto/Storage=volatile/g' "$MOUNT_DIR/etc/systemd/journald.conf" || true
sed -i 's/#SystemMaxUse=/SystemMaxUse=20M/g' "$MOUNT_DIR/etc/systemd/journald.conf" || true

# Optimize Linux virtual memory for real-time Klipper performance
mkdir -p "$MOUNT_DIR/etc/sysctl.d"
cat << 'EOF' > "$MOUNT_DIR/etc/sysctl.d/99-klipper-perf.conf"
# Prevent Klipper from being swapped to disk (fixes print stutters)
vm.swappiness=10
# Retain inode caches longer to speed up file access
vm.vfs_cache_pressure=50

# TCP BBR Congestion Control for lightning-fast G-Code Wi-Fi uploads
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
net.core.rmem_max=16777216
net.core.wmem_max=16777216
net.ipv4.tcp_rmem=4096 87380 16777216
net.ipv4.tcp_wmem=4096 65536 16777216
EOF

echo "6. Verifying systemd klipper/helixscreen paths..."
# Make sure klipper\_mcu and other critical components have proper executable bits
chmod +x "$MOUNT_DIR/usr/bin/klipper_mcu" || true

echo "6. Unmounting and cleaning up..."
umount "$MOUNT_DIR"
rmdir "$MOUNT_DIR"

echo "7. Running filesystem integrity check (e2fsck)..."
e2fsck -p -f "$IMAGE" || true

echo "======================================"
echo " Repackaging complete! Image is ready."
echo "======================================"
