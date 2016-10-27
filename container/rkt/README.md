# Build Lernstick with rkt 

# Requirements
 - rkt and acbuild is installed
 - debootstrap is installed

# Customize Lernstick
Fork the repository in your own and make your modifications. 
Change the repository in `image.sh` to your own.
 
# Create Image
Run `image.sh` to generate an custom rkt image to build the lernstick.  
This gives you a `lernstick.aci` to run 

# Run Image
We need an "privileged" container to build the lernstick because chroot does not work without root privileges.
rkt provides a fly stage1 for that which is essentally a chroot for rkt. </br>
Replace `/path/to/build/dir` with your own directory.

Run `sudo rkt run --insecure-options=all --stage1-name=coreos.com/rkt/stage1-fly:1.17.0 --net=host --volume build,kind=host,source=/path/to/build/dir lernstick.aci`

