import random
import sys

from PIL import Image


width, height = 16, 8
message = b"FLAG=ST3G4N0GR4PHY_2024\0"
bits = [int(bit) for byte in message for bit in f"{byte:08b}"]
random_source = random.Random(2024)
pixels = []
bit_index = 0

for y in range(height):
    for x in range(width):
        channels = [
            (x * 13 + y * 7 + random_source.randrange(32)) % 256,
            (x * 5 + y * 17 + random_source.randrange(32)) % 256,
            (x * 11 + y * 3 + random_source.randrange(32)) % 256,
        ]
        if (x + y) % 2 == 0:
            for channel in range(3):
                channels[channel] = (channels[channel] & 0xFE) | bits[bit_index]
                bit_index += 1
        pixels.append((*channels, 255))

image = Image.new("RGBA", (width, height))
image.putdata(pixels)
image.save(sys.argv[1], format="PNG", optimize=False)
