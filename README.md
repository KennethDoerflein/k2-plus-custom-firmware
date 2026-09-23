# K2 Plus Custom Firmware (Security-Hardened Fork)

A security-hardened fork of [Jacob10383's K2 Plus Custom Firmware](https://github.com/Jacob10383/k2-plus-custom-firmware).

This fork removes embedded developer SSH keys, redirects all OTA updates to
self-hosted GitHub repositories, disables third-party telemetry, and adds
material-specific Klipper macro overrides.

> [!IMPORTANT]
> After installation is complete, log in to your printer via SSH and immediately
> run `passwd` to change the default `creality_2024` root password to something
> secure!

## Security Changes

- **SSH Backdoor Removed**: The installer now automatically deletes the embedded
  developer Ed25519 public key from `/etc/ssh/authorized_keys/root` after
  flashing the root filesystem.
- **Self-Hosted OTA**: All firmware downloads and bootstrap clones now point to
  this repository and its companion forks, not `firmware.jacobean.xyz`.
- **Telemetry Disabled**: Pseudonymous install telemetry to the original author's
  server has been disabled.
- **Local Firmware Flashing**: `install.py` can detect firmware binaries placed
  next to the script and use them instead of downloading, enabling fully offline
  installation.

## Upgrading from Jacob's Firmware

If you already have Jacob's custom firmware installed, you must swap back to the stock firmware before installing this fork.

1. SSH into the printer.
2. Run `swap` to switch back to the stock firmware environment. The printer will reboot.
3. SSH into the printer again (it will now be running stock firmware).
4. Run the installer for this fork:
   ```sh
   python3 -c "import urllib.request; exec(urllib.request.urlopen('https://raw.githubusercontent.com/KennethDoerflein/k2-plus-custom-firmware/main/install.py').read(), {'__name__':'__main__'})"
   ```
5. After the installer completes and reboots into the custom firmware, SSH in one more time and run `bootstrap --replace` to update the Git remotes and replace the payloads on your printer with the hardened versions. (Your configuration files will be automatically backed up).

## Repository Layout

- `kernel.img` and `rootfs.ext2` — firmware images
- `install.py` — installation entrypoint
- `bootstrap` and `swap` — printer setup and firmware-slot commands
- `index` — release metadata
- `extras/` — Kalico extensions for K2-specific hardware and functionality
- `docs/` — user documentation

## Companion Repositories

- [Kalico (Klipper fork)](https://github.com/KennethDoerflein/kalico)
- [Fluidd (Web UI)](https://github.com/KennethDoerflein/fluidd)

This is an independent project and is not affiliated with Creality.
