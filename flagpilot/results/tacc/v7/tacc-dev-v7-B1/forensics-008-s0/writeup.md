# FlagPilot Solution Writeup

## Run

```text
Task: forensics-008
Category: Forensics
Model: qwen3.8-27b
Run: forensics-008-s0
Verdict: verified
Reason: match
```

The verdict below is the recorded outcome. Model reasoning is a self-report; tool and replay outputs are recorded execution evidence.

## Challenge

```text
Files can always be changed in a secret way. Can you find the flag?

Files provided (in ./challenge/):
  - cat.jpg
  - exiftool_download_url.txt
```

## Recorded Steps

### Model call 1

Model output (self-reported):

```json
{"thought": "Let me start by inspecting the files in the challenge directory.", "action": "shell", "cmd": "ls -la ./challenge/ && file ./challenge/cat.jpg && cat ./challenge/exiftool_download_url.txt"}
```

### Tool: shell

```text
Command: ls -la ./challenge/ && file ./challenge/cat.jpg && cat ./challenge/exiftool_download_url.txt
Exit code: 127
Timed out: False
Duration: 536 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/ && file ./challenge/cat.jpg && cat ./challenge/exiftool_download_url.txt
exit=127  time=0.54s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 22 00:54 .
drwxrwxr-x 4 root root   4096 Nov 22 00:54 ..
-r--r--r-- 1 root root 878136 Nov 16 14:02 cat.jpg
-r--r--r-- 1 root root     48 Nov 16 14:02 exiftool_download_url.txt

[stderr]
/bin/sh: 1: file: not found

```

Recorded stderr excerpt:

```text
/bin/sh: 1: file: not found

```

### Model call 2

Model output (self-reported):

```json
{"thought": "Let me check the exiftool download URL and try to use exiftool or other tools to inspect the JPEG file.", "action": "shell", "cmd": "cat ./challenge/exiftool_download_url.txt && which exiftool && which strings && which python3"}
```

### Tool: shell

```text
Command: cat ./challenge/exiftool_download_url.txt && which exiftool && which strings && which python3
Exit code: 0
Timed out: False
Duration: 549 ms
```

Recorded observation excerpt:

```text
[step 2] shell: cat ./challenge/exiftool_download_url.txt && which exiftool && which strings && which python3
exit=0  time=0.55s
--- output ---
https://exiftool.org/Image-ExifTool-12.64.tar.gz/mnt/home/yliutz/WORKDIR/flagpilot-client/bin/exiftool
/usr/bin/strings
/tmp/csit6000v-qwen-20716-30505.FO0mr8/fp-bin/python3

```

### Model call 3

Model output (self-reported):

```json
{"thought": "exiftool is available. Let me use it to inspect the JPEG file for hidden data.", "action": "shell", "cmd": "exiftool ./challenge/cat.jpg"}
```

### Tool: shell

```text
Command: exiftool ./challenge/cat.jpg
Exit code: 0
Timed out: False
Duration: 550 ms
```

Recorded observation excerpt:

```text
[step 3] shell: exiftool ./challenge/cat.jpg
exit=0  time=0.55s
--- output ---
ExifTool Version Number         : FlagPilot shim 1.0 (subset: EXIF/XMP/PNG/GIF/PDF text tags)
File Name                       : cat.jpg
Directory                       : ./challenge
File Size                       : 878 kB
File Type                       : JPEG
File Type Extension             : jpg
MIME Type                       : image/jpeg
JFIF Version                    : 1.02
Rights                          : PicoCTF
Image Width                     : 2560
Image Height                    : 1598
Bits Per Sample  
```

### Model call 4

Model output (self-reported):

```json
{"thought": "The exiftool output doesn't show obvious hidden data. Let me try with more verbose options and also check for steganography or appended data.", "action": "shell", "cmd": "exiftool -a -G1 -s ./challenge/cat.jpg 2>&1 | head -100"}
```

### Tool: shell

