# Build Lernstick with Docker

# Requirements
 - Docker is installed

# Customize Lernstick
Fork the repository in your own and make your modifications. 
Change the repository in the `Dockerfile` to your own.
 
# Create Image
Go in the `container/docker` directory and run `sudo docker build -t docker-lernstick .`
This will create an image with the name `docker-lernstick`

# Run Image
Create a directory where the .iso will be stored: `mkdir build` </br>
We need an "privileged" container to build the lernstick because chroot (lb build) does not work without root privileges. </br>
Replace `/path/to/build/dir` with your own directory.

Run `docker run -t -i -v /path/to/your/build/dir:/build:rw docker-lernstick --privileged`

