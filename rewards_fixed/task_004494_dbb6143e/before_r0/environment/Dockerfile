FROM hamishi740/swerl-tmax-v3:763c9648f9c0
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
# apt-get update && apt-get install -y python3 python3-pip tesseract-ocr
# pip3 install pytest numpy scipy flask fastapi uvicorn pillow pytesseract requests
#
# mkdir -p /app
# cat << 'EOF' > /tmp/gen_img.py
# from PIL import Image, ImageDraw
# img = Image.new('RGB', (800, 200), color = (255, 255, 255))
# d = ImageDraw.Draw(img)
# d.text((10,50), "Use mesh dimension N=40. Apply Tikhonov regularization lambda=0.005.", fill=(0,0,0))
# img.save('/app/spec.png')
# EOF
# python3 /tmp/gen_img.py
# rm /tmp/gen_img.py
#
# useradd -m -s /bin/bash user || true
# chmod -R 777 /home/user