```text
Command: exiftool -a -G1 -s ./challenge/cat.jpg 2>&1 | head -100
Exit code: 0
Timed out: False
Duration: 638 ms
```

Recorded observation excerpt:

```text
[step 4] shell: exiftool -a -G1 -s ./challenge/cat.jpg 2>&1 | head -100
exit=0  time=0.64s
--- output ---
ExifToolVersionNumber           : FlagPilot shim 1.0 (subset: EXIF/XMP/PNG/GIF/PDF text tags)
FileName                        : cat.jpg
Directory                       : ./challenge
FileSize                        : 878 kB
FileType                        : JPEG
FileTypeExtension               : jpg
MIMEType                        : image/jpeg
JFIFVersion                     : 1.02
Rights                          : PicoCTF
ImageWidth                      : 2560
ImageHeight                  
```

### Model call 5

Model output (self-reported):

```json
{"thought": "The exiftool shim is limited. Let me use strings to look for hidden data in the file, and also check if there's data appended after the JPEG EOF marker.", "action": "shell", "cmd": "strings ./challenge/cat.jpg | grep -i 'pico\\|flag\\|ctf' && echo '---' && xxd ./challenge/cat.jpg | tail -20"}
```

### Tool: shell

```text
Command: strings ./challenge/cat.jpg | grep -i 'pico\|flag\|ctf' && echo '---' && xxd ./challenge/cat.jpg | tail -20
Exit code: 0
Timed out: False
Duration: 1249 ms
```

Recorded observation excerpt:

```text
[step 5] shell: strings ./challenge/cat.jpg | grep -i 'pico\|flag\|ctf' && echo '---' && xxd ./challenge/cat.jpg | tail -20
exit=0  time=1.25s
--- output ---
PicoCTF
    <rdf:li xml:lang='x-default'>PicoCTF</rdf:li>
---
000d6500: a98e 7f6e 68bf fc6f ff00 297f fd4c 5c2d  ...nh..o..)..L\-
000d6510: 32c0 5d82 ed22 00e3 8a2f ff00 1bff 00ca  2.]..".../......
000d6520: 5fff 0053 1cfe dcd1 7ff8 dffe 52ff 00fa  _..S........R...
000d6530: 9892 6993 c5c8 295c 417f 6de8 bffc 67ff  ..i...)\A.m...g.
000d6540: 0029 7ffd 4c73 fb6f 45ff 00e3 7ff9 4bfe  .)..Ls.oE.....K.
000d6550: 9124 de4e 15c1 73c4 21c6 d453 
```

### Model call 6

Model output (self-reported):

```json
{"thought": "The file ends with ffd9 which is the JPEG EOF marker, so no data appended after. Let me look for XMP metadata or other hidden data. The strings output showed XMP with PicoCTF. Let me extract the XMP data more carefully.", "action": "shell", "cmd": "strings ./challenge/cat.jpg | grep -A5 -B5 'PicoCTF'"}
```

### Tool: shell

```text
Command: strings ./challenge/cat.jpg | grep -A5 -B5 'PicoCTF'
Exit code: 0
Timed out: False
Duration: 425 ms
```

Recorded observation excerpt:

```text
[step 6] shell: strings ./challenge/cat.jpg | grep -A5 -B5 'PicoCTF'
exit=0  time=0.42s
--- output ---
JFIF
0Photoshop 3.0
8BIM
PicoCTF
http://ns.adobe.com/xap/1.0/
<?xpacket begin='
' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/' x:xmptk='Image::ExifTool 10.80'>
<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>
--
 </rdf:Description>
 <rdf:Description rdf:about=''
  xmlns:dc='http://purl.org/dc/elements/1.1/'>
  <dc:rights>
   <rdf:Alt>
    <rdf:li xml:lang='x-default'>PicoCTF</rdf:li>
   </rdf:Alt>
  </dc:rights>
 </rdf:Description>
</rdf:RDF>
</x:xmpmet
```

