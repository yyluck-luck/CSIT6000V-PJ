# FlagPilot Solution Writeup

## Run

```text
Task: forensics-008
Category: Forensics
Model: qwen3.8-27b
Run: forensics-008-s1
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
{"thought": "Let me start by examining the files provided in the challenge directory.", "action": "shell", "cmd": "ls -la ./challenge/ && file ./challenge/cat.jpg && cat ./challenge/exiftool_download_url.txt"}
```

### Tool: shell

```text
Command: ls -la ./challenge/ && file ./challenge/cat.jpg && cat ./challenge/exiftool_download_url.txt
Exit code: 127
Timed out: False
Duration: 539 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/ && file ./challenge/cat.jpg && cat ./challenge/exiftool_download_url.txt
exit=127  time=0.54s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 21 05:25 .
drwxrwxr-x 4 root root   4096 Nov 21 05:25 ..
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
{"thought": "Let me check the exiftool URL and examine the JPEG file for hidden data.", "action": "shell", "cmd": "cat ./challenge/exiftool_download_url.txt && xxd ./challenge/cat.jpg | head -50"}
```

### Tool: shell

```text
Command: cat ./challenge/exiftool_download_url.txt && xxd ./challenge/cat.jpg | head -50
Exit code: 0
Timed out: False
Duration: 730 ms
```

Recorded observation excerpt:

```text
[step 2] shell: cat ./challenge/exiftool_download_url.txt && xxd ./challenge/cat.jpg | head -50
exit=0  time=0.73s
--- output ---
https://exiftool.org/Image-ExifTool-12.64.tar.gz00000000: ffd8 ffe0 0010 4a46 4946 0001 0200 0001  ......JFIF......
00000010: 0001 0000 ffed 0030 5068 6f74 6f73 686f  .......0Photosho
00000020: 7020 332e 3000 3842 494d 0404 0000 0000  p 3.0.8BIM......
00000030: 0013 1c02 7400 0750 6963 6f43 5446 1c02  ....t..PicoCTF..
00000040: 0000 0200 0400 ffe1 0bf9 6874 7470 3a2f  ..........http:/
00000050: 2f6e 732e 6164 6f62 652e 636f 6d2f 7861  /ns.adobe.com/xa
00000060: 702f
```

Recorded stderr excerpt:

```text
Exception ignored in: <_io.TextIOWrapper name='<stdout>' mode='w' encoding='utf-8'>
BrokenPipeError: [Errno 32] Broken pipe

```

### Model call 3

Model output (self-reported):

```json
{"thought": "I can see the JPEG has EXIF metadata. I can see 'PicoCTF' in the XMP data. Let me look for more hidden data - the challenge says files can be changed in a secret way. Let me look at the end of the file and check for appended data or steganography.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | tail -50"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | tail -50
Exit code: 0
Timed out: False
Duration: 873 ms
```

Recorded observation excerpt:

```text
[step 3] shell: xxd ./challenge/cat.jpg | tail -50
exit=0  time=0.87s
--- output ---
000d6320: 00b2 3ffc 873f fa98 29ed 370f 1fff 0058  ..?..?..).7....X
000d6330: ff00 f21c ff00 ea62 42d2 65d8 ce2a 0a67  .......bB.e..*.g
000d6340: 55e5 1493 da5e 1fff 00ec 8fff 0021 cffe  U....^.......!..
000d6350: a60b ff00 7938 7bff 00b2 3ffc 973f fa98  ....y8{...?..?..
000d6360: 9269 32ea 6694 7988 e19a 5758 a5ff 00de  .i2.f.y...WX....
000d6370: 4e1e ff00 ec87 ff00 25cf fea6 07fd e561  N.......%......a
000d6380: ff00 fec8 7ff2 5cff 00ea 6249 a4cb 919a  ......\...bI....
000d6390: 5758 2f79 5758 a77f de46 1fff
```

### Model call 4

Model output (self-reported):

```json
{"thought": "The file ends with ffd9 which is the JPEG EOI marker, so no data appended at the end. Let me look more carefully at the EXIF data. I saw 'PicoCTF' in the XMP metadata. Let me extract all the EXIF data and look for the full flag. Let me also check if there's steganography in the image itself.", "action": "shell", "cmd": "strings ./challenge/cat.jpg | grep -i -E 'pico|flag|ctf|{' | head -20"}
```

### Tool: shell

```text
Command: strings ./challenge/cat.jpg | grep -i -E 'pico|flag|ctf|{' | head -20
Exit code: 0
Timed out: False
Duration: 307 ms
```

Recorded observation excerpt:

```text
[step 4] shell: strings ./challenge/cat.jpg | grep -i -E 'pico|flag|ctf|{' | head -20
exit=0  time=0.31s
--- output ---
PicoCTF
    <rdf:li xml:lang='x-default'>PicoCTF</rdf:li>
	{>Uz
#{G3EI
 B{!
nLZbP\{C
{D>o
T={J'
fZa*{y
5[Mm{^,
{tkm,
{XXC
{1e'
\{Ku
JRwQ{
Gr{M@
.{f.
6,1Sb{
{X@D
HZeE{

```

### Model call 5

Model output (self-reported):

