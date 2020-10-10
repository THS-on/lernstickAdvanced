import argparse
import shutil
import toml
import os
import subprocess
import shlex
from time import sleep
import logging
import datetime
import hashlib
from glob import glob

# TODO add logger

parser = argparse.ArgumentParser()
parser.add_help = True
subparsers = parser.add_subparsers(help="actions", dest="command")

build_parser = subparsers.add_parser("build")
build_parser.add_argument("config")


def merge(a, b):
    """
    Deep merges b into a.
    If a and b have the same key and the value is not a dict the value of b is used.
    :param a:
    :param b:
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


def call_cmd(command, options=None, logging_level=logging.INFO):
    command = [command]
    if options:
        command.extend(options)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    for line in iter(process.stdout.readline, b''):
        # TODO: replace this with something more robust
        logging.log(logging_level, line.decode().replace('\n', ''))

    process.wait()
    if process.returncode != 0:
        logging.error(f"Command {command} exited with {process.returncode}")
    return process.returncode == 0


def call_lb(command, options=None):
    new_options = [command]
    if options:
        new_options.extend(options)
    return call_cmd("lb", new_options)


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
    call_lb("clean")


# TODO fix this mess
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


# Maps everything to a string
def val_to_argument(val):
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


def build_lb_config(config, date):
    """
    Configures lb config with the specified options
    :param config: merged config
    :return: True if it was successful
    """
    options = []

    lb_config = config.get("lb-config", {})
    apt_config = config.get("apt-mirror", {})
    normal_config = config.get("config", {})

    if not lb_config:
        logging.error("No config options for lb config exists!")
        return False

    try:
        if lb_config.get("iso-volume", None) == "auto":
            lb_config["iso-volume"] = f'{normal_config["prefix"]}-{normal_config["version-name"]} {date}'
    except KeyError as err:
        logging.error("prefix or version-name is not specified")
        logging.error(err)
        return False

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
        return False

    return call_lb("config", options)


def build_lb_build():
    logging.info("Calling lb build...")
    return call_lb("build")


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
        return False

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
        logging.info(f"Move files to  {build_path}")
        for file in glob(f'{prefix}*'):
            new_file_path = os.path.join(build_path, os.path.basename(file))
            logging.info(f"Move {file} to {new_file_path}")
            shutil.move(file, new_file_path)



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


def build(config_path):
    today = datetime.date.today()
    config = get_config(config_path)
    start_time = datetime.datetime.now()
    logging.info(f"Starting build at: {start_time}")
    build_clean()
    build_handle_excludes(config)
    build_bootloader_update(today)
    build_lb_config(config, today)
    # init_tmpfs()
    build_lb_build()
    build_finish_build()
    end_time = datetime.datetime.now()
    logging.info(f"Start: {start_time}")
    logging.info(f"End: {end_time}")
    return


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
        return False

    if not os.path.ismount(path):
        logging.error(f"No tmpfs is mounted on {path}")
        return False

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
            return False
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
    # TODO: this should not be necessary after redesign
    os.symlink(os.path.join(pwd, "templates"), os.path.join(image_mount, "templates"))

    return True


def main():
    ARGS = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if ARGS.command == "build":
        build(ARGS.config)
    else:
        parser.print_help()


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    main()
    exit(0)
