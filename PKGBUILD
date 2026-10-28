# Maintainer: local build
pkgname=omen88d2
pkgver=0.1.0
pkgrel=1
pkgdesc="Fan control with curves for HP OMEN 15-en1xxx (board 88D2) on Linux"
arch=('any')
url="https://github.com/seakyy/OmenMon-Reborn/issues/181"
license=('GPL-3.0-or-later')
depends=('python')
optdepends=('lm_sensors: richer hwmon temperature input'
            'nvidia-utils: GPU temperature via nvidia-smi')
source=('bin/omen88d2'
        'omen88d2/__init__.py'
        'omen88d2/ec.py'
        'omen88d2/fakeec.py'
        'omen88d2/control.py'
        'omen88d2/temps.py'
        'omen88d2/config.py'
        'omen88d2/daemon.py'
        'omen88d2/calibrate.py'
        'omen88d2/probe.py'
        'omen88d2/cli.py'
        'config.example.toml'
        'omen88d2.service'
        'README.md')
md5sums=('SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP'
         'SKIP' 'SKIP' 'SKIP' 'SKIP')

package() {
  install -dm755 "$pkgdir/opt/omen88d2" "$pkgdir/etc/omen88d2"
  cp -r "$srcdir/bin" "$srcdir/omen88d2" "$pkgdir/opt/omen88d2/"
  install -Dm644 "$srcdir/config.example.toml" "$pkgdir/etc/omen88d2/config.toml"
  install -Dm644 "$srcdir/omen88d2.service" "$pkgdir/usr/lib/systemd/system/omen88d2.service"
  install -Dm644 "$srcdir/README.md" "$pkgdir/usr/share/doc/omen88d2/README.md"
  install -dm755 "$pkgdir/usr/local/bin"
  ln -s /opt/omen88d2/bin/omen88d2 "$pkgdir/usr/local/bin/omen88d2"
  install -Dm644 /dev/stdin "$pkgdir/etc/modules-load.d/omen88d2.conf" <<'EOF'
ec_sys
EOF
  install -Dm644 /dev/stdin "$pkgdir/etc/modprobe.d/omen88d2.conf" <<'EOF'
options ec_sys write_support=1
EOF
}
