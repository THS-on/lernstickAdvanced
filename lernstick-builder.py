import argparse
import shutil
import toml
import os
import subprocess
import shlex
import sys
import logging
import datetime
import hashlib
from glob import glob
from time import sleep

parser = argparse.ArgumentParser()
parser.add_help = True
subparsers = parser.add_subparsers(help="actions", dest="command")

build_parser = subparsers.add_parser("build")
build_parser.add_argument("config")

# TODO remove this
logging.basicConfig(level=logging.DEBUG)


def build(config_path):
    today = datetime.date.today()
    config = get_config(config_path)
    start_time = datetime.datetime.now()
    logging.info(f"Starting build at: {start_time}")
    try:
        build_clean()
        build_handle_excludes(config)
        build_bootloader_update(today)
        build_lb_config(config, today)
        # init_tmpfs()
        build_lb_build()
        check_b43legacy()
        build_finish_build(config, today)
    except BuildFailure as err:
        logging.error("Something went wrong during building. Exiting...")
        return False

    end_time = datetime.datetime.now()
    logging.info(f"Start: {start_time}")
    logging.info(f"End: {end_time}")
    return True


def build_handle_excludes(config):
    excludes_config = config.get("excludes", None)

    if not excludes_config:
        logging.info("No excludes configured")
        return

    hooks_template_path = "config/hooks.template"
    hooks_path = "config/hooks"
    package_lists_template_path = "config/package-lists.template"
    package_lists_path = "config/package-lists"

    if os.path.isdir(hooks_path):
        logging.info("Remove old hooks directory")
        shutil.rmtree(hooks_path)
    if os.path.isdir(package_lists_path):
        logging.info("Remove old package-list directory")
        shutil.rmtree(package_lists_path)

    logging.info(f"Copying hook template to {hooks_path}")
    shutil.copytree(hooks_template_path, hooks_path)
    logging.info(f"Copying package-list template to {package_lists_path}")
    shutil.copytree(package_lists_template_path, package_lists_path)

    if "hooks" in excludes_config:
        for hook in excludes_config["hooks"]:
            file = os.path.join(hooks_path, "live", hook)
            logging.info(f"Removing hook {file}")
            os.remove(file)

    if "package-lists" in excludes_config:
        for package_list in excludes_config["package-lists"]:
            file = os.path.join(package_lists_path, package_list)
            logging.info(f"Removing package list {file}")
            os.remove(file)


def build_clean():
    paths = ["config/binary",
             "config/bootstrap",
             "config/build",
             "config/chroot",
             "config/common",
             "config/source"]
    logging.info("Start cleaning files...")
    for path in paths:
        if os.path.isdir(path):
            logging.info(f"Removing directory {path}")
            shutil.rmtree(path)
        elif os.path.isfile(path):
            logging.info(f"Removing file {path}")
            os.remove(path)
    if not call_lb("clean"):
        raise BuildFailure("lb clean failed")


def build_bootloader_update(today):
    # ISOLINUX/SYSLINUX
    bootlogo = "config/bootloaders/isolinux/bootlogo"
    bootlogo_dir = f"{bootlogo}.dir"
    grub_theme_dir = "config/includes.binary/boot/grub/themes/lernstick"

    logging.info(f"Update date in gfxboot to {today}")
    shutil.copy2("templates/xmlboot.config", bootlogo_dir)
    call_cmd("sed", shlex.split(
        f'-i "s|<version its:translate=\"no\">.*</version>|<version its:translate=\"no\">(Version {today})</version>|1"'
        f' {bootlogo_dir}/xmlboot.config'))

    logging.info("Calling gfxboot...")
    call_cmd("gfxboot", shlex.split(f"--archive {bootlogo_dir} --pack-archive {bootlogo}"))
    shutil.copy2(bootlogo, f"{bootlogo}.orig")  # TODO Does this make sense after or before calling gfxboot

    # Update date in GRUB
    logging.info(f"Update GRUB date to {today}")
    shutil.copy2("templates/theme.txt", f"{grub_theme_dir}/theme.txt")
    call_cmd("sed", ["-i",
                     f's|title-text.*|title-text: "Lernstick Debian 10 (Version {today})"|1',
                     f'{grub_theme_dir}/theme.txt'])