```json
{"thought": "The EXIF data only shows 'PicoCTF' without the full flag. The challenge says files can be changed in a secret way - this likely means steganography. Let me try to use Python to check for LSB steganography or other hidden data in the image.", "action": "python", "code": "from PIL import Image\nimport numpy as np\n\nimg = Image.open('./challenge/cat.jpg')\nprint(f\"Image size: {img.size}, mode: {img.mode}\")\nprint(f\"Info: {img.info}\")\n\n# Check for LSB steganography - look at the last bits of pixel values\narr = np.array(img)\nprint(f\"Array shape: {arr.shape}\")\n\n# Check LSB of all channels\nlsb = arr & 1\n# Try to read LSB data from the image\n# Convert to flat array of LSBs\nflat_lsb = lsb.flatten()\n\n# Try to extract bytes from LSBs\nbytes_data = []\nfor i in range(0, len(flat_lsb) - 7, 8):\n    byte = 0\n    for j in range(8):\n        byte = (byte << 1) | flat_lsb[i+j]\n    bytes_data.append(byte)\n\n# Look for printable ASCII in the extracted data\nresult = b''.join(bytes_data[:200])\nprint(\"First 200 bytes of LSB data:\")\nprint(result)\nprint(\"As text:\", result.decode('latin-1', errors='replace')[:200])"}
```

### Tool: python

```text
Command: 
Exit code: 0
Timed out: False
Duration: 5945 ms
```

Recorded observation excerpt:

```text
[step 5] python
exit=0  time=5.95s
--- output ---
Image size: (2560, 1598), mode: RGB
Info: {'jfif': 258, 'jfif_version': (1, 2), 'jfif_unit': 0, 'jfif_density': (1, 1), 'photoshop': {1028: b'\x1c\x02t\x00\x07PicoCTF\x1c\x02\x00\x00\x02\x00\x04'}, 'xmp': b"<?xpacket begin='\xef\xbb\xbf' id='W5M0MpCehiHzreSzNTczkc9d'?>\n<x:xmpmeta xmlns:x='adobe:ns:meta/' x:xmptk='Image::ExifTool 10.80'>\n<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>\n\n <rdf:Description rdf:about=''\n  xmlns:cc='http://creativecommons.org/ns#'>\n  <cc:license rdf:resource='cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW
```

### Model call 6

Model output (self-reported):

```json
{"thought": "I found it! In the XMP metadata there's a base64-encoded string: `cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9` in the cc:license field. Let me decode it.", "action": "decode", "scheme": "base64", "text": "cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9"}
```

### Tool: decode

```text
Command: 
Exit code: 0
Timed out: False
Duration: 0 ms
```

Recorded observation excerpt:

```text
[step 6] decode
exit=0  time=0.00s
--- output ---
picoCTF{the_m3tadata_1s_modified}
```

### Model call 7

Model output (self-reported):

```json
{"thought": "I found the flag by decoding the base64 string hidden in the XMP metadata of the JPEG file. The flag is picoCTF{the_m3tadata_1s_modified}.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\nflag = base64.b64decode('cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9').decode()\nprint(flag)"}
```

### Model call 8

Model output (self-reported):

```json
{"thought": "The script needs to actually read the file and extract the flag from it, not hardcode the base64 string. Let me write a script that reads the JPEG file, extracts the XMP metadata, finds the base64-encoded license URL, and decodes it.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\nimport re\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the XMP metadata section\nxmp_start = data.find(b'<?xpacket')\nxmp_end = data.find(b'<?xpacket end')\nif xmp_start == -1:\n    xmp_end = len(data)\nelse:\n    xmp_end = data.find(b'?>', xmp_end) + 2\n\nxmp_data = data[xmp_start:xmp_end].decode('utf-8', errors='replace')\n\n# Find the base64-encoded license resource\nmatch = re.search(r'rdf:resource=\\'([A-Za-z0-9+/=]+)\\'', xmp_data)\nif match:\n    b64 = match.group(1)\n    flag = base64.b64decode(b64).decode()\n    print(flag)\nelse:\n    # Fallback: search for base64-like strings in the file\n    for m in re.finditer(r'[A-Za-z0-9+/]{20,}={0,2}', data.decode('latin-1')):\n        try:\n            decoded = base64.b64decode(m.group()).decode()\n            if 'picoCTF' in decoded:\n                print(decoded)\n                break\n        except:\n            pass"}
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

# Find the XMP metadata section
xmp_start = data.find(b'<?xpacket')
xmp_end = data.find(b'<?xpacket end')
if xmp_start == -1:
    xmp_end = len(data)
else:
    xmp_end = data.find(b'?>', xmp_end) + 2

xmp_data = data[xmp_start:xmp_end].decode('utf-8', errors='replace')

# Find the base64-encoded license resource
match = re.search(r'rdf:resource=\'([A-Za-z0-9+/=]+)\'', xmp_data)
if match:
    b64 = match.group(1)
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    # Fallback: search for base64-like strings in the file
    for m in re.finditer(r'[A-Za-z0-9+/]{20,}={0,2}', data.decode('latin-1')):
        try:
            decoded = base64.b64decode(m.group()).decode()
            if 'picoCTF' in decoded:
                print(decoded)
                break
        except:
            pass
```

## Replay Verification

```text
Verdict: verified
Reason: match
Gold checked: True
Exit code: 0
Duration: 707 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 8
Wall time: 68.29 s
Input tokens: 38547
Output tokens: 1309
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
