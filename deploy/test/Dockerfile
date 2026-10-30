# A throwaway "fresh droplet": Ubuntu 24.04 with systemd as PID 1, sshd, and root as the admin
# (like DigitalOcean / Hetzner). Used only by deploy/test/docker_test.sh.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive container=docker
RUN apt-get update -q && apt-get install -y -q --no-install-recommends \
      systemd systemd-sysv dbus ca-certificates curl openssh-server openssh-client sudo shellcheck python3 \
 && rm -rf /var/lib/apt/lists/* \
 && rm -f /etc/systemd/system/*.wants/* /lib/systemd/system/multi-user.target.wants/getty* \
 && systemctl mask systemd-firstboot.service systemd-udevd.service systemd-modules-load.service \
      sys-kernel-debug.mount sys-kernel-tracing.mount getty.target console-getty.service \
 && systemctl enable ssh.service \
 # what DigitalOcean's cloud-init leaves behind: passwords allowed in a 50- drop-in
 && mkdir -p /etc/ssh/sshd_config.d \
 && printf 'PasswordAuthentication yes\n' > /etc/ssh/sshd_config.d/50-cloud-init.conf \
 # root's key, as the DO control panel would install it
 && mkdir -p /root/.ssh && chmod 700 /root/.ssh \
 && ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/test_key -C clive-test \
 && cp /root/.ssh/test_key.pub /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
