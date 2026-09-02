#!/bin/sh
# Create the disk image the task partitions.
#
# Same repair as tw_627786's near-twin tw_693888, at a different size. The image
# used to be made by `RUN truncate -s 10G /disk.img`, and a build layer
# materialises it: the sparse file enters the layer tar as 10 GiB of zeros and is
# written out in full, which measured 10818 MB against Daytona's 10 GiB
# per-sandbox cap. The task was dropped from the corpus for it.
#
# Created here instead, at container start, the file stays sparse: the 10G the
# task asks for costs only the blocks fdisk actually writes.
[ -e /disk.img ] || truncate -s 10G /disk.img
exec "$@"
