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
{"thought": "Let me start by examining the files in the challenge directory.", "action": "shell", "cmd": "ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt"}
```

### Tool: shell

```text
Command: ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt
Exit code: 0
Timed out: False
Duration: 544 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt
exit=0  time=0.54s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 21 03:54 .
drwxrwxr-x 4 root root   4096 Nov 21 03:54 ..
-r--r--r-- 1 root root 878136 Nov 16 14:02 cat.jpg
-r--r--r-- 1 root root     48 Nov 16 14:02 exiftool_download_url.txt
https://exiftool.org/Image-ExifTool-12.64.tar.gz
```

### Model call 2

Model output (self-reported):

```json
{"thought": "Let me examine the cat.jpg file - check its file type and look for hidden data. The challenge mentions files can be changed in a secret way, which suggests steganography or hidden data in the image.", "action": "shell", "cmd": "file ./challenge/cat.jpg && xxd ./challenge/cat.jpg | tail -20"}
```

### Tool: shell

```text
Command: file ./challenge/cat.jpg && xxd ./challenge/cat.jpg | tail -20
Exit code: 127
Timed out: False
Duration: 132 ms
```

Recorded observation excerpt:

```text
[step 2] shell: file ./challenge/cat.jpg && xxd ./challenge/cat.jpg | tail -20
exit=127  time=0.13s
--- output ---
[stderr]
/bin/sh: 1: file: not found

```

Recorded stderr excerpt:

```text
/bin/sh: 1: file: not found

```

### Model call 3

Model output (self-reported):

```json
{"thought": "file command not available. Let me use xxd or hexdump to inspect the file.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | tail -20"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | tail -20
Exit code: 0
Timed out: False
Duration: 1331 ms
```

Recorded observation excerpt:

```text
[step 3] shell: xxd ./challenge/cat.jpg | tail -20
exit=0  time=1.33s
--- output ---
000d6500: a98e 7f6e 68bf fc6f ff00 297f fd4c 5c2d  ...nh..o..)..L\-
000d6510: 32c0 5d82 ed22 00e3 8a2f ff00 1bff 00ca  2.]..".../......
000d6520: 5fff 0053 1cfe dcd1 7ff8 dffe 52ff 00fa  _..S........R...
000d6530: 9892 6993 c5c8 295c 417f 6de8 bffc 67ff  ..i...)\A.m...g.
000d6540: 0029 7ffd 4c73 fb6f 45ff 00e3 7ff9 4bfe  .)..Ls.oE.....K.
000d6550: 9124 de4e 15c1 73c4 21c6 d453 ff00 be7f  .$.N..s.!..S....
000d6560: f297 ff00 d4c7 3fb6 d46f fe37 ff00 94bf  ......?..o.7....
000d6570: e912 4de4 ee7f 2819 c748 81fe
```

### Model call 4

Model output (self-reported):

```json
{"thought": "The file ends with 0xffd9 which is the JPEG EOI marker. Let me check the beginning of the file and look for any hidden data. The challenge mentions files can be changed in a secret way - this could be steganography (LSB), or hidden data appended, or EXIF data manipulation. Let me check the header and look for strings.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | head -30"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | head -30
Exit code: 0
Timed out: False
Duration: 507 ms
```

Recorded observation excerpt:

```text
[step 4] shell: xxd ./challenge/cat.jpg | head -30
exit=0  time=0.51s
--- output ---
00000000: ffd8 ffe0 0010 4a46 4946 0001 0200 0001  ......JFIF......
00000010: 0001 0000 ffed 0030 5068 6f74 6f73 686f  .......0Photosho
00000020: 7020 332e 3000 3842 494d 0404 0000 0000  p 3.0.8BIM......
00000030: 0013 1c02 7400 0750 6963 6f43 5446 1c02  ....t..PicoCTF..
00000040: 0000 0200 0400 ffe1 0bf9 6874 7470 3a2f  ..........http:/
00000050: 2f6e 732e 6164 6f62 652e 636f 6d2f 7861  /ns.adobe.com/xa
00000060: 702f 312e 302f 003c 3f78 7061 636b 6574  p/1.0/.<?xpacket
00000070: 2062 6567 696e 3d27 efbb bf27
```

Recorded stderr excerpt:

```text
Exception ignored in: <_io.TextIOWrapper name='<stdout>' mode='w' encoding='utf-8'>
BrokenPipeError: [Errno 32] Broken pipe

```

### Model call 5

Model output (self-reported):

```json
{"thought": "I can see there's a base64-encoded string in the XMP metadata: 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'. Let me decode it.", "action": "python", "code": "import base64\ns = 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'\nprint(base64.b64decode(s).decode())"}
```

### Tool: python

```text
Command: 
Exit code: 0
Timed out: False
Duration: 407 ms
```

Recorded observation excerpt:

```text
[step 5] python
exit=0  time=0.41s
--- output ---
picoCTF{the_m3tadata_1s_modified}

```

### Model call 6

Model output (self-reported):

```json
{"thought": "I found the flag by decoding the base64 string in the XMP metadata of the JPEG file.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\ns = 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'\nprint(base64.b64decode(s).decode())"}
```

### Model call 7

Model output (self-reported):

```json
{"thought": "I need to write a script that actually reads the cat.jpg file and extracts the base64 string from the XMP metadata, then decodes it to get the flag.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\nimport re\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the XMP metadata section and look for base64-encoded strings\n# The XMP data is in the APP1 segment (ff e1)\n# Look for base64 patterns in the XMP section\nxmp_start = data.find(b'<?xpacket')\nxmp_end = data.find(b'<?xpacket end')\nif xmp_end == -1:\n    xmp_end = data.find(b'</x:xmpmeta>')\n\nxmp_data = data[xmp_start:xmp_end].decode('utf-8', errors='ignore')\n\n# Find base64-encoded strings (at least 20 chars of base64)\nmatches = re.findall(r'[A-Za-z0-9+/]{20,}={0,2}', xmp_data)\n\nfor m in matches:\n    try:\n        decoded = base64.b64decode(m).decode('utf-8')\n        if 'picoCTF' in decoded:\n            print(decoded)\n            break\n    except Exception:\n        pass"}
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

# Find the XMP metadata section and look for base64-encoded strings
# The XMP data is in the APP1 segment (ff e1)
# Look for base64 patterns in the XMP section
xmp_start = data.find(b'<?xpacket')
xmp_end = data.find(b'<?xpacket end')
if xmp_end == -1:
    xmp_end = data.find(b'</x:xmpmeta>')

xmp_data = data[xmp_start:xmp_end].decode('utf-8', errors='ignore')

# Find base64-encoded strings (at least 20 chars of base64)
matches = re.findall(r'[A-Za-z0-9+/]{20,}={0,2}', xmp_data)

for m in matches:
    try:
        decoded = base64.b64decode(m).decode('utf-8')
        if 'picoCTF' in decoded:
            print(decoded)
            break
    except Exception:
        pass
```

## Replay Verification

```text
Verdict: verified
Reason: match
Gold checked: True
Exit code: 0
Duration: 519 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 7
Wall time: 33.12 s
Input tokens: 15125
Output tokens: 828
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