# TODO test this function
def cache_cleanup():
    logging.info("removing deprecated packages from cache")
    for dir in glob("cache/packages.*"):
        logging.info(f"checking directory {dir}")
        for deb_file in os.listdir(dir):
            deb_path = os.path.join(dir, deb_file)
            package_name = get_package_name(deb_path)
            versions = glob(f"{dir}/{package_name}_*")
            package_version = get_package_version(deb_path)
            logging.debug(f'File: {deb_file}, Package name: {package_name}, Version {package_version} ')

            if len(versions) == 1:  # Skip if we only have one version of a package
                continue

            for version in versions:
                other_package_version = get_package_version(os.path.join(dir, version))
                if call_cmd("dpkg" ["--compare-versions", package_version, "lt", other_package_version]):
                    logging.info(f"removing deprecated cache file {deb_file} (newer version {other_package_version} found)")
                    os.remove(deb_path)
                    break


def build_lb_config(config, date):
    """
    Configures lb config with the specified options.
    We assume that the options in [lb-config] have the same name as the parameters for lb config.
    Arguments that don't take an extra value can be enabled by setting them to true.
    :param config: merged config
    :return: True if it was successful
    """
    options = []

    lb_config = config.get("lb-config", {})
    apt_config = config.get("apt-mirror", {})
    normal_config = config.get("config", {})

    if not lb_config:
        logging.error("No config options for lb config exists!")
        raise BuildFailure("No config options for lb config exists!")

    try:
        if lb_config.get("iso-volume", None) == "auto":
            lb_config["iso-volume"] = f'{normal_config["prefix"]}-{normal_config["version-name"]} {date}'
    except KeyError as err:
        logging.error("prefix or version-name is not specified")
        logging.error(err)
        raise BuildFailure("Cannot create iso-volume name")

    # Add arguments that don't take a value
    single_arguments = {"breakpoints", "clean", "color", "debug", "dump", "force", "no-color", "quiet", "validate",
                        "verbose"}
    for arg in single_arguments:
        add_if_true(arg, lb_config, options, f"--{arg}")

    # Add normal lb config options
    for key, val in {k: v for (k, v) in lb_config.items() if k not in single_arguments}.items():
        options.append(f'--{key}')
        options.append(val_to_argument(val))

    try:
        options += [
            "--mirror-binary", apt_config["system"],
            "--mirror-binary-security", apt_config["system-security"],
            "--mirror-bootstrap", apt_config["build"]
        ]

        # Optional arguments
        add_if_exists("build-proxy", apt_config, options, before="--apt-http-proxy")

    except KeyError as err:
        logging.error("Mirror options not complete")
        logging.error(err)
        raise BuildFailure("Mirror options not complete")

    if not call_lb("config", options):
        raise BuildFailure("lb config failed")


def build_lb_build():
    logging.info("Calling lb build...")
    if not call_lb("build"):
        raise BuildFailure("lb build failed")


def build_finish_build(config, today):
    iso_file = "live-image-amd64.iso"

    if not os.path.isfile(iso_file):
        logging.error("ISO file was not created!")
        return False

    try:
        prefix = f"{config['config']['prefix']}_{config['config']['version-name']}_{today}"
    except KeyError as err:
        logging.error("Couldn't create prefix for iso. Please specify a prefix and a version-name in config")
        logging.error(err)
        raise BuildFailure("Couldn't create prefix for iso. Please specify a prefix and a version-name in config")

    image = f"{prefix}.iso"
    shutil.move(iso_file, image)
    # we must update the zsync file because we renamed the iso file
    os.remove(f'{iso_file}.zsync')
    logging.info("Updating zsync file...")
    call_cmd("zsyncmake", ["-C", image, "-u", image])
    logging.info("Create md5 hash for iso...")
    with open(f'{image}.md5', 'w+') as f:
        f.write(md5sum_file(image).hexdigest())

    if "lb-config" in config and config["lb-config"].get("source", False):
        # Debian live sources
        shutil.move("live-image-source.live.tar", f'{prefix}-source.live.tar')

        debian_tar = f'{prefix}-source.debian.tar'
        shutil.move("live-image-source.debian.tar", debian_tar)
        logging.info(f"Create md5 hash for {debian_tar}...")
        with open(f'{debian_tar}.md5', 'w+'):
            f.write(md5sum_file(debian_tar).hexdigest())

    # TODO decuple this step from tmpfs
    if "tmpfs" in config and config["tmpfs"].get("build-path", None):
        build_path = config["tmpfs"]["build-path"]

        if not os.path.exists(build_path):
            logging.info(f"Build directory not found. Creating {build_path}")
            os.makedirs(build_path)

        logging.info(f"Move files to  {build_path}")
        for file in glob(f'{prefix}*'):
            new_file_path = os.path.join(build_path, os.path.basename(file))
            logging.info(f"Move {file} to {new_file_path}")
            shutil.move(file, new_file_path)