### Model call 7

Model output (self-reported):

```json
{"thought": "The XMP data only shows PicoCTF as rights. Let me look for the full XMP block and check if there's any hidden data in the EXIF segments. Let me also check for steganography using Python.", "action": "python", "code": "import struct\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find all JPEG markers\ni = 0\nwhile i < len(data) - 1:\n    if data[i] == 0xFF and data[i+1] not in (0x00, 0xFF):\n        marker = data[i+1]\n        if marker in (0xD8, 0xD9):\n            print(f\"Marker 0xFF{marker:02X} at offset {i}\")\n        elif marker >= 0xC0 and marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):\n            length = struct.unpack('>H', data[i+2:i+4])[0]\n            print(f\"Marker 0xFF{marker:02X} at offset {i}, length {length}\")\n        elif marker == 0xE1:  # EXIF\n            length = struct.unpack('>H', data[i+2:i+4])[0]\n            print(f\"EXIF marker at offset {i}, length {length}\")\n            exif_data = data[i+4:i+2+length]\n            print(f\"EXIF data: {exif_data[:100]}\")\n        elif marker == 0xE2:  # Photoshop\n            length = struct.unpack('>H', data[i+2:i+4])[0]\n            print(f\"Photoshop marker at offset {i}, length {length}\")\n            ps_data = data[i+4:i+2+length]\n            print(f\"Photoshop data first 200 bytes: {ps_data[:200]}\")\n        elif marker == 0xED:  # ICC\n            length = struct.unpack('>H', data[i+2:i+4])[0]\n            print(f\"ICC marker at offset {i}, length {length}\")\n        elif marker == 0xEE:  # IPTC\n            length = struct.unpack('>H', data[i+2:i+4])[0]\n            print(f\"IPTC marker at offset {i}, length {length}\")\n        elif marker == 0xDB:  # Quantization\n            length = struct.unpack('>H', data[i+2:i+4])[0]\n            print(f\"Quantization table at offset {i}, length {length}\")\n        elif marker == 0xC4:  # Huffman\n            length = struct.unpack('>H', data[i+2:i+4])[0]\n            print(f\"Huffman table at offset {i}, length {length}\")\n        i += 2\n    else:\n        i += 1\n"}
```

### Tool: python

```text
Command: 
Exit code: 0
Timed out: False
Duration: 720 ms
```

Recorded observation excerpt:

```text
[step 7] python
exit=0  time=0.72s
--- output ---
Marker 0xFFD8 at offset 0
ICC marker at offset 20, length 48
EXIF marker at offset 70, length 3065
EXIF data: b"http://ns.adobe.com/xap/1.0/\x00<?xpacket begin='\xef\xbb\xbf' id='W5M0MpCehiHzreSzNTczkc9d'?>\n<x:xmpmeta xmlns:"
Quantization table at offset 3137, length 132
Marker 0xFFC0 at offset 3271, length 17
Huffman table at offset 3290, length 204
Marker 0xFFD9 at offset 878134

```

### Model call 8

Model output (self-reported):

```json
{"thought": "The EXIF data is actually XMP data. Let me look more carefully at the full EXIF/XMP block and also check the ICC profile. The ICC profile is only 48 bytes which is suspiciously small. Let me examine it.", "action": "python", "code": "with open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# ICC profile at offset 20, length 48\nicc = data[22:22+48]  # skip FF ED and length\nprint(f\"ICC data ({len(icc)} bytes):\")\nprint(icc.hex())\nprint(repr(icc))\n\n# Let me also look at the full XMP/EXIF block\nexif = data[72:72+3065]\nprint(f\"\\nFull XMP block:\")\nprint(exif.decode('utf-8', errors='replace'))"}
```

### Tool: python

```text
Command: 
Exit code: 0
Timed out: False
Duration: 375 ms
```

Recorded observation excerpt:

