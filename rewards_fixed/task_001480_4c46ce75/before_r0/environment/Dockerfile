FROM hamishi740/swerl-tmax-v3:cd472ae53ad5
#
# The task environment is BAKED INTO THE IMAGE above. The lines below are this task's
# setup.sh, inlined as comments so a reader -- and the breeding model, which sees only
# instruction.md, this file and solution/solve.sh -- can tell how the environment was built.
# They are COMMENTS on purpose: these scripts are not idempotent and must never run again as
# RUN steps, and a COPY/RUN here would move the image identity the pre-test hook keys on.
# The executable copy ships beside this file as tasks/<id>/setup.sh.
#
# #!/bin/bash
# set -e
# export DEBIAN_FRONTEND=noninteractive
#     apt-get update && apt-get install -y python3 python3-pip sudo imagemagick fonts-dejavu-core tesseract-ocr
#     pip3 install pytest
#
#     mkdir -p /app
#
#     # Create the catalog.csv
#     cat << 'EOF' > /app/catalog.csv
# product_id,product_name
# 101,Widget Pro X
# 102,Blender 500W Black
# 103,Coffee Maker Deluxe
# 104,SuperPhone 128GB
# 105,SuperPhone 64GB
# 106,Widget Standard
# EOF
#
#     # Create the manifest.png using ImageMagick
#     # Allow ImageMagick to read/write images (modify policy.xml if necessary, usually default allows png)
#     convert -size 400x200 xc:white -font DejaVu-Sans -pointsize 20 -fill black -annotate +20+40 "1x Wdget Pro X\n2x Blndr 500w Blck\n1x Cff Makr Dlux\n4x SuperPhone 128GB" /app/manifest.png
#
#     useradd -m -s /bin/bash user || true
#     echo "user ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers
#     chmod -R 777 /home/user
#     chmod -R 777 /app