def check_b43legacy():
    """
    Checks if firmware-b43legacy-installer installed correctly.
    When installing firmware-b43legacy-installer downloads.openwrt.org is
    sometimes down. Building doesn't fail in this situation but we would
    have produced an image without support for some legacy broadcom cards.
    """

    # This file is created during installation and contains all installed files.
    installed = os.path.isfile("chroot/lib/firmware/b43legacy/firmware-b43legacy-installer.catalog")
    if installed:
        logging.info("firmware-b43legacy-installer seems to be installed correctly")
    else:
        logging.error("firmware-b43legacy-installer didn't install correctly!")
        raise BuildFailure("firmware-b43legacy-installer didn't install correctly!")


# TODO test this code
def init_tmpfs(config):
    """
    Experience has shown that using a file system in RAM speeds up the build
    process between 5 to 10 times in comparison to using a file system on SSDs or
    classical hard drives with spinning rust.
    Unfortunately, tmpfs doesn't support extended attributes but they are needed
    by some tools during installation, e.g. flatpak. Therefore we can't use
    tmpfs mounts directly but must create an additional image inside the tmpfs
    that must be formatted with another file system that supports extended
    attributes (e.g. ext4).
    """

    if "tmpfs" not in config or not config["tmpfs"].get("enabled", False):
        logging.info("Building with tmpfs is disabled")
        return False

    try:
        path = config["tmpfs"]["path"]
        image_path = config["tmpfs"]["image"]["path"]
        image_size = config["tmpfs"]["image"]["size"]
        image_mount = config["tmpfs"]["image"]["mount"]

    except KeyError as err:
        logging.error("tmpfs options couldn't be parsed")
        logging.error(err)
        raise BuildFailure("tmpfs options couldn't be parsed")

    if not os.path.ismount(path):
        logging.error(f"No tmpfs is mounted on {path}")
        raise BuildFailure(f"No tmpfs is mounted on {path}")

    logging.info(f"Found tmpfs mounted on {path}")

    if os.path.ismount(image_mount):
        logging.info(f"found tmpfs image mounted on {image_mount}")
        logging.info(f"killing all processes still accessing {image_mount}")

        call_cmd("fuser", shlex.split(f"-v -k -m '{image_mount}'"))
        logging.debug("Waiting for 3 seconds")
        sleep(3)
        logging.info(f"unmounting {image_mount}")

        if call_cmd("unmount", [image_mount]):
            os.remove(image_path)
        else:
            logging.error(f"unmounting \"{image_mount}\" failed, exiting...")
            raise BuildFailure(f"unmounting \"{image_mount}\" failed, exiting...")
    else:
        logging.info(f"no tmpfs image mounted on \"{image_mount}\" found")

    logging.info(f"creating tmpfs mount point \"${image_mount}\"")
    os.makedirs(image_mount, exist_ok=True)
    logging.info(f"creating new tmpfs image ({image_size} at {image_path})")
    call_cmd("truncate", ["-s", image_size, image_path])
    call_cmd("mkfs.ext4", [image_path])
    logging.info(f"mount tmpfs image to \"{image_mount}\"")
    call_cmd("mount", [image_path, image_mount])

    pwd = os.getcwd()
    shutil.copymode(os.path.join(pwd, "config"), os.path.join(image_mount, "config"))
    os.symlink(os.path.join(pwd, "cache"), os.path.join(image_mount, "cache"))
    os.symlink(os.path.join(pwd, "templates"), os.path.join(image_mount, "templates"))

    return True


