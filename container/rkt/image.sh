#!/bin/bash
echo "Debootstrap Debian Jessie"
#Debootstrap Debian Jessie with programs that the build process needs
mkdir rootfs-debian
sudo debootstrap --include=git,uuid-runtime,debootstrap,libxml-parser-perl,gfxboot,zsync stable rootfs-debian/ http://httpredir.debian.org/debian/

echo "Start image creation"
#Create custom rkt image 
sudo acbuild begin ./rootfs-debian
sudo acbuild set-name lernstick/lernstick
sudo acbuild label add version "8"
#Use custom live build version
sudo acbuild run -- wget http://mirror.thson.de/lernstick/pool/main/l/live-build/live-build_5.0~a5-1%2blernstick2_all.deb
sudo acbuild run -- dpkg -i live-build_5.0~a5-1+lernstick2_all.deb
sudo acbuild run -- rm live-build_5.0~a5-1+lernstick2_all.deb
#Using own reposistory for custom constans and fixed build_dvd.sh
sudo acbuild set-exec -- /bin/bash -c "git clone https://github.com/THS-on/lernstickAdvanced /lernstickCI && cd /lernstickCI && git checkout jessie32-ci && ./build_dvd.sh -c ./container/constants.ci"
#build is specified in run command
sudo acbuild mount add build /build
#Create lernstick.aci and overite old versions
sudo acbuild write --overwrite lernstick.aci
sudo acbuild end
echo "lernstick.aci created"

echo "Removing rootfs-debian"
sudo rm -rf ./rootfs-debian
echo "Removed rootfs-debian"
echo "Finished build"
