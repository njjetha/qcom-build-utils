import os
import random
import shutil
import argparse
from build_kernel import build_kernel, reorganize_kernel_debs
from build_dtb import build_dtb
from build_deb import PackageBuilder
from constants import *
from datetime import date
from helpers import create_new_directory, umount_dir, check_if_root, check_and_append_line_in_file, cleanup_file, logger, cleanup_directory, change_folder_perm_read_write, print_build_logs, start_local_apt_server, build_deb_package_gz, mount_img
from deb_organize import generate_manifest_map
from pack_deb import PackagePacker

def parse_arguments():
    parser = argparse.ArgumentParser(description="Process command line arguments.")

    parser.add_argument('--apt-server-config', type=str, required=False, default="deb [arch=arm64 trusted=yes] http://pkg.qualcomm.com noble/stable main",
                        help='APT Server configuration to use')
    parser.add_argument('--mount_dir', type=str, required=False,
                        help='Mount directoryfor builds (default: <workspace>/build)')
    parser.add_argument('--workspace', type=str, required=True,
                        help='Workspace directory (mandatory)')
    parser.add_argument('--build-kernel', action='store_true', default=False,
                        help='Build kernel')
    parser.add_argument('--kernel-src-dir', type=str, required=False,
                        help='Kernel directory (default: <workspace>/kernel)')
    parser.add_argument('--kernel-dest-dir', type=str, required=False,
                        help='Kernel out directory (default: <workspace>/debian_packages/oss)')
    parser.add_argument('--flavor', type=str, choices=['server', 'desktop'], default='server',
                        help='Image flavor (only server or desktop, default: server)')
    parser.add_argument('--debians-path', type=str, required=False,
                        help='Directory with debians to install')
    parser.add_argument('--gen-debians', action='store_true', default=False,
                        help='Generate Debian binary (default: False)')
    parser.add_argument('--pack-image', action='store_true', default=False,
                        help='Pack system.img with generated debians (default: False)')
    parser.add_argument('--pack-variant', type=str, choices=['base', 'qcom'], default='qcom',
                        help='Pack variant (only base or qcom, default: qcom)')
    parser.add_argument('--output-image-file', type=str, required=False,
                        help='Path for output system.img (default: <workspace>/out/system.img)')
    parser.add_argument('--chroot-name', type=str, required=False,
                        help='chroot name to use')
    parser.add_argument('--package', type=str, required=False,
                        help='Package to build')
    parser.add_argument("--nocleanup", action="store_true",
                        help="Cleanup workspace after build", default=False)

    # Deprecated
    parser.add_argument('--skip-starter-image', action='store_true', default=False,
                        help='Build starter image (deprecated)')
    parser.add_argument('--input-image-file', type=str, required=False,
                        help='Path for input system.img (deprecated)')

    args = parser.parse_args()

    # Absolute path checks
    for path_arg, path_value in {
        '--workspace': args.workspace,
        '--kernel-dest-dir': args.kernel_dest_dir,
        '--debians-path': args.debians_path,
        '--output-image-file': args.output_image_file,
    }.items():
        if path_value and not os.path.isabs(path_value):
            logger.error(f"Error: {path_arg} must be an absolute path.")
            exit(1)

    return args

args = parse_arguments()

WORKSPACE_DIR = args.workspace
IMAGE_TYPE = args.flavor

CHROOT_NAME = args.chroot_name if args.chroot_name else f"ubuntu-{date.today()}-{random.randint(0, 10000)}"

OUT_SYSTEM_IMG = args.output_image_file

BUILD_PACKAGE_NAME = args.package

DEBIAN_INSTALL_DIR = args.debians_path

# Process Flags
IF_BUILD_KERNEL = args.build_kernel
IF_GEN_DEBIANS = args.gen_debians
IF_PACK_IMAGE = args.pack_image
IS_CLEANUP_ENABLED = not args.nocleanup

PACK_VARIANT = args.pack_variant

MOUNT_DIR = args.mount_dir if args.mount_dir else os.path.join(WORKSPACE_DIR, "build")
MOUNT_DIR = os.path.join(MOUNT_DIR, CHROOT_NAME)

KERNEL_DIR = args.kernel_src_dir if args.kernel_src_dir else os.path.join(WORKSPACE_DIR, "kernel")
SOURCES_DIR = os.path.join(WORKSPACE_DIR, "sources")
OUT_DIR = os.path.join(WORKSPACE_DIR, "out")
DEB_OUT_DIR = os.path.join(WORKSPACE_DIR, "debian_packages")

OSS_DEB_OUT_DIR = os.path.join(DEB_OUT_DIR, "oss")
KERNEL_DEB_OUT_DIR = args.kernel_dest_dir if args.kernel_dest_dir else OSS_DEB_OUT_DIR
PROP_DEB_OUT_DIR = os.path.join(DEB_OUT_DIR, "prop")
TEMP_DIR = os.path.join(DEB_OUT_DIR, "temp")

