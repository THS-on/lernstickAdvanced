#!/bin/sh

set -e

SOURCE="false"
USE_TMPFS="false"
CONSTANTS="constants"

OPTS=`getopt -o tc: --long tmpfs,constants: -n 'build_dvd.sh' -- "$@"`

if [ $? != 0 ] ; then echo "Failed parsing options." >&2 ; exit 1 ; fi

eval set -- "$OPTS"

while true; do
  case "$1" in
    -c | --constants ) CONSTANTS="$2"; shift; shift ;;
    -t | --tmpfs )    USE_TMPFS="true"; shift ;;
    -- ) shift; break ;;
    * ) break ;;
  esac
done
. ./functions.sh
check_and_source_constants

if [ "$USE_TMPFS" == "true" ]
then
	if [ -d "${TMPFS}" ]
	then
		cd "${TMPFS}"
	else
		echo "The tmpfs directory \"${TMPFS}\" doesn't exist."
		echo "Do you want to run the build without a tmpfs? (y/n)"
		read answer
		if [ "${answer}" != "y" ]
		then
			exit 0
		fi
	fi
fi

init_build
configure
build_image