def get_config(config_path, loaded=set()):
    """
    :param config_path: path to the config that should be get
    :param loaded: config that already have been loaded
    :return: parsed config
    """
    # TODO: Validate config
    config = None
    try:
        with open(config_path, 'r') as f:
            config = toml.load(f)

        # Recursively merge configs
        if "based-on" in config.get("config", {}):
            loaded.add(config_path)

            lower_config_path = config["config"]["based-on"]
            if lower_config_path in loaded:
                logging.error("Circular includes are not allowed!")
                raise EnvironmentError()
            lower_config = get_config(lower_config_path, loaded)
            if not lower_config:
                raise EnvironmentError()
            config = merge(lower_config, config)

    except EnvironmentError:
        logging.error(f"Could not successfully read config: {config_path}")
        return None
    else:
        logging.debug(f"Config is {config}")
        return config


# Utility functions

def call_cmd(command, options=None, logging_level=logging.INFO, return_output=False):
    command = [command]
    if options:
        command.extend(options)
    # TODO log stderr as error
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    output = []

    for line in iter(process.stdout.readline, b''):
        # TODO: replace this with something more robust
        output.append(line.decode())
        logging.log(logging_level, line.decode().replace('\n', ''))

    process.wait()
    if process.returncode != 0:
        logging.error(f"Command {command} exited with {process.returncode}")

    return "".join(output) if return_output else process.returncode == 0


def call_lb(command, options=None):
    new_options = [command]
    if options:
        new_options.extend(options)
    return call_cmd("lb", new_options)


def md5sum_file(filename):
    """
    Generates a md5 hash for a given file.
    This function reads the file chunk wise.
    :param filename: path to file for hashing
    :return: hash object
    """
    hash = hashlib.md5()
    block_size = hash.block_size * 256
    with open(filename, 'rb') as file:
        for chunk in iter(lambda: file.read(block_size), b''):
            hash.update(chunk)
    return hash


def val_to_argument(val):
    """
    Maps a given value to a string.
    True -> "true", False -> "false", x -> str(x)
    :param val: Value to convert to a string
    :return: string of value
    """
    if val is True:
        return "true"
    if val is False:
        return "false"
    return str(val)


def add_if_true(key, map, list, value, before=None):
    if map.get(key, False):
        if before:
            list.append(before)
        list.append(value)


def add_if_exists(key, map, list, before=None):
    if key in map:
        if before:
            list.append(before)
        list.append(val_to_argument(map[key]))


def get_package_version(package):
    """
    Gets package version of given path to a .deb file using dpkg-deb.
    :param package: path to package
    :return: Package version
    """
    if not os.path.exists(package):
        logging.error(f"Package {package} does not exits")
        raise EnvironmentError
    return call_cmd("dpkg-deb", ["-f", package, "Version"], logging_level=logging.DEBUG, return_output=True)


def get_package_name(package):
    """
    Gets package name of given path to a .deb file using dpkg-deb
    :param package: path to package
    :return: Package Name
    """
    if not os.path.exists(package):
        logging.error(f"Package {package} does not exits")
        raise EnvironmentError
    return call_cmd("dpkg-deb", ["-f", package, "Package"], logging_level=logging.DEBUG, return_output=True)


def merge(a, b):
    """
    Deep merges b into a.
    If a and b have the same key and the value is not a dict the value of b is used.
    :param a: Dict to merge into
    :param b: Dict to merge from
    :return: a
    """
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge(a[key], b[key])
            elif a[key] != b[key]:  # If a and be differ overwrite with the value of b
                a[key] = b[key]
        else:
            a[key] = b[key]
    return a


def setup_logging(debug=False):
    logger = logging.getLogger()
    logfile_name = "lernstick.log"
    if debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)
    logger.addHandler(logging.StreamHandler(sys.stdout))
    logger.addHandler(logging.FileHandler(logfile_name))
    if os.path.exists(logfile_name):
        os.remove(logfile_name)
        logging.info("Removed old log file")
    logging.debug("Logging setup complete.")


class BuildFailure(Exception):
    """
    This exception is thrown if some in the build chain goes wrong, such that the resulting iso is not usable
    """
    pass



# Main
def main():
    args = parser.parse_args()
    setup_logging()
    if args.command == "build":
        build(args.config)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
    exit(0)