if not check_if_root():
    logger.error('Please run this script as root user.')
    exit(1)

create_new_directory(WORKSPACE_DIR, delete_if_exists=False)
create_new_directory(MOUNT_DIR, delete_if_exists=False)
create_new_directory(KERNEL_DIR, delete_if_exists=False)
create_new_directory(KERNEL_DEB_OUT_DIR, delete_if_exists=False)
create_new_directory(SOURCES_DIR, delete_if_exists=False)
create_new_directory(OUT_DIR, delete_if_exists=False)
create_new_directory(DEB_OUT_DIR, delete_if_exists=False)
create_new_directory(OSS_DEB_OUT_DIR, delete_if_exists=False)
create_new_directory(PROP_DEB_OUT_DIR, delete_if_exists=False)
create_new_directory(TEMP_DIR, delete_if_exists=True)

APT_SERVER_CONFIG = [config.strip() for config in args.apt_server_config.split(',')] if args.apt_server_config else None
MANIFEST_MAP = generate_manifest_map(WORKSPACE_DIR)
APT_SERVER_CONFIG = list(set(APT_SERVER_CONFIG)) if APT_SERVER_CONFIG else None

ERROR_EXIT_BUILD = False

if IF_BUILD_KERNEL:
    try:
        os.chdir(WORKSPACE_DIR)
        build_kernel(KERNEL_DIR)
        reorganize_kernel_debs(WORKSPACE_DIR, KERNEL_DEB_OUT_DIR)

        build_dtb(KERNEL_DEB_OUT_DIR, LINUX_MODULES_DEB, COMBINED_DTB_FILE, OUT_DIR)
    except Exception as e:
        logger.error(e)
        ERROR_EXIT_BUILD = True

if ERROR_EXIT_BUILD:
    exit(1)

if IF_GEN_DEBIANS:
    builder = None

    try:
        DEB_OUT_DIR_APT = None
        DEBIAN_INSTALL_DIR_APT = None

        if DEB_OUT_DIR and os.path.exists(DEB_OUT_DIR):
            DEB_OUT_DIR_APT = build_deb_package_gz(DEB_OUT_DIR, start_server=True)
        if DEBIAN_INSTALL_DIR and os.path.exists(DEBIAN_INSTALL_DIR):
            DEBIAN_INSTALL_DIR_APT = build_deb_package_gz(DEBIAN_INSTALL_DIR, start_server=True)

        builder = PackageBuilder(MOUNT_DIR, SOURCES_DIR, APT_SERVER_CONFIG, CHROOT_NAME, MANIFEST_MAP, TEMP_DIR, DEB_OUT_DIR, DEB_OUT_DIR_APT, DEBIAN_INSTALL_DIR, DEBIAN_INSTALL_DIR_APT, IS_CLEANUP_ENABLED)
        builder.load_packages()
        if BUILD_PACKAGE_NAME:
            # TODO: Check if package is available
            can_build = builder.build_specific_package(BUILD_PACKAGE_NAME)
            if not can_build:
                raise Exception(f"Unable to build {BUILD_PACKAGE_NAME}")
        else:
            builder.build_all_packages()

    except Exception as e:
        logger.error(e)
        print_build_logs(TEMP_DIR)
        ERROR_EXIT_BUILD = True

    finally:
        if ERROR_EXIT_BUILD:
            exit(1)

if OUT_SYSTEM_IMG is None:
    OUT_SYSTEM_IMG = os.path.join(OUT_DIR, IMAGE_NAME)

if IF_PACK_IMAGE:
    packer = None
    cleanup_file(OUT_SYSTEM_IMG)
    create_new_directory(MOUNT_DIR)
    try:
        build_dtb(KERNEL_DEB_OUT_DIR, LINUX_MODULES_DEB, COMBINED_DTB_FILE, OUT_DIR)
        packer = PackagePacker(MOUNT_DIR, IMAGE_TYPE, PACK_VARIANT, OUT_DIR, OUT_SYSTEM_IMG, APT_SERVER_CONFIG, TEMP_DIR, DEB_OUT_DIR, DEBIAN_INSTALL_DIR, IS_CLEANUP_ENABLED)

        packer.build_image()
    except Exception as e:
        logger.error(e)
        print_build_logs(TEMP_DIR)
        ERROR_EXIT_BUILD = True
        umount_dir(MOUNT_DIR, UMOUNT_HOST_FS=True)

    finally:
        if IS_CLEANUP_ENABLED:
            cleanup_directory(MOUNT_DIR)
        if ERROR_EXIT_BUILD:
            exit(1)

if IS_CLEANUP_ENABLED:
    try:
        change_folder_perm_read_write(OSS_DEB_OUT_DIR)
        change_folder_perm_read_write(PROP_DEB_OUT_DIR)
        change_folder_perm_read_write(DEB_OUT_DIR)
        change_folder_perm_read_write(OUT_DIR)
    except Exception:
        ERROR_EXIT_BUILD = True

if ERROR_EXIT_BUILD:
    exit(1)
