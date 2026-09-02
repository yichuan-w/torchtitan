#!/bin/sh
# Create the disk image the task partitions.
#
# This used to be `RUN truncate -s 20G /disk.img` in the Dockerfile, and a build
# layer materialises it: the sparse file enters the layer tar as 20 GiB of zeros
# and is written out in full. Measured, the image occupied 21556 MB of a sandbox,
# and Daytona caps a sandbox at 10 GiB, so the task could not run at any sizing.
#
# Created here instead, at container start, the file stays sparse: the 20G the
# task asks for costs only the blocks fdisk actually writes, which are the
# partition table and the extended partition's EBR.
[ -e /disk.img ] || truncate -s 20G /disk.img
exec "$@"