```text
[step 8] python
exit=0  time=0.38s
--- output ---
ICC data (48 bytes):
003050686f746f73686f7020332e30003842494d04040000000000131c027400075069636f4354461c02000002000400
b'\x000Photoshop 3.0\x008BIM\x04\x04\x00\x00\x00\x00\x00\x13\x1c\x02t\x00\x07PicoCTF\x1c\x02\x00\x00\x02\x00\x04\x00'

Full XMP block:
�http://ns.adobe.com/xap/1.0/ <?xpacket begin='﻿' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/' x:xmptk='Image::ExifTool 10.80'>
<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>

 <rdf:Description rdf:about=''
  xmlns:cc='http://creativecommons.org/ns#'>
  
```

### Model call 9

Model output (self-reported):

```json
{"thought": "I found it! The XMP data contains a base64-encoded license URL: `cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9`. Let me decode it.", "action": "python", "code": "import base64\nencoded = \"cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9\"\ndecoded = base64.b64decode(encoded).decode('utf-8')\nprint(decoded)"}
```

### Tool: python

```text
Command: 
Exit code: 0
Timed out: False
Duration: 541 ms
```

Recorded observation excerpt:

```text
[step 9] python
exit=0  time=0.54s
--- output ---
picoCTF{the_m3tadata_1s_modified}

```

### Model call 10

Model output (self-reported):

```json
{"thought": "I found the flag by decoding the base64-encoded license URL in the XMP metadata of the JPEG file.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\nencoded = \"cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9\"\nprint(base64.b64decode(encoded).decode('utf-8'))"}
```

### Model call 11

Model output (self-reported):

```json
{"thought": "The script needs to actually read the file and extract the base64 string from it, not hardcode it. Let me write a script that reads the JPEG file, finds the XMP metadata, extracts the base64-encoded license URL, and decodes it.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\nimport re\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the XMP block in the EXIF segment\n# Look for the license rdf:resource attribute which contains base64\nmatch = re.search(rb'cc:license rdf:resource=\\'([A-Za-z0-9+/=]+)\\'', data)\nif match:\n    encoded = match.group(1).decode('utf-8')\n    flag = base64.b64decode(encoded).decode('utf-8')\n    print(flag)\nelse:\n    # Fallback: search for base64-like strings in XMP\n    xmp_match = re.search(rb'<x:xmpmeta.*?</x:xmpmeta>', data, re.DOTALL)\n    if xmp_match:\n        xmp = xmp_match.group(0)\n        b64_match = re.search(rb'rdf:resource=\\'([A-Za-z0-9+/=]+)\\'', xmp)\n        if b64_match:\n            encoded = b64_match.group(1).decode('utf-8')\n            flag = base64.b64decode(encoded).decode('utf-8')\n            print(flag)"}
```

## Submission

Submission attempts: 2

Submitted flag:

```text
picoCTF{the_m3tadata_1s_modified}
```

Submitted solve.py:

```python
import base64
import re

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the XMP block in the EXIF segment
# Look for the license rdf:resource attribute which contains base64
match = re.search(rb'cc:license rdf:resource=\'([A-Za-z0-9+/=]+)\'', data)
if match:
    encoded = match.group(1).decode('utf-8')
    flag = base64.b64decode(encoded).decode('utf-8')
    print(flag)
else:
    # Fallback: search for base64-like strings in XMP
    xmp_match = re.search(rb'<x:xmpmeta.*?</x:xmpmeta>', data, re.DOTALL)
    if xmp_match:
        xmp = xmp_match.group(0)
        b64_match = re.search(rb'rdf:resource=\'([A-Za-z0-9+/=]+)\'', xmp)
        if b64_match:
            encoded = b64_match.group(1).decode('utf-8')
            flag = base64.b64decode(encoded).decode('utf-8')
            print(flag)
```

## Replay Verification

```text
Verdict: verified
Reason: match
Gold checked: True
Exit code: 0
Duration: 835 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 11
Wall time: 63.62 s
Input tokens: 26857
Output tokens: 1898
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
